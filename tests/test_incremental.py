from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from analysis.build import build_analysis
from analysis.comparison import AnalysisRepository
from analysis.incremental import IncrementalAnalysisStore, update_analysis
from storage.foundation import ensure_foundation, iter_foundation
from storage.lease import LeaseBusyError
from storage.raw_writer import RawPacketWriter
from tests.test_analysis_pipeline import session_packet, motion_packet, lap_packet, telemetry_packet, history_packet, flashback_packet
from tests.test_foundation import write_packets, table_rows


class IncrementalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.raw = self.root / "raw_packets.bin"
        packets = [session_packet()]
        for frame, distance in enumerate(range(0, 101, 10), 1):
            packets += [motion_packet(frame, frame, distance), lap_packet(frame, frame, distance), telemetry_packet(frame, frame, distance, False)]
        packets += [history_packet(12), flashback_packet(13, 5)]
        for frame, distance in enumerate(range(50, 101, 10), 6):
            packets += [motion_packet(frame, frame + 10, distance), lap_packet(frame, frame + 10, distance), telemetry_packet(frame, frame + 10, distance, True)]
        packets += [history_packet(30)]
        write_packets(self.raw, packets)
        self.foundation = Path(ensure_foundation(self.raw)["database"])
        self.database = self.root / "incremental" / "telemetry_analysis.db"

    def tearDown(self):
        self.temp.cleanup()

    def test_many_batch_restart_matches_full_reference(self):
        done = False
        while not done:
            with IncrementalAnalysisStore(self.raw, self.database, distance_step_m=10) as store:
                result = store.update(final=True, batch_size=4)
                done = result["caught_up"]
        reference = Path(build_analysis(self.raw, output=self.root / "reference", distance_step_m=10, progress_every=0)["analysis_database"])
        for table in ("sessions", "laps", "lap_tyres", "telemetry_samples", "events", "lap_analysis", "resampled_lap_samples", "braking_events", "throttle_events", "gear_shift_events", "lap_metrics", "session_metrics", "lap_quality_details", "frame_quality_issues", "sample_origins", "quality_context_events"):
            self.assertEqual(table_rows(self.database, table), table_rows(reference, table), table)
        with IncrementalAnalysisStore(self.raw, self.database, distance_step_m=10) as store:
            self.assertTrue(store.update(final=True)["reused"])
        repo = AnalysisRepository(self.database)
        self.assertEqual(len(repo.segments("77")), 1)
        self.assertEqual(repo.quality("77")[0]["quality_status"], "ready")
        self.assertEqual(repo.quality("77", repo.segments("77")[0]["id"])[0]["lap_number"], 1)

    def test_failed_transaction_retries_without_duplicate_rows(self):
        with IncrementalAnalysisStore(self.raw, self.database) as store:
            first = store.update(batch_size=5)
            before = table_rows(self.database, "incremental_checkpoint")
            with patch.object(store, "_publish", side_effect=RuntimeError("injected storage failure")):
                with self.assertRaises(RuntimeError):
                    store.update(batch_size=5)
            self.assertEqual(before, table_rows(self.database, "incremental_checkpoint"))
            result = store.update(batch_size=5)
            self.assertEqual(result["total_raw_packets"], first["total_raw_packets"] + 5)

    def test_range_iterator_only_returns_new_packets(self):
        with closing(sqlite3.connect(self.foundation)) as con:
            all_packets = list(iter_foundation(con))
            tail = list(iter_foundation(con, all_packets[8].offset, all_packets[12].offset))
        self.assertEqual(tail, all_packets[9:13])

    def test_second_writer_is_rejected_but_live_snapshot_is_readable(self):
        with IncrementalAnalysisStore(self.raw, self.database) as store:
            store.update()
            with self.assertRaises(LeaseBusyError):
                IncrementalAnalysisStore(self.raw, self.database)
            result = update_analysis(self.raw, output=self.database.parent)
            self.assertTrue(result["reused"])

    def test_unchanged_entry_never_replays_packets(self):
        first = update_analysis(self.raw, output=self.database.parent)
        with patch("analysis.incremental.iter_foundation", side_effect=AssertionError("must not replay")):
            second = update_analysis(self.raw, output=self.database.parent)
        self.assertTrue(second["reused"])
        self.assertEqual(first["total_raw_packets"], second["total_raw_packets"])

    def test_full_export_never_overwrites_incremental_database(self):
        update_analysis(self.raw, output=self.database.parent)
        before = table_rows(self.database, "incremental_checkpoint")
        with self.assertRaises(ValueError):
            build_analysis(self.raw, output=self.database.parent, force_rebuild=True, export_files=True)
        self.assertEqual(before, table_rows(self.database, "incremental_checkpoint"))

    def test_completed_lap_is_published_live_then_flashback_withdraws_it(self):
        raw = self.root / "live.bin"
        packets = [session_packet()]
        for frame in range(1, 631):
            number = 1 if frame <= 330 else 2
            distance = (frame - 1) * 100 / 329 if number == 1 else (frame - 331) / 4
            lap = bytearray(lap_packet(frame, frame, distance))
            lap[29 + 31] = number
            packets += [motion_packet(frame, frame, distance), bytes(lap), telemetry_packet(frame, frame, distance, False)]
            if frame == 330:
                packets += [history_packet(frame)]
        prefix_count = len(packets)
        packets += [flashback_packet(700, 100)]
        write_packets(raw, packets)
        foundation = Path(ensure_foundation(raw)["database"])
        database = self.root / "live-analysis.db"
        with IncrementalAnalysisStore(raw, database, foundation) as store:
            first = store.update(final=False, batch_size=prefix_count)
            self.assertFalse(first["caught_up"])
            self.assertEqual(first["laps_resampled"], 1)
            self.assertEqual(len(AnalysisRepository(database).laps("77")), 1)
            second = store.update(final=False)
            self.assertEqual(second["laps_resampled"], 0)
            self.assertEqual(AnalysisRepository(database).laps("77"), ())
            final = store.update(final=True)
            self.assertEqual(final["status"], "complete")

    def test_lagging_foundation_never_flushes_away_partial_frames(self):
        raw = self.root / "lagging.bin"
        database = self.root / "lagging-analysis.db"
        with RawPacketWriter(raw, flush_every=1) as writer:
            for index, payload in enumerate((session_packet(), motion_packet(1, 1, 0), lap_packet(1, 1, 0))):
                writer.write(payload, index, index, "127.0.0.1", 20777)
            foundation = Path(ensure_foundation(raw)["database"])
            writer.write(telemetry_packet(1, 1, 0, False), 3, 3, "127.0.0.1", 20777)
            with IncrementalAnalysisStore(raw, database, foundation) as store:
                result = store.update(final=True)
                self.assertEqual(result["status"], "pending")
                self.assertFalse(result["source_complete"])
                self.assertEqual(len(store.writer.pending), 1)
            ensure_foundation(raw, foundation)
        with IncrementalAnalysisStore(raw, database, foundation) as store:
            result = store.update(final=True)
            self.assertEqual(result["status"], "complete")
        self.assertEqual(len(table_rows(database, "telemetry_samples")), 1)


if __name__ == "__main__":
    unittest.main()
