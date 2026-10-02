from contextlib import closing
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import tkinter as tk
import unittest
from unittest.mock import patch

from analysis.comparison import AnalysisRepository
from analysis.practice import practice_report, repeatability
from analysis.quality import inspect_source
from analysis.build import _FrameWriter
from analysis.schema import create_schema
from tests.gui_wait import ready_window as AnalysisWindow
from collector.audit_session import audit_session
from collector.packet_capture import PacketCapture
from collector.practice_gui import PracticeWindow
from collector.udp_receiver import ReceivedDatagram
from decoder.header import decode_header
from storage.foundation import ensure_foundation, foundation_path
from tests.test_analysis_pipeline import motion_packet, lap_packet, telemetry_packet, empty_packet
from analysis.player_decoder import decode_player_record
from tests.test_time_regions import make_database


def clone_lap(con, number):
    con.row_factory = sqlite3.Row
    for table in ("laps", "lap_metrics", "lap_analysis", "resampled_lap_samples", "telemetry_samples"):
        for row in con.execute(f"SELECT * FROM {table} WHERE lap_number=2").fetchall():
            fields = dict(row)
            fields["lap_number"] = number
            if table == "telemetry_samples":
                fields.pop("id")
                fields["frame_identifier"] += number * 1000
                fields["overall_frame_identifier"] += number * 1000
                fields["current_lap_time_ms"] = fields["lap_distance_m"] * (10 + number)
                fields["session_time"] = fields["current_lap_time_ms"] / 1000
            elif "lap_time_ms" in fields:
                fields["lap_time_ms"] = (1000 * (10 + number) if table != "resampled_lap_samples"
                                         else fields["distance_m"] * (10 + number))
            con.execute(f"INSERT INTO {table} ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})", tuple(fields.values()))


class PracticeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "analysis.db"
        make_database(self.db)
        with closing(sqlite3.connect(self.db)) as con:
            clone_lap(con, 3)
            con.commit()
        self.repo = AnalysisRepository(self.db)

    def tearDown(self):
        self.temp.cleanup()

    def edit(self, sql):
        with closing(sqlite3.connect(self.db)) as con:
            con.execute(sql)
            con.commit()

    def test_statistics_sectors_repetition_and_read_only(self):
        before = hashlib.sha256(self.db.read_bytes()).digest()
        report = practice_report(self.repo, "7", reference_lap=1)
        self.assertEqual(report["statistics"]["count"], 3)
        self.assertEqual(report["statistics"]["median_ms"], 11000)
        self.assertEqual(report["statistics"]["spread_ms"], 3000)
        self.assertEqual(report["sectors"][0]["count"], 3)
        self.assertEqual(report["candidate_laps"], [3, 2])
        self.assertTrue(all(row["repeated_loss"] for row in report["repeatability"]))
        self.assertEqual(before, hashlib.sha256(self.db.read_bytes()).digest())

    def test_invalid_pit_and_pending_excluded_with_reasons(self):
        self.edit("UPDATE laps SET lap_valid=0 WHERE lap_number=1")
        self.edit("UPDATE telemetry_samples SET driver_status=3 WHERE lap_number=2")
        self.edit("UPDATE lap_analysis SET quality_status='pending' WHERE lap_number=3")
        report = practice_report(self.repo, "7", reference_lap=1)
        self.assertEqual(report["statistics"]["count"], 0)
        self.assertIsNone(report["reference_lap"])
        self.assertFalse(report["repeatability"])
        self.assertIn("出入站/车库圈", report["excluded_reasons"])

    def test_superseded_bad_samples_not_used_and_bad_sector_unknown(self):
        self.edit("UPDATE laps SET sector2_valid=0 WHERE lap_number=2")
        self.edit("UPDATE telemetry_samples SET pit_status=1,superseded=1 WHERE lap_number=2 AND lap_distance_m=0")
        report = practice_report(self.repo, "7")
        row = next(r for r in report["rows"] if r["lap"] == 2)
        self.assertTrue(row["eligible"])
        self.assertIsNone(row["sector_ms"][1])
        self.assertEqual(report["sectors"][1]["count"], 2)

    def test_cached_values_not_presented_as_verified(self):
        self.edit("UPDATE telemetry_samples SET fuel_in_tank=15,ers_store_energy=2000000,tyre_inner_temp_rl=80,tyre_inner_temp_rr=80,tyre_inner_temp_fl=80,tyre_inner_temp_fr=80")
        report = practice_report(self.repo, "7")
        c = report["rows"][0]["conditions"]
        self.assertIsNone(c["fuel_start_kg"])
        self.assertIsNone(c["ers_median_mj"])
        self.assertIsNone(c["tyre_inner_median_c"])

    def test_writer_marks_same_frame_not_carried_status(self):
        con = sqlite3.connect(":memory:")
        try:
            create_schema(con)
            writer = _FrameWriter(con)
            for number in (1, 2):
                packets = [motion_packet(number, number, number*10), lap_packet(number, number, number*10), telemetry_packet(number, number, number*10, False)]
                if number == 1:
                    packets.append(bytes(empty_packet(7, number, number, number/10)))
                for payload in packets:
                    header = decode_header(payload)
                    writer.add(header, number, decode_player_record(payload, header), number)
            writer.flush_all()
            self.assertEqual(con.execute("SELECT status_present,telemetry_present FROM condition_frames ORDER BY overall_frame_identifier").fetchall(), [(1,1),(0,1)])
        finally:
            con.close()

    def test_verified_conditions_and_differences(self):
        with closing(sqlite3.connect(self.db)) as con:
            con.execute("INSERT INTO condition_frames SELECT session_uid,overall_frame_identifier,1,1 FROM telemetry_samples")
            con.execute("UPDATE telemetry_samples SET fuel_in_tank=lap_number*6-lap_distance_m*.001,ers_store_energy=lap_number*1000000,ers_deploy_mode=1,tyre_inner_temp_rl=80,tyre_inner_temp_rr=80,tyre_inner_temp_fl=80,tyre_inner_temp_fr=80")
            con.commit()
        pair = self.repo.comparison("7", 1, 2)
        self.assertEqual(pair.conditions[0]["fuel_start_kg"], 6)
        self.assertEqual(pair.conditions[0]["fuel_end_kg"], 5)
        self.assertEqual(pair.conditions[0]["tyre_inner_median_c"], 80)
        self.assertIn("起始采样燃油相差至少5 kg", pair.warnings)
        self.assertIn("已验证ERS储能中位值相差至少0.5 MJ", pair.warnings)

    def test_missing_legacy_evidence_table_stays_read_only(self):
        self.edit("DROP TABLE condition_frames")
        before = self.db.read_bytes()
        report = practice_report(AnalysisRepository(self.db), "7")
        self.assertIsNone(report["rows"][0]["conditions"]["fuel_start_kg"])
        self.assertEqual(before, self.db.read_bytes())

    def test_driving_segments_not_assumed_to_be_tyre_sets(self):
        with closing(sqlite3.connect(self.db)) as con:
            con.execute("CREATE TABLE lap_segments(session_uid TEXT,lap_number INTEGER,segment_id INTEGER)")
            con.executemany("INSERT INTO lap_segments VALUES ('7',?,?)", [(1,10),(2,10),(3,20)])
            con.commit()
        report = practice_report(AnalysisRepository(self.db), "7", reference_lap=1)
        self.assertEqual(report["candidate_laps"], [2])
        self.assertEqual(report["condition_excluded_laps"], [3])
        self.assertEqual(len(report["groups"]), 2)
        scoped = practice_report(AnalysisRepository(self.db), "7", 20)
        self.assertEqual([r["lap"] for r in scoped["rows"]], [3])

    def test_candidate_limit_and_cancel(self):
        with closing(sqlite3.connect(self.db)) as con:
            for number in range(4, 17):
                clone_lap(con, number)
            con.commit()
        report = practice_report(self.repo, "7", reference_lap=1)
        self.assertEqual(len(report["candidate_laps"]), 12)
        self.assertEqual(report["candidate_laps"][0], 16)
        with self.assertRaises(InterruptedError):
            practice_report(self.repo, "7", cancelled=lambda: True)

    def test_repetition_requires_coverage_not_one_accidental_lap(self):
        rows = [dict(lap_distance_m=d, current_lap_time_ms=d*10, speed_kph=200, throttle=1, brake=0) for d in range(0,301,10)]
        base = inspect_source(rows)
        loss = replace(base, times=tuple(t*1.1 for t in base.times))
        gap = replace(loss, bad_intervals=((0, 300),))
        report = repeatability({1:base,2:loss,3:gap,4:gap}, 1, [2,3,4], 300)
        self.assertFalse(any(r["repeated_loss"] for r in report))
        self.assertTrue(all(r["supported"] == 1 and r["candidate_count"] == 3 for r in report))

    def test_ui_review_tabs_and_safe_cancel(self):
        root = tk.Tk()
        root.withdraw()
        try:
            window = AnalysisWindow(root, Path(self.temp.name), self.db)
            report = practice_report(self.repo, "7", reference_lap=1)
            review = PracticeWindow(root, report, lambda:None)
            self.assertEqual(len(review.tabs.tabs()), 4)
            self.assertEqual(len(review.lap_table.get_children()), 3)
            entered = threading.Event()
            def job(cancel):
                entered.set()
                while not cancel():
                    threading.Event().wait(.01)
                raise InterruptedError()
            window._start_review("practice", job)
            self.assertTrue(entered.wait(2))
            window.prepare_close()
            window._worker.join(2)
            self.assertTrue(window.shutdown_ready())
        finally:
            for callback in root.tk.splitlist(root.tk.call("after", "info")):
                root.after_cancel(callback)
            root.destroy()


class AuditTests(unittest.TestCase):
    def make_capture(self, root):
        capture = PacketCapture(root, 20777)
        for index in range(3):
            payload = telemetry_packet(index+1, index+1, index*10, False)
            capture.process(ReceivedDatagram(1800000000000000000+index, index, "192.168.123.45", 20777, payload))
        capture.close("complete")
        ensure_foundation(capture.session_directory / "raw_packets.bin")
        return capture.session_directory

    def test_raw_header_index_cache_and_privacy(self):
        with tempfile.TemporaryDirectory() as temporary:
            session = self.make_capture(Path(temporary))
            raw = session / "raw_packets.bin"
            before = raw.read_bytes()
            report = audit_session(session)
            self.assertEqual(report["status"], "archive_checks_passed")
            self.assertEqual(report["raw_packets"], 3)
            self.assertEqual(report["header_roundtrip_mismatches"], 0)
            self.assertEqual(report["foundation_mismatches"], 0)
            text = json.dumps(report)
            self.assertNotIn("192.168.123.45", text)
            self.assertNotIn(temporary, text)
            self.assertEqual(before, raw.read_bytes())

    def test_stale_cache_and_index_mismatch_are_not_passed(self):
        with tempfile.TemporaryDirectory() as temporary:
            session = self.make_capture(Path(temporary))
            with closing(sqlite3.connect(foundation_path(session / "raw_packets.bin"))) as con:
                con.execute("DELETE FROM packets WHERE raw_offset=(SELECT MAX(raw_offset) FROM packets)")
                con.commit()
            with closing(sqlite3.connect(session / "telemetry.db")) as con:
                con.execute("UPDATE packets SET player_car_index=9 WHERE id=1")
                con.commit()
            report = audit_session(session)
            self.assertEqual(report["status"], "needs_review")
            self.assertEqual(report["foundation_mismatches"], 1)
            self.assertEqual(report["index_mismatches"], 1)

    def test_live_missing_and_truncated_archive(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaises(ValueError):
                audit_session(root)
            self.assertFalse((root / "telemetry.db").exists())
            capture = PacketCapture(root / "live", 20777)
            try:
                with self.assertRaises(ValueError):
                    audit_session(capture.session_directory)
            finally:
                capture.close("complete")
            session = self.make_capture(root / "stopped")
            raw = session / "raw_packets.bin"
            raw.write_bytes(raw.read_bytes()[:-1])
            report = audit_session(session)
            self.assertFalse(report["raw_crc_valid"])
            self.assertEqual(report["status"], "needs_review")


if __name__ == "__main__":
    unittest.main()
