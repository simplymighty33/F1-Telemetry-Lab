from __future__ import annotations

from contextlib import closing
from dataclasses import asdict
import hashlib
import logging
import os
from pathlib import Path
import sqlite3
import struct
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from analysis.build import build_analysis
from collector.foundation_worker import FoundationWorker
from decoder.full_parser import PACKET_SIZES, _read_schema, compiled_schema, decode_packet
from decoder.header import HEADER_SIZE, decode_header
from decoder.stream import PLAYER_SCHEMAS, decode_player_record
from storage.foundation import FoundationRepository, FoundationStore, ensure_foundation, foundation_path, iter_foundation, read_progress
from storage.raw_archive import ArchiveCursor, ArchiveReadError, FILE_HEADER, RawPacketWriter, iter_archive
from storage.lease import FileLease, LeaseBusyError, lease_active
from tests.test_analysis_pipeline import (
    empty_packet, flashback_packet, history_packet, lap_packet, motion_packet,
    session_packet, telemetry_packet,
)


def write_packets(raw: Path, packets: list[bytes], *, compression: str = "zlib") -> None:
    with RawPacketWriter(raw, flush_every=3, compression=compression) as writer:
        for index, payload in enumerate(packets):
            writer.write(payload, 1800000000000000000 + index, index, "127.0.0.1", 20777)


def table_rows(database: Path, table: str):
    with closing(sqlite3.connect(database)) as connection:
        return connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()


class FoundationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.raw = self.root / "raw_packets.bin"

    def tearDown(self):
        self.temporary.cleanup()

    def test_compiled_decoder_matches_fieldwise_reference_and_player_entries(self):
        for packet_id, schema in PLAYER_SCHEMAS.items():
            packet = empty_packet(packet_id, 1, 1, 0.1)
            offset = HEADER_SIZE
            reference = {}
            for name, fmt in schema:
                parser = struct.Struct("<" + fmt)
                values = parser.unpack_from(packet, offset)
                reference[name] = values[0] if len(values) == 1 else list(values)
                offset += parser.size
            self.assertEqual(_read_schema(packet, HEADER_SIZE, schema)[0], reference)
            self.assertEqual(decode_player_record(packet, decode_header(packet)), reference)
            complete = decode_packet(packet)
            expected = {key: complete[key] for key, _ in schema} if packet_id == 13 else {key: complete["cars"][0][key] for key, _ in schema}
            self.assertEqual(reference, expected)
        self.assertLessEqual(compiled_schema.cache_info().currsize, 128)

    def test_both_raw_versions_replay_identical_headers_and_channel_values(self):
        payloads = [bytes(empty_packet(packet_id, 1, 1, 0.1)) for packet_id in PACKET_SIZES]
        for compression in ("none", "zlib"):
            raw = self.root / f"{compression}.bin"
            write_packets(raw, payloads, compression=compression)
            summary = ensure_foundation(raw)
            with closing(sqlite3.connect(summary["database"])) as connection:
                decoded = list(iter_foundation(connection))
            self.assertEqual([asdict(item.header) for item in decoded], [asdict(decode_header(item)) for item in payloads])
            self.assertEqual(summary["errors"], 0)
            for item, payload in zip(decoded, payloads):
                expected = decode_packet(payload)
                if item.header.packet_id in PLAYER_SCHEMAS:
                    expected = decode_player_record(payload, item.header)
                else:
                    expected = {key: value for key, value in expected.items() if key not in {"header", "packet_name"}}
                self.assertEqual(item.body, expected)

    def test_missing_other_packet_types_never_discards_valid_channels(self):
        write_packets(self.raw, [telemetry_packet(1, 1, 10, False)])
        summary = ensure_foundation(self.raw)
        self.assertEqual(len(table_rows(Path(summary["database"]), "telemetry")), 1)
        self.assertEqual(len(table_rows(Path(summary["database"]), "motion")), 0)

    def test_channel_range_queries_preserve_source_and_separate_sessions_and_cars(self):
        payloads = [telemetry_packet(1, 1, 10, False), telemetry_packet(2, 2, 20, False)]
        other = bytearray(telemetry_packet(3, 3, 30, False))
        struct.pack_into("<Q", other, 7, 88)
        payloads.append(bytes(other))
        write_packets(self.raw, payloads)
        result = ensure_foundation(self.raw)
        repository = FoundationRepository(Path(result["database"]))
        rows = list(repository.channel(6, "77", 0, FILE_HEADER.size, self.raw.stat().st_size * 100))
        self.assertEqual([row["values"]["speed"] for row in rows], [110, 120])
        self.assertEqual(list(repository.channel(6, "77", 4, 0, 100000)), [])
        self.assertEqual(rows[0]["header"], decode_header(payloads[0]))

    def test_same_context_is_deduplicated_and_ai_history_is_not_expanded(self):
        ai = bytearray(history_packet(4))
        ai[HEADER_SIZE] = 5
        write_packets(self.raw, [session_packet(), session_packet(), bytes(ai), history_packet(5)])
        summary = ensure_foundation(self.raw)
        self.assertEqual(len(table_rows(Path(summary["database"]), "bodies")), 2)
        with closing(sqlite3.connect(summary["database"])) as connection:
            records = list(iter_foundation(connection))
        self.assertIsNone(records[2].body)
        self.assertEqual(summary["packets"], 4)

    def test_resume_after_append_matches_offline_replay(self):
        live_database = self.root / "live.db"
        writer = RawPacketWriter(self.raw, flush_every=1, compression="zlib")
        writer.write(session_packet(), 1, 1, "127.0.0.1", 20777)
        first = ensure_foundation(self.raw, live_database)
        writer.write(lap_packet(2, 2, 10), 2, 2, "127.0.0.1", 20777)
        writer.close()
        resumed = ensure_foundation(self.raw, live_database)
        rebuilt = ensure_foundation(self.raw, self.root / "offline.db")
        self.assertEqual(first["packets"], 1)
        self.assertEqual(resumed["packets"], 2)
        for table in ("packets", "lap", "bodies", "segments"):
            self.assertEqual(table_rows(Path(resumed["database"]), table), table_rows(Path(rebuilt["database"]), table))
        self.assertTrue(ensure_foundation(self.raw, live_database)["reused"])

    def test_uncommitted_raw_is_not_consumed(self):
        writer = RawPacketWriter(self.raw, flush_every=100, compression="zlib")
        writer.write(session_packet(), 1, 1, "127.0.0.1", 20777)
        writer.flush()
        first_limit = writer.persisted_file_bytes
        with ArchiveCursor(self.raw) as cursor:
            self.assertEqual(len(cursor.next_batch(first_limit)), 1)
            writer.write(lap_packet(2, 2, 10), 2, 2, "127.0.0.1", 20777)
            self.assertEqual(cursor.next_batch(first_limit), [])
            writer.flush()
            self.assertEqual(len(cursor.next_batch(writer.persisted_file_bytes)), 1)
        writer.close()

    def test_writer_lease_prevents_two_ingesters_and_releases_on_close(self):
        write_packets(self.raw, [session_packet()])
        with FoundationStore(self.raw) as store:
            self.assertTrue(lease_active(store.database.with_suffix(".lock")))
            with self.assertRaises(LeaseBusyError):
                FoundationStore(self.raw)
        self.assertFalse(lease_active(foundation_path(self.raw).with_suffix(".lock")))
        self.assertEqual(ensure_foundation(self.raw)["packets"], 1)

    def test_recording_analysis_reads_committed_snapshot_without_second_writer(self):
        write_packets(self.raw, [session_packet()])
        result = ensure_foundation(self.raw)
        with FileLease(self.root / "capture.lock"), FoundationStore(self.raw):
            summary = build_analysis(self.raw, self.root / "snapshot", progress_every=0)
        self.assertEqual(summary["total_raw_packets"], 1)

    def test_stale_recording_metadata_resumes_when_capture_lease_is_released(self):
        write_packets(self.raw, [session_packet()])
        (self.root / "metadata.json").write_text('{"summary":{"status":"recording"}}', encoding="utf-8")
        with FileLease(self.root / "capture.lock"):
            pass
        result = build_analysis(self.raw, self.root / "resumed", progress_every=0)
        self.assertEqual(result["total_raw_packets"], 1)

    def test_batch_failure_rolls_back_records_and_checkpoint_together(self):
        write_packets(self.raw, [lap_packet(1, 1, 10)])
        with FoundationStore(self.raw) as store, ArchiveCursor(self.raw) as cursor:
            batch = cursor.next_batch(self.raw.stat().st_size)
            with patch.object(store, "_lap_segment", side_effect=RuntimeError("injected failure")):
                with self.assertRaises(RuntimeError):
                    store.ingest(batch, cursor.checkpoint(), source_mtime_ns=self.raw.stat().st_mtime_ns)
            self.assertEqual(store.connection.execute("SELECT COUNT(*) FROM packets").fetchone()[0], 0)
            self.assertIsNone(read_progress(store.database))
        self.assertEqual(ensure_foundation(self.raw)["packets"], 1)

    def test_resume_rejects_modified_prefix_even_when_headers_remain_valid(self):
        write_packets(self.raw, [session_packet()], compression="none")
        ensure_foundation(self.raw)
        content = bytearray(self.raw.read_bytes())
        content[-1] ^= 1
        self.raw.write_bytes(content)
        with self.assertRaisesRegex(ArchiveReadError, "prefix changed"):
            ensure_foundation(self.raw, verify=True)

    def test_unchanged_bytes_with_new_mtime_are_verified_once_then_reused(self):
        write_packets(self.raw, [session_packet()])
        ensure_foundation(self.raw)
        stat = self.raw.stat()
        os.utime(self.raw, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1000000))
        self.assertFalse(ensure_foundation(self.raw)["reused"])
        self.assertTrue(ensure_foundation(self.raw)["reused"])

    def test_truncated_tail_requires_explicit_recovery_crc_is_never_ignored(self):
        write_packets(self.raw, [session_packet(), lap_packet(2, 2, 10)], compression="none")
        content = self.raw.read_bytes()
        self.raw.write_bytes(content[:-10])
        with self.assertRaises(ArchiveReadError):
            ensure_foundation(self.raw)
        result = ensure_foundation(self.raw, recover_tail=True)
        self.assertEqual(result["packets"], 1)
        self.assertIsNotNone(result["tail_error"])
        with self.assertRaises(ArchiveReadError):
            list(iter_archive(self.raw))
        self.raw.write_bytes(content[:-1] + bytes([content[-1] ^ 1]))
        with self.assertRaises(ArchiveReadError):
            ensure_foundation(self.raw, recover_tail=True)

    def test_bad_game_packet_is_audited_without_stopping_valid_packets(self):
        write_packets(self.raw, [b"short", telemetry_packet(2, 2, 10, False)])
        result = ensure_foundation(self.raw)
        self.assertEqual(result["errors"], 1)
        self.assertEqual(result["packets"], 2)
        self.assertEqual(len(table_rows(Path(result["database"]), "telemetry")), 1)

    def test_background_failure_never_prevents_raw_writer_from_continuing(self):
        writer = RawPacketWriter(self.raw, flush_every=1, compression="zlib")
        logger = logging.getLogger("expected-foundation-failure")
        with patch.object(logger, "exception"), patch.object(FoundationStore, "ingest", side_effect=sqlite3.OperationalError("injected disk error")):
            worker = FoundationWorker(self.raw, lambda: writer.persisted_file_bytes, logger)
            worker.start()
            writer.write(session_packet(), 1, 1, "127.0.0.1", 20777)
            deadline = time.monotonic() + 3
            while worker.snapshot().state != "error" and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(worker.snapshot().state, "error")
            for index in range(2, 12):
                writer.write(telemetry_packet(index, index, 10, False), index, index, "127.0.0.1", 20777)
            writer.close()
            worker.close()
        self.assertEqual(len(list(iter_archive(self.raw))), 11)
        self.assertEqual(ensure_foundation(self.raw)["packets"], 11)

    def test_explicit_prefix_analysis_is_marked_partial_and_strict_mode_still_fails(self):
        write_packets(self.raw, [session_packet(), lap_packet(2, 2, 10)], compression="none")
        self.raw.write_bytes(self.raw.read_bytes()[:-10])
        result = build_analysis(self.raw, self.root / "partial", recover_tail=True, progress_every=0)
        self.assertEqual(result["status"], "partial_verified_prefix")
        self.assertEqual(result["total_raw_packets"], 1)
        self.assertIsNotNone(result["tail_error"])
        with self.assertRaises(ArchiveReadError):
            build_analysis(self.raw, self.root / "partial", progress_every=0)

    def test_non_foundation_database_is_rejected_without_changing_its_bytes(self):
        write_packets(self.raw, [session_packet()])
        target = self.root / "unrelated.db"
        with closing(sqlite3.connect(target)) as connection:
            connection.execute("CREATE TABLE metadata(key TEXT,value TEXT)")
            connection.commit()
        before = target.read_bytes()
        from storage.foundation import FoundationError
        with self.assertRaises(FoundationError):
            ensure_foundation(self.raw, target)
        self.assertEqual(target.read_bytes(), before)

    def test_pit_lane_does_not_end_segment_garage_does_flashback_reopens(self):
        packets = []
        for frame, driver, pit in ((1, 0, 0), (2, 3, 0), (3, 1, 0), (4, 2, 2), (5, 0, 2)):
            payload = bytearray(lap_packet(frame, frame, frame * 10))
            payload[HEADER_SIZE + 42] = driver
            payload[HEADER_SIZE + 32] = pit
            packets.append(bytes(payload))
        write_packets(self.raw, packets)
        result = ensure_foundation(self.raw)
        rows = table_rows(Path(result["database"]), "segments")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][9:11], (1, "garage"))
        # Rebuild a different recording whose rewind crosses its garage return.
        raw = self.root / "rewind.bin"
        write_packets(raw, packets + [flashback_packet(6, 3)])
        result = ensure_foundation(raw)
        with closing(sqlite3.connect(result["database"])) as connection:
            current = connection.execute("SELECT end_reason FROM segments WHERE superseded=0").fetchall()
        self.assertEqual(current, [("open",)])

    def test_analysis_from_cache_equals_raw_reference_and_reuses_results(self):
        packets = [session_packet()]
        for frame, distance in enumerate(range(0, 101, 10), 1):
            packets += [motion_packet(frame, frame, distance), lap_packet(frame, frame, distance), telemetry_packet(frame, frame, distance, False)]
        packets.append(history_packet(12))
        write_packets(self.raw, packets)
        reference = build_analysis(self.raw, self.root / "reference", use_foundation=False, progress_every=0)
        cached = build_analysis(self.raw, self.root / "cached", progress_every=0)
        for table in ("sessions", "events", "laps", "telemetry_samples", "lap_analysis", "resampled_lap_samples", "braking_events", "throttle_events", "gear_shift_events", "lap_metrics", "session_metrics"):
            self.assertEqual(table_rows(Path(reference["analysis_database"]), table), table_rows(Path(cached["analysis_database"]), table), table)
        self.assertTrue(build_analysis(self.raw, self.root / "cached", progress_every=0)["reused"])
        self.assertFalse(build_analysis(self.raw, self.root / "cached", distance_step_m=10, progress_every=0)["reused"])

    def test_background_worker_catches_up_and_uses_same_tables_as_replay(self):
        writer = RawPacketWriter(self.raw, flush_every=1, compression="zlib")
        worker = FoundationWorker(self.raw, lambda: writer.persisted_file_bytes, logging.getLogger("foundation-test"))
        worker.start()
        for index, payload in enumerate([session_packet(), lap_packet(2, 2, 10), telemetry_packet(2, 2, 10, False)]):
            writer.write(payload, index, index, "127.0.0.1", 20777)
        writer.close()
        worker.close()
        self.assertEqual(worker.snapshot().packets, 3)
        reference = ensure_foundation(self.raw, self.root / "reference.db")
        for table in ("packets", "lap", "telemetry", "segments"):
            self.assertEqual(table_rows(foundation_path(self.raw), table), table_rows(Path(reference["database"]), table))

    def test_close_wakes_idle_worker_and_consumes_final_flushed_tail(self):
        idle = threading.Event()

        class ObservedStop(threading.Event):
            def wait(self, timeout=None):
                if timeout == 0.15:
                    idle.set()
                return super().wait(timeout)

        writer = RawPacketWriter(self.raw, flush_every=1, compression="zlib")
        worker = FoundationWorker(self.raw, lambda: writer.persisted_file_bytes, logging.getLogger("close-race-test"))
        worker._stop = ObservedStop()
        writer.write(session_packet(), 1, 1, "127.0.0.1", 20777)
        worker.start()
        self.assertTrue(idle.wait(3))
        writer.write(telemetry_packet(2, 2, 10, False), 2, 2, "127.0.0.1", 20777)
        writer.close()
        worker.close()
        self.assertEqual(worker.snapshot().packets, 2)

    def test_default_analysis_never_overwrites_old_analysis(self):
        old = self.root / "analysis"
        old.mkdir()
        database = old / "telemetry_analysis.db"
        database.write_bytes(b"old user analysis")
        write_packets(self.raw, [session_packet()])
        result = build_analysis(self.raw, progress_every=0)
        self.assertEqual(database.read_bytes(), b"old user analysis")
        self.assertIn("analysis_v0.10.0_reference", result["analysis_database"])


if __name__ == "__main__":
    unittest.main()
