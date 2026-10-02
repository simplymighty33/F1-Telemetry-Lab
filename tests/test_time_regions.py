from dataclasses import replace
from contextlib import closing
from pathlib import Path
import hashlib
import math
import sqlite3
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

from analysis.comparison import AnalysisRepository, TracePoint
from analysis.quality import inspect_source
from analysis.regions import time_regions
from analysis.resample import _source_rows, resample_laps
from analysis.schema import create_schema
from tests.gui_wait import ready_window as AnalysisWindow


def point(distance, delta=0, **changes):
    p = TracePoint(distance, distance * 10, distance * 10 + delta, delta,
                   200, 190, 1, 1, 0, 0, 4, 3)
    return replace(p, **changes)


def make_database(path):
    con = sqlite3.connect(path)
    create_schema(con)
    con.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                ("7", "Time Trial", 1, "Time Trial", 1, "Test", 0, 1000, 2, 1, 2))
    for number in (1, 2):
        lap_time = 10000 if number == 1 else 11000
        con.execute("INSERT INTO laps VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    ("7", number, lap_time, 3000, 3000, lap_time - 6000, 1, 1, 1, 1, 1))
        con.execute("INSERT INTO lap_metrics VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("7", number, lap_time, lap_time - 10000, 70, 10, 5, 0, 250, 80, 200,
                     1, 1, 2, 2, 0, 0, 0, 0.8, 1))
        con.execute("INSERT INTO lap_analysis VALUES (?,?,?,?,?,?,?,?,?)",
                    ("7", number, 101, 101, 0, 1000, 1, 10, "ready"))
        for index, distance in enumerate(range(0, 1001, 10)):
            elapsed = distance * (10 if number == 1 else 11)
            con.execute("""INSERT INTO resampled_lap_samples
                (session_uid,lap_number,distance_m,lap_time_ms,speed_kph,throttle,brake,steer,gear)
                VALUES (?,?,?,?,?,?,?,?,?)""", ("7", number, distance, elapsed, 200, 1, 0, 0, 4))
            values = dict(session_uid="7", overall_frame_identifier=number * 1000 + index,
                          frame_identifier=number * 1000 + index, received_at_ns=index,
                          session_time=elapsed / 1000, player_car_index=0, lap_number=number,
                          lap_distance_m=distance, total_distance_m=distance,
                          current_lap_time_ms=elapsed, sector=0, current_lap_invalid=0,
                          pit_status=0, driver_status=4, superseded=0,
                          speed_kph=200, throttle=1, brake=0, steer=0, gear=4)
            con.execute(f"INSERT INTO telemetry_samples ({','.join(values)}) VALUES ({','.join('?' for _ in values)})",
                        tuple(values.values()))
    con.commit()
    con.close()


class RegionAlgorithmTests(unittest.TestCase):
    def test_identical_laps_zero_and_whole_domain_retained(self):
        regions = time_regions(tuple(point(d) for d in range(0, 1001, 5)))
        self.assertEqual(len(regions), 1)
        self.assertEqual((regions[0].start_m, regions[0].end_m, regions[0].delta_ms), (0, 1000, 0))
        self.assertEqual(regions[0].kind, "小变化")

    def test_analytic_constant_speed_delta(self):
        trace = tuple(point(d, (d / 45 - d / 50) * 1000) for d in range(0, 1001, 5))
        regions = time_regions(trace)
        self.assertAlmostEqual(sum(r.delta_ms for r in regions), (1000 / 45 - 1000 / 50) * 1000)
        self.assertTrue(all(r.kind == "损失" for r in regions))

    def test_gains_losses_and_small_fragments_conserve_original_delta(self):
        trace = tuple(point(d, 100 * math.sin(d / 80) + d * 0.2) for d in range(0, 1001, 5))
        regions = time_regions(trace)
        self.assertTrue(any(r.kind == "获益" for r in regions))
        self.assertTrue(any(r.kind == "损失" for r in regions))
        self.assertEqual(regions[0].start_m, 0)
        self.assertEqual(regions[-1].end_m, 1000)
        self.assertTrue(all(a.end_m == b.start_m for a, b in zip(regions, regions[1:])))
        self.assertAlmostEqual(sum(r.delta_ms for r in regions), trace[-1].delta_ms - trace[0].delta_ms)

    def test_gap_is_not_bridged_or_allocated(self):
        trace = tuple(point(d, d, connected=d != 500) for d in range(0, 1001, 10))
        regions = time_regions(trace)
        self.assertFalse(any(r.start_m < 500 and r.end_m >= 500 for r in regions))
        self.assertEqual(sum(r.delta_ms for r in regions), 990)

    def test_invalid_points_are_not_used(self):
        trace = tuple(point(d, d, valid=not 400 <= d <= 600) for d in range(0, 1001, 10))
        regions = time_regions(trace)
        self.assertFalse(any(r.start_m < 600 and r.end_m > 400 for r in regions))
        self.assertEqual(sum(r.delta_ms for r in regions), 780)

    def test_operations_use_shared_bounds_and_real_threshold_crossings(self):
        speeds = (200, 180, 100, 120, 160)
        throttle = (1, 0, 0.2, 0.5, 1)
        brake = (0, 0.8, 0.6, 0, 0)
        trace = tuple(point(i * 25, i * 25, reference_speed_kph=speeds[i],
                            reference_throttle=throttle[i], reference_brake=brake[i]) for i in range(5))
        result = time_regions(trace)[0].reference
        self.assertEqual(result.minimum_speed_kph, 100)
        self.assertEqual(result.brake_start_m, 25)
        self.assertEqual(result.full_throttle_m, 100)
        self.assertEqual(result.minimum_gear, 4)

    def test_already_full_throttle_is_not_fake_pickup(self):
        region = time_regions(tuple(point(d, d) for d in range(0, 101, 10)))[0]
        self.assertIsNone(region.reference.full_throttle_m)
        self.assertIsNone(region.reference.brake_start_m)

    def test_empty_or_isolated_points_return_no_regions(self):
        self.assertEqual(time_regions(()), ())
        self.assertEqual(time_regions((point(0),)), ())
        self.assertEqual(time_regions((point(0, valid=False), point(5))), ())

    def test_short_supported_region_with_large_loss_is_not_called_small(self):
        regions = time_regions((point(0), point(5, 100), point(10, 200)))
        self.assertEqual(regions[0].kind, "损失")
        self.assertEqual(regions[0].delta_ms, 200)


class RegionRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "analysis.db"
        make_database(self.database)

    def tearDown(self):
        self.temp.cleanup()

    def change(self, sql, parameters=()):
        with closing(sqlite3.connect(self.database)) as con:
            con.execute(sql, parameters)
            con.commit()

    def test_complete_comparison_conserves_official_time_and_is_read_only(self):
        before = hashlib.sha256(self.database.read_bytes()).digest()
        comparison = AnalysisRepository(self.database).comparison("7", 1, 2)
        self.assertAlmostEqual(comparison.observed_delta_ms, 1000)
        self.assertAlmostEqual(comparison.unattributed_delta_ms, 0)
        self.assertIn("燃油条件未知/来源未核验", comparison.warnings)
        self.assertFalse(any("不可靠区间" in warning for warning in comparison.warnings))
        self.assertEqual(before, hashlib.sha256(self.database.read_bytes()).digest())

    def test_legacy_interpolation_cannot_hide_original_gap(self):
        self.change("DELETE FROM telemetry_samples WHERE lap_number=2 AND lap_distance_m>400 AND lap_distance_m<600")
        comparison = AnalysisRepository(self.database).comparison("7", 1, 2)
        self.assertTrue(comparison.warnings)
        self.assertEqual(comparison.observed_delta_ms, 800)
        self.assertEqual(comparison.unattributed_delta_ms, 200)
        self.assertFalse(any(r.start_m < 600 and r.end_m > 400 for r in comparison.regions))

    def test_partial_endpoints_keep_authoritative_residual(self):
        self.change("DELETE FROM resampled_lap_samples WHERE distance_m<100 OR distance_m>900")
        comparison = AnalysisRepository(self.database).comparison("7", 1, 2)
        self.assertEqual(comparison.observed_delta_ms, 800)
        self.assertEqual(comparison.unattributed_delta_ms, 200)

    def test_unknown_source_does_not_claim_reliable_analysis(self):
        self.change("DELETE FROM telemetry_samples")
        comparison = AnalysisRepository(self.database).comparison("7", 1, 2)
        self.assertEqual(comparison.regions, ())
        self.assertEqual(comparison.unattributed_delta_ms, 1000)

    def test_missing_channel_is_marked_and_chart_points_not_valid(self):
        self.change("UPDATE telemetry_samples SET speed_kph=NULL WHERE lap_number=2 AND lap_distance_m=500")
        comparison = AnalysisRepository(self.database).comparison("7", 1, 2)
        self.assertFalse(next(p.valid for p in comparison.trace if p.distance_m == 500))
        self.assertTrue(comparison.warnings)

    def test_new_normalization_rejects_large_gap(self):
        self.change("DELETE FROM telemetry_samples WHERE lap_number=2 AND lap_distance_m>400 AND lap_distance_m<600")
        with closing(sqlite3.connect(self.database)) as con:
            con.execute("DELETE FROM resampled_lap_samples")
            con.execute("DELETE FROM lap_analysis")
            result = resample_laps(con, distance_step_m=10)
            self.assertEqual(result["laps_resampled"], 1)
            self.assertEqual(con.execute("SELECT quality_status FROM lap_analysis WHERE lap_number=2").fetchone()[0], "sample_gap")

    def test_large_time_gap_and_missing_channel_are_rejected(self):
        with closing(sqlite3.connect(self.database)) as con:
            rows = _source_rows(con, "7", 1, 1000)
        for row in rows[1:]:
            row["current_lap_time_ms"] += 2000
        self.assertEqual(inspect_source(rows).status, "sample_gap")
        rows[1]["speed_kph"] = None
        self.assertEqual(inspect_source(rows).status, "missing_channels")

    def test_quality_thresholds_and_nonfinite_values(self):
        rows = [dict(lap_distance_m=0, current_lap_time_ms=0, speed_kph=100, throttle=0.5, brake=0),
                dict(lap_distance_m=50, current_lap_time_ms=1500, speed_kph=100, throttle=0.5, brake=0)]
        self.assertEqual(inspect_source(rows).status, "ready")
        rows[1]["lap_distance_m"] = 50.01
        self.assertEqual(inspect_source(rows).status, "sample_gap")
        rows[1]["speed_kph"] = float("nan")
        self.assertEqual(inspect_source(rows).status, "missing_channels")

    def test_small_clock_jitter_corrected_only_in_derived_view(self):
        self.change("UPDATE telemetry_samples SET lap_distance_m=10.5,current_lap_time_ms=80 WHERE lap_number=1 AND lap_distance_m=20")
        with closing(sqlite3.connect(self.database)) as con:
            rows = _source_rows(con, "7", 1, 1000)
            self.assertEqual(rows[2]["current_lap_time_ms"], 100)
            self.assertEqual(con.execute("SELECT current_lap_time_ms FROM telemetry_samples WHERE lap_number=1 AND lap_distance_m=10.5").fetchone()[0], 80)

    def test_true_clock_rollback_is_not_normalized(self):
        self.change("UPDATE telemetry_samples SET current_lap_time_ms=0 WHERE lap_number=1 AND lap_distance_m=500")
        with closing(sqlite3.connect(self.database)) as con:
            self.assertEqual(inspect_source(_source_rows(con, "7", 1, 1000)).status, "nonmonotonic_time")

    def test_discrete_gear_uses_actual_nearest_sample(self):
        self.change("UPDATE telemetry_samples SET gear=2 WHERE lap_number=1 AND lap_distance_m=0")
        self.change("UPDATE telemetry_samples SET gear=6 WHERE lap_number=1 AND lap_distance_m=10")
        with closing(sqlite3.connect(self.database)) as con:
            con.execute("DELETE FROM resampled_lap_samples")
            con.execute("DELETE FROM lap_analysis")
            resample_laps(con, distance_step_m=5)
            gear = con.execute("SELECT gear FROM resampled_lap_samples WHERE lap_number=1 AND distance_m=5").fetchone()[0]
            self.assertEqual(gear, 6)


class RegionGuiTests(unittest.TestCase):
    def test_chart_does_not_draw_a_line_across_source_gap(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "analysis.db"
            make_database(database)
            with closing(sqlite3.connect(database)) as con:
                con.execute("DELETE FROM telemetry_samples WHERE lap_number=2 AND lap_distance_m>400 AND lap_distance_m<600")
                con.commit()
            root = tk.Tk()
            root.withdraw()
            try:
                window = AnalysisWindow(root, Path(temporary), database)
                root.geometry("+30000+30000")
                root.deiconify()
                root.update()
                trace = window.comparison.trace
                left, right, _, _ = window.chart._geometry()
                start = left + (right - left) * 0.4
                end = left + (right - left) * 0.6
                lines = window.chart.find_withtag("series")
                self.assertTrue(lines)
                for line in lines:
                    xs = window.chart.coords(line)[::2]
                    self.assertFalse(min(xs) < end - 1e-6 and max(xs) > start + 1e-6)
            finally:
                for callback in root.tk.splitlist(root.tk.call("after", "info")):
                    root.after_cancel(callback)
                root.destroy()

    def test_selection_zoom_summary_and_clear(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "analysis.db"
            make_database(database)
            root = tk.Tk()
            root.withdraw()
            try:
                window = AnalysisWindow(root, Path(temporary), database)
                window.details_tabs.select(1)
                region = window.region_tree.get_children()[0]
                window.region_tree.selection_set(region)
                window._region_selected()
                self.assertIsNotNone(window.chart.focus_range)
                self.assertIn("最低速度", window.region_details.get("1.0", "end"))
                window._restore_full_lap()
                self.assertIsNone(window.chart.focus_range)
                self.assertIn("未归属差额", window.region_details.get("1.0", "end"))
                window._clear_comparison()
                self.assertEqual(window.region_tree.get_children(), ())
            finally:
                for callback in root.tk.splitlist(root.tk.call("after", "info")):
                    root.after_cancel(callback)
                root.destroy()

    def test_region_tab_fits_small_screen_high_dpi(self):
        for dpi in (96, 144, 192):
            with self.subTest(dpi=dpi):
                root = tk.Tk()
                root.withdraw()
                previous = root.tk.call("tk", "scaling")
                try:
                    root.tk.call("tk", "scaling", dpi / 72)
                    with patch.object(root, "winfo_screenwidth", return_value=1366), patch.object(root, "winfo_screenheight", return_value=768):
                        window = AnalysisWindow(root, Path("data"))
                    window.details_tabs.select(1)
                    root.geometry("+30000+30000")
                    root.deiconify()
                    root.update()
                    for widget in (window.region_tree, window.region_details, window.full_lap_button, window.status_label):
                        self.assertTrue(widget.winfo_ismapped())
                        bottom = widget.winfo_rooty() - root.winfo_rooty() + widget.winfo_height()
                        self.assertLessEqual(bottom, root.winfo_height())
                        self.assertGreater(widget.winfo_height(), 10)
                    self.assertGreater(window.chart.winfo_height(), 100)
                finally:
                    root.tk.call("tk", "scaling", previous)
                    for callback in root.tk.splitlist(root.tk.call("after", "info")):
                        root.after_cancel(callback)
                    root.destroy()


if __name__ == "__main__":
    unittest.main()
