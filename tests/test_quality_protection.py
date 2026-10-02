from contextlib import closing
import hashlib
import json
import logging
from pathlib import Path
import sqlite3
import struct
import tempfile
import threading
import time
import tkinter as tk
from dataclasses import replace
import unittest
from unittest.mock import patch

from analysis.build import build_analysis
from analysis.comparison import AnalysisRepository
from analysis.incremental import IncrementalAnalysisStore
from analysis.quality import inspect_source
from analysis.resample import resample_laps
from collector.foundation_worker import FoundationWorker
from collector.analysis_worker import AnalysisWorker
from collector.analysis_gui import AnalysisWindow
from collector.health import MIB, resource_decision
from collector.packet_capture import PacketCapture
from collector.pipeline import CapturePipeline, CaptureOverloadError
from collector.service import CollectorService
from collector.settings import load_settings
from collector.udp_receiver import ReceivedDatagram
from storage.foundation import ensure_foundation
from storage.raw_writer import RawPacketWriter, iter_archive
from tests.test_analysis_pipeline import session_packet, motion_packet, lap_packet, telemetry_packet, history_packet
from tests.test_foundation import write_packets, table_rows
from tests.test_time_regions import make_database


def cadence_rows(rate, extra=0, context=False):
    rows = []
    elapsed = 0
    for index in range(80):
        elapsed += 1000 / rate + (extra if index == 40 else 0)
        rows.append(dict(lap_distance_m=elapsed * 0.06, current_lap_time_ms=elapsed,
                         session_time=elapsed / 1000, received_at_ns=int(elapsed * 1000000),
                         overall_frame_identifier=index, speed_kph=216, throttle=1, brake=0,
                         pit_status=1 if context and index >= 40 else 0, driver_status=4))
    return rows


class QualityProtectionTests(unittest.TestCase):
    def test_adaptive_gap_at_20_30_60hz_is_warning_not_whole_lap_rejection(self):
        for rate in (20, 30, 60):
            with self.subTest(rate=rate):
                self.assertFalse(inspect_source(cadence_rows(rate)).issues)
                source = inspect_source(cadence_rows(rate, 250))
                self.assertEqual(source.status, "ready")
                self.assertEqual([i["kind"] for i in source.issues], ["cadence_gap"])
                left, right = source.bad_intervals[0]
                self.assertFalse(source.supports((left + right) / 2))
                self.assertFalse(source.connects(left, right))

    def test_wall_clock_pause_and_pit_transition_are_not_udp_gap_claims(self):
        rows = cadence_rows(60)
        for row in rows[40:]:
            row["received_at_ns"] += 5_000_000_000
        self.assertFalse(inspect_source(rows).issues)
        self.assertFalse(inspect_source(cadence_rows(60, 250, context=True)).issues)

    def test_jitter_does_not_trigger_gap_warning(self):
        rows = cadence_rows(60)
        for index, row in enumerate(rows):
            row["session_time"] += (index % 3) * 0.001
        self.assertFalse(inspect_source(rows).issues)

    def test_common_non_grid_endpoints_and_residual_components_are_read_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "analysis.db"
            make_database(database)
            with closing(sqlite3.connect(database)) as con:
                con.execute("UPDATE telemetry_samples SET lap_distance_m=lap_distance_m*.9986+.7")
                con.execute("DELETE FROM lap_analysis")
                con.execute("DELETE FROM resampled_lap_samples")
                resample_laps(con, distance_step_m=5)
                con.commit()
            before = hashlib.sha256(database.read_bytes()).digest()
            result = AnalysisRepository(database).comparison("7", 1, 2)
            self.assertAlmostEqual(result.trace[0].distance_m, .7)
            self.assertAlmostEqual(result.trace[-1].distance_m, 999.3)
            self.assertAlmostEqual(result.observed_delta_ms, 1000)
            self.assertAlmostEqual(sum(value for _, value in result.residual_components), result.unattributed_delta_ms)
            self.assertEqual(before, hashlib.sha256(database.read_bytes()).digest())
            self.assertTrue(result.warnings)  # Missing physical endpoints are still disclosed.

    def test_unreliable_internal_interval_net_difference_is_separate(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "analysis.db"
            make_database(database)
            with closing(sqlite3.connect(database)) as con:
                con.execute("DELETE FROM telemetry_samples WHERE lap_number=2 AND lap_distance_m>400 AND lap_distance_m<600")
                con.commit()
            result = AnalysisRepository(database).comparison("7", 1, 2)
            self.assertEqual(result.residual_components[1][1], 200)
            self.assertAlmostEqual(result.observed_delta_ms + sum(v for _, v in result.residual_components), 1000)

    def test_missing_each_required_packet_is_recorded_and_full_incremental_match(self):
        for missing_id in (0, 2, 6):
            with self.subTest(missing_id=missing_id), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                raw = root / "raw_packets.bin"
                packets = [session_packet()]
                for frame, distance in enumerate(range(0, 101, 10), 1):
                    lap = bytearray(lap_packet(frame, frame, distance))
                    struct.pack_into("<I", lap, 29 + 4, int(distance * 10))
                    candidates = {0: motion_packet(frame, frame, distance),
                                  2: bytes(lap),
                                  6: telemetry_packet(frame, frame, distance, False)}
                    packets += [payload for packet_id, payload in candidates.items() if not (frame == 6 and packet_id == missing_id)]
                packets.append(history_packet(20))
                write_packets(raw, packets)
                before = hashlib.sha256(raw.read_bytes()).digest()
                full = Path(build_analysis(raw, output=root / "full", distance_step_m=10, use_foundation=False, progress_every=0)["analysis_database"])
                ensure_foundation(raw)
                incremental = root / "incremental.db"
                done = False
                while not done:
                    with IncrementalAnalysisStore(raw, incremental, distance_step_m=10) as store:
                        done = store.update(final=True, batch_size=4)["caught_up"]
                for table in ("lap_quality_details", "frame_quality_issues", "sample_origins", "quality_context_events", "lap_metrics", "resampled_lap_samples"):
                    self.assertEqual(table_rows(full, table), table_rows(incremental, table), table)
                with closing(sqlite3.connect(full)) as con:
                    issue = con.execute("SELECT missing_parts_json FROM frame_quality_issues").fetchone()
                    self.assertEqual(json.loads(issue[0]), [missing_id])
                    details = json.loads(con.execute("SELECT details_json FROM lap_quality_details").fetchone()[0])
                    self.assertEqual(details["incomplete_frame_count"], 1)
                    self.assertIsNotNone(details["raw_offset_range"][0])
                    self.assertTrue(any(i["kind"] == "incomplete_frame" for i in details["issues"]))
                    self.assertFalse(con.execute("SELECT 1 FROM resampled_lap_samples WHERE distance_m=50").fetchone())
                self.assertEqual(before, hashlib.sha256(raw.read_bytes()).digest())

    def test_old_incremental_derived_results_upgrade_without_replay(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = root / "raw_packets.bin"
            packets = [session_packet()]
            for frame, distance in enumerate(range(0, 101, 10), 1):
                packets += [motion_packet(frame, frame, distance), lap_packet(frame, frame, distance), telemetry_packet(frame, frame, distance, False)]
            packets.append(history_packet(20))
            write_packets(raw, packets)
            ensure_foundation(raw)
            database = root / "incremental.db"
            with IncrementalAnalysisStore(raw, database) as store:
                store.update(final=True)
            with closing(sqlite3.connect(database)) as con:
                con.execute("DELETE FROM metadata WHERE key='quality_version'")
                for table in ("lap_quality_details", "sample_origins", "frame_quality_issues", "quality_context_events"):
                    con.execute(f"DROP TABLE {table}")
                con.commit()
            with patch("analysis.incremental.iter_foundation", side_effect=AssertionError("must not replay")):
                with IncrementalAnalysisStore(raw, database) as store:
                    result = store.update(final=True)
                    self.assertEqual(result["processed_this_update"], 0)
                    self.assertEqual(result["laps_updated"], 1)
                    self.assertEqual(len(table_rows(database, "lap_quality_details")), 1)


class ResourceProtectionTests(unittest.TestCase):
    def test_low_disk_safely_stops_service_and_keeps_window_state_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = replace(load_settings(root / "settings.json"), foundation_enabled=False, analysis_enabled=False)
            service = CollectorService(settings, root / "data", logging.getLogger("disk-test"), host="127.0.0.1", port=0)
            usage = type("Usage", (), {"free": 100 * MIB})()
            with patch("collector.service.shutil.disk_usage", return_value=usage):
                service.start()
                self.assertTrue(service.wait(5))
            self.assertEqual(service.snapshot().state, "error")
            self.assertTrue(service.snapshot().health_warnings)
            self.assertIn("磁盘", service.snapshot().error)
            self.assertFalse(service._health_thread.is_alive())
            raw = service.snapshot().session_directory / "raw_packets.bin"
            self.assertEqual(list(iter_archive(raw)), [])
            metadata = json.loads((raw.parent / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["summary"]["status"], "error")
            self.assertTrue(metadata["summary"]["resource_health"]["disk_stop"])

    def test_pressure_hysteresis_low_disk_and_lag(self):
        self.assertTrue(resource_decision(60, 100, 2048*MIB).pause_derived)
        self.assertTrue(resource_decision(30, 100, 2048*MIB, paused=True).pause_derived)
        self.assertFalse(resource_decision(24, 100, 2048*MIB, paused=True).pause_derived)
        self.assertTrue(resource_decision(0, 100, 512*MIB).pause_derived)
        self.assertFalse(resource_decision(0, 100, 512*MIB).stop_capture)
        self.assertTrue(resource_decision(0, 100, 100*MIB).stop_capture)
        self.assertTrue(resource_decision(0, 100, None).warnings)
        self.assertTrue(resource_decision(0, 100, 2048*MIB, analysis_lag=10000).warnings)

    def test_ui_history_is_coalesced_without_unbounded_backlog(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = CollectorService(load_settings(root / "settings.json"), root, logging.getLogger("test"))
            for index in range(10000):
                with service._lock:
                    service._latest_lap_update = index
                service._record_packet(index)
            self.assertEqual(service.drain_lap_updates(), [9999])
            self.assertEqual(service.drain_lap_updates(), [])
            self.assertLessEqual(len(service._recent_packet_times), 8192)

    def test_writer_overload_is_explicit_and_queued_raw_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            capture = PacketCapture(Path(temporary), 20777)
            pipeline = CapturePipeline(capture, capacity=2, put_timeout_seconds=.01)
            entered, release = threading.Event(), threading.Event()
            original = capture.process
            def slow(item):
                entered.set()
                release.wait(3)
                original(item)
            with patch.object(capture, "process", side_effect=slow):
                pipeline.start()
                pipeline.submit(ReceivedDatagram(1, 1, "127.0.0.1", 20777, b"first"))
                self.assertTrue(entered.wait(2))
                pipeline.submit(ReceivedDatagram(2, 2, "127.0.0.1", 20777, b"second"))
                pipeline.submit(ReceivedDatagram(3, 3, "127.0.0.1", 20777, b"third"))
                with self.assertRaises(CaptureOverloadError):
                    pipeline.submit(ReceivedDatagram(4, 4, "127.0.0.1", 20777, b"not_enqueued"))
                release.set()
                summary = pipeline.close("error")
            self.assertEqual(summary["software_drop_count"], 1)
            self.assertEqual([p.payload for p in iter_archive(capture.session_directory / "raw_packets.bin")], [b"first", b"second", b"third"])

    def test_paused_foundation_worker_preserves_raw_and_resumes_on_shutdown(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = root / "raw_packets.bin"
            with RawPacketWriter(raw, flush_every=1) as writer:
                writer.write(session_packet(), 1, 1, "127.0.0.1", 20777)
                worker = FoundationWorker(raw, lambda: writer.persisted_file_bytes, logging.getLogger("test"), pressure=lambda: True)
                worker.start()
                deadline = time.monotonic() + 3
                while worker.snapshot().state != "throttled" and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertEqual(worker.snapshot().state, "throttled")
                for index in range(2, 50):
                    writer.write(telemetry_packet(index, index, 0, False), index, index, "127.0.0.1", 20777)
            worker.close()
            self.assertEqual(len(list(iter_archive(raw))), 49)
            self.assertEqual(ensure_foundation(raw)["packets"], 49)

    def test_paused_analysis_worker_resumes_without_changing_raw(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = root / "raw_packets.bin"
            write_packets(raw, [session_packet(), history_packet(20)])
            ensure_foundation(raw)
            before = hashlib.sha256(raw.read_bytes()).digest()
            worker = AnalysisWorker(raw, logging.getLogger("test"), pressure=lambda: True)
            worker.start()
            deadline = time.monotonic() + 3
            while worker.snapshot().state != "throttled" and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertEqual(worker.snapshot().state, "throttled")
            worker.close()
            self.assertEqual(worker.snapshot().packets, 2)
            self.assertEqual(before, hashlib.sha256(raw.read_bytes()).digest())


class QualityGuiTests(unittest.TestCase):
    def test_quality_details_and_residual_breakdown_are_accessible(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "analysis.db"
            make_database(database)
            with closing(sqlite3.connect(database)) as con:
                con.execute("DELETE FROM lap_analysis")
                con.execute("DELETE FROM resampled_lap_samples")
                resample_laps(con)
                con.commit()
            root = tk.Tk()
            root.withdraw()
            try:
                window = AnalysisWindow(root, Path(temporary), database)
                self.assertIn("差额分解", window.region_details.get("1.0", "end"))
                window.quality_tree.selection_set("1")
                window._show_lap_quality()
                popup = next(widget for widget in root.winfo_children() if isinstance(widget, tk.Toplevel))
                text = next(widget for widget in popup.winfo_children() if isinstance(widget, tk.Text))
                content = text.get("1.0", "end")
                self.assertIn("Raw逻辑偏移", content)
                self.assertIn("游戏状态变化", content)
                self.assertNotIn(str(database), content)
                popup.destroy()
            finally:
                for callback in root.tk.splitlist(root.tk.call("after", "info")):
                    root.after_cancel(callback)
                root.destroy()


if __name__ == "__main__":
    unittest.main()
