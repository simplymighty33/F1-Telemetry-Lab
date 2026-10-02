from pathlib import Path
import json
import tempfile
import threading
import unittest
import logging
import socket
import time
import struct
import shutil
from contextlib import closing
import sqlite3
from unittest.mock import patch
from dataclasses import replace
from uuid import uuid4

from collector.session_capture import SessionCapture
from collector.udp_receiver import ReceivedDatagram
from decoder.header import HEADER_STRUCT, decode_header
from storage.raw_archive import iter_archive
from collector.inspect_session import inspect
from tests.test_analysis_pipeline import (session_packet, motion_packet, lap_packet, telemetry_packet,
                                         history_packet, flashback_packet)
from collector.pipeline import CapturePipeline
from collector.session_workers import SessionDerivedWorker
from analysis.incremental import update_analysis
from analysis.build import build_analysis
from storage.foundation import foundation_path, read_progress
from storage.lease import lease_active
from collector.service import CollectorService
from collector.settings import load_settings


def datagram(uid, sequence):
    payload = HEADER_STRUCT.pack(2023, 23, 1, 0, 1, 6, uid,
                                 sequence / 60, sequence, sequence, 0, 255) + b"fixture"
    return ReceivedDatagram(1_800_000_000_000_000_000 + sequence,
                           2_000_000_000 + sequence, "127.0.0.1", 20777, payload)


class SessionArchiveTests(unittest.TestCase):
    def test_background_shutdown_fault_is_visible_and_does_not_lose_raw(self):
        from collector.foundation_worker import FoundationWorker
        original_close = FoundationWorker.close
        def faulty_close(worker):
            original_close(worker)
            raise RuntimeError('derived shutdown fault')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = load_settings(root/'settings.json')
            logger = logging.getLogger('derived-shutdown-fault')
            logger.addHandler(logging.NullHandler())
            logger.propagate = False
            service = CollectorService(settings, root/'data', logger, '127.0.0.1', 0)
            service.start()
            try:
                deadline = time.monotonic()+3
                while service.snapshot().state == 'starting' and time.monotonic()<deadline:
                    time.sleep(.01)
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                    sender.sendto(session_packet(), ('127.0.0.1',service.snapshot().udp_port))
                deadline = time.monotonic()+3
                while service.snapshot().foundation_state != 'ready' and time.monotonic()<deadline:
                    time.sleep(.02)
                self.assertEqual(service.snapshot().foundation_state, 'ready')
                with patch.object(FoundationWorker, 'close', faulty_close):
                    service.stop()
                    self.assertTrue(service.wait(5))
                self.assertIn('derived shutdown fault', service.snapshot().foundation_error or '')
                self.assertEqual(service.snapshot().persisted_packets, 1)
            finally:
                service.stop()
                service.wait(5)

    def test_snapshot_does_not_wait_for_slow_raw_write(self):
        from storage.raw_archive import RawPacketWriter
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = replace(load_settings(root/'settings.json'), foundation_enabled=False, analysis_enabled=False)
            service = CollectorService(settings, root/'data', logging.getLogger('slow-write'), '127.0.0.1', 0)
            entered, release = threading.Event(), threading.Event()
            original_write = RawPacketWriter.write
            def slow_write(writer, *args, **kwargs):
                entered.set()
                release.wait(2)
                return original_write(writer, *args, **kwargs)
            with patch.object(RawPacketWriter, 'write', slow_write):
                service.start()
                try:
                    deadline = time.monotonic()+3
                    while service.snapshot().state == 'starting' and time.monotonic()<deadline:
                        time.sleep(.01)
                    port = service.snapshot().udp_port
                    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                        sender.sendto(session_packet(), ('127.0.0.1',port))
                    self.assertTrue(entered.wait(2))
                    timer = threading.Timer(.5, release.set)
                    timer.start()
                    start = time.monotonic()
                    service.snapshot()
                    elapsed = time.monotonic()-start
                    timer.join()
                    self.assertLess(elapsed, .2)
                finally:
                    release.set()
                    service.stop()
                    self.assertTrue(service.wait(5))

    def test_changed_archive_identity_cannot_reuse_another_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            capture = SessionCapture(root, 20777)
            capture.process(replace(datagram(77, 1), payload=session_packet()))
            capture.close()
            directory = capture.session_directory
            update_analysis(directory)
            metadata_file = directory / "metadata.json"
            metadata = json.loads(metadata_file.read_text("utf-8"))
            metadata["archive_identity"] = str(uuid4())
            metadata_file.write_text(json.dumps(metadata), "utf-8")
            before = (directory / "raw_packets.bin").read_bytes()
            with self.assertRaisesRegex(ValueError, "档案身份改变"):
                update_analysis(directory)
            self.assertEqual((directory / "raw_packets.bin").read_bytes(), before)

    def test_new_archive_can_be_copied_and_resume_offline_analysis(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            capture = SessionCapture(root / "original", 20777)
            fixture = replace(datagram(77, 1), payload=session_packet())
            capture.process(fixture)
            capture.close()
            original = capture.session_directory
            update_analysis(original)
            moved = root / "copied" / original.name
            shutil.copytree(original, moved)
            result = update_analysis(moved)
            self.assertEqual(result["total_raw_packets"], 1)
            self.assertEqual((moved / "raw_packets.bin").read_bytes(), (original / "raw_packets.bin").read_bytes())

    def test_parked_session_remains_marked_live_until_capture_stops(self):
        with tempfile.TemporaryDirectory() as temporary:
            capture = SessionCapture(Path(temporary), 20777)
            capture.process(datagram(77, 1))
            first = capture.session_directory
            capture.process(datagram(88, 2))
            try:
                self.assertTrue(lease_active(first / "capture.lock"))
            finally:
                capture.close()
            self.assertFalse(lease_active(first / "capture.lock"))

    def test_unassigned_packets_are_preserved_without_guessing_session(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            capture = SessionCapture(root, 20777)
            first = datagram(77, 1)
            bad = replace(first, payload=b"short")
            capture.process(bad)
            capture.process(first)
            capture.process(replace(bad, monotonic_ns=bad.monotonic_ns + 2))
            capture.close()
            archives = list(root.glob("*/raw_packets.bin"))
            self.assertEqual(len(archives), 2)
            counts = {}
            for archive in archives:
                metadata = json.loads((archive.parent / "metadata.json").read_text("utf-8"))
                counts[metadata["game_session_uid"]] = len(list(iter_archive(archive)))
                self.assertTrue(inspect(archive.parent, verify_raw=True)["raw_matches_index"])
            self.assertEqual(counts, {None: 2, "77": 1})

    def test_same_uid_different_protocol_formats_do_not_mix(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            capture = SessionCapture(root, 20777)
            first = datagram(77, 1)
            second = bytearray(first.payload)
            struct.pack_into("<H", second, 0, 2024)
            second[2] = 24
            capture.process(first)
            capture.process(replace(first, payload=bytes(second)))
            capture.close()
            formats = [decode_header(next(iter_archive(raw)).payload).packet_format
                       for raw in root.glob("*/raw_packets.bin")]
            self.assertEqual(sorted(formats), [2023, 2024])

    def test_repeated_recording_same_uid_and_timestamp_never_overwrites(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directories = []
            for sequence in (1, 2):
                capture = SessionCapture(root, 20777)
                capture.process(datagram(77, sequence))
                capture.close()
                directories.append(capture.session_directory)
            self.assertNotEqual(*directories)
            self.assertEqual([next(iter_archive(d / "raw_packets.bin")).payload for d in directories],
                             [datagram(77, 1).payload, datagram(77, 2).payload])

    def test_display_decode_failure_does_not_stop_udp_or_raw_routing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = replace(load_settings(root / "settings.json"), foundation_enabled=False)
            logger = logging.getLogger("display-fault-uid")
            logger.addHandler(logging.NullHandler())
            logger.propagate = False
            service = CollectorService(settings, root / "data", logger, "127.0.0.1", 0)
            service.start()
            try:
                deadline = time.monotonic() + 3
                while service.snapshot().state == "starting" and time.monotonic() < deadline:
                    time.sleep(0.01)
                port = service.snapshot().udp_port
                with patch("collector.service.decode_packet", side_effect=RuntimeError("display fault")):
                    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                        for _ in range(4):
                            sender.sendto(session_packet(), ("127.0.0.1", port))
                    deadline = time.monotonic() + 3
                    while service.snapshot().received_packets < 4 and not service.completed.is_set() and time.monotonic() < deadline:
                        time.sleep(0.01)
            finally:
                service.stop()
                self.assertTrue(service.wait(5))
            self.assertEqual(service.snapshot().state, "stopped")
            self.assertEqual(service.snapshot().persisted_packets, 4)
            self.assertIn("显示", service.snapshot().display_error)

    def test_naming_decoder_failure_cannot_stop_following_raw_packets(self):
        with tempfile.TemporaryDirectory() as temporary:
            capture = SessionCapture(Path(temporary), 20777)
            first = datagram(77, 1)
            context = replace(first, payload=session_packet())
            try:
                with patch("collector.session_capture.decode_packet", side_effect=RuntimeError("decoder fault")):
                    capture.process(context)
                capture.process(first)
            finally:
                capture.close()
            records = list(iter_archive(capture.session_directory / "raw_packets.bin"))
            self.assertEqual([r.payload for r in records], [context.payload, first.payload])

    def test_per_uid_background_and_offline_replay_keep_flashback_results(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            capture = SessionCapture(root, 20777, raw_flush_every=1)
            pipeline = CapturePipeline(capture)
            worker = SessionDerivedWorker(capture, logging.getLogger("session-workers"))
            pipeline.start()
            worker.start()
            expected = {}
            sequence = 0
            try:
                for uid in (77, 88):
                    packets = [session_packet()]
                    for frame, distance in enumerate(range(0, 101, 10), 1):
                        packets += [motion_packet(frame, frame, distance), lap_packet(frame, frame, distance),
                                    telemetry_packet(frame, frame, distance, False)]
                    packets += [flashback_packet(12, 5)]
                    for frame, distance in enumerate(range(50, 101, 10), 6):
                        packets += [motion_packet(frame, frame + 7, distance), lap_packet(frame, frame + 7, distance),
                                    telemetry_packet(frame, frame + 7, distance, True)]
                    packets += [history_packet(20)]
                    expected[uid] = []
                    for payload in packets:
                        payload = bytearray(payload)
                        struct.pack_into("<Q", payload, 7, uid)
                        payload = bytes(payload)
                        expected[uid].append(payload)
                        sequence += 1
                        pipeline.submit(ReceivedDatagram(1_800_000_000_000_000_000 + sequence,
                                                        2_000_000_000 + sequence, "127.0.0.1", 20777, payload))
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        directory = capture.directory_for(uid, 2023)
                        if directory and worker.snapshot().packets >= len(packets):
                            break
                        time.sleep(0.02)
                    self.assertIsNotNone(directory)
                    progress = read_progress(foundation_path(directory / "raw_packets.bin"))
                    self.assertIsNotNone(progress)
                    self.assertEqual(progress["packets"], len(packets))
            finally:
                pipeline.close()
                worker.close()
            for uid, payloads in expected.items():
                directory = capture.directory_for(uid, 2023)
                raw = directory / "raw_packets.bin"
                records = list(iter_archive(raw))
                self.assertEqual([r.payload for r in records], payloads)
                self.assertEqual([decode_header(r.payload) for r in records], [decode_header(p) for p in payloads])
                live = update_analysis(raw)
                replay = build_analysis(raw, output=directory / "reference", use_foundation=False, progress_every=0)
                self.assertEqual(live["flashback_count"], replay["flashback_count"])
                self.assertEqual(live["laps_resampled"], replay["laps_resampled"])
                self.assertEqual(replay["flashback_count"], 1)
                self.assertEqual(replay["laps_resampled"], 1)

    def test_live_service_rotates_uid_without_rebinding_udp(self):
        with tempfile.TemporaryDirectory() as temporary, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            root = Path(temporary)
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
            probe.close()
            settings = replace(load_settings(root / "settings.json"), foundation_enabled=False)
            service = CollectorService(settings, root / "data", logging.getLogger("uid-test"), "127.0.0.1", port)
            service.start()
            try:
                deadline = time.monotonic() + 3
                while service.snapshot().state == "starting" and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertEqual(service.snapshot().state, "listening")
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                    for uid in (77, 88, 77, 88):
                        sender.sendto(datagram(uid, uid).payload, ("127.0.0.1", port))
                deadline = time.monotonic() + 3
                while service.snapshot().received_packets < 4 and time.monotonic() < deadline:
                    time.sleep(0.01)
            finally:
                service.stop()
                self.assertTrue(service.wait(5))
            self.assertEqual(service.snapshot().state, "stopped")
            self.assertEqual(service.snapshot().persisted_packets, 4)
            self.assertEqual(len(list((root / "data").glob("*/raw_packets.bin"))), 2)

    def test_delayed_session_context_labels_folder_without_losing_prefix(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            capture = SessionCapture(root, 20777)
            prefix = datagram(77, 1)
            capture.process(prefix)
            context = bytearray(session_packet())
            context[35] = 2  # independently specified Practice 2, Melbourne
            context = ReceivedDatagram(prefix.received_at_ns + 1, prefix.monotonic_ns + 1,
                                       "127.0.0.1", 20777, bytes(context))
            capture.process(context)
            directory = capture.session_directory
            capture.close()
            self.assertIn("澳大利亚_练习赛2", directory.name)
            self.assertIn("UID-000000000000004D", directory.name)
            report = inspect(directory, verify_raw=True)
            self.assertEqual(report["raw_packet_count"], 2)
            self.assertTrue(report["raw_matches_index"])
            with closing(sqlite3.connect(directory / "telemetry.db")) as database:
                self.assertEqual(database.execute("SELECT track,session_type FROM sessions").fetchone(),
                                 ("Melbourne", "Practice 2"))
            self.assertEqual([r.payload for r in iter_archive(directory / "raw_packets.bin")],
                             [prefix.payload, context.payload])

    def test_different_uids_and_late_packets_have_separate_lossless_archives(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            capture = SessionCapture(root, 20777)
            packets = [datagram(77, 1), datagram(88, 2), datagram(77, 3), datagram(88, 4)]
            for packet in packets:
                capture.process(packet)
            summary = capture.close()
            archives = list(root.glob("*/raw_packets.bin"))
            self.assertEqual(len(archives), 2)
            recovered = {}
            for archive in archives:
                records = list(iter_archive(archive))
                uid = decode_header(records[0].payload).session_uid
                self.assertTrue(all(decode_header(item.payload).session_uid == uid for item in records))
                recovered[uid] = [item.payload for item in records]
                metadata = json.loads((archive.parent / "metadata.json").read_text("utf-8"))
                self.assertEqual(metadata["game_session_uid"], str(uid))
            self.assertEqual(recovered[77], [packets[0].payload, packets[2].payload])
            self.assertEqual(recovered[88], [packets[1].payload, packets[3].payload])
            self.assertEqual(summary["packet_count"], 4)
