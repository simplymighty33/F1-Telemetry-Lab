"""User-visible layout and refresh contracts for the desktop UI upgrade."""
from pathlib import Path
import logging
from contextlib import closing
import sqlite3
import gc
import tempfile
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from analysis.comparison import AnalysisRepository
from analysis.practice import practice_report
from collector.practice_gui import PracticeWindow
from collector.practice_gui import show_audit
from collector.gui import CollectorWindow
from collector.analysis_gui import AnalysisWindow
from tests.test_time_regions import make_database
from tests.gui_wait import ready_window, wait_for_tasks


def close_root(root):
    for callback in root.tk.splitlist(root.tk.call("after", "info")):
        root.after_cancel(callback)
    root.destroy()


class UiCase(unittest.TestCase):
    def tearDown(self):
        # Finalize dead Tk cycles on the UI thread before another test starts
        # a worker that may otherwise trigger Python's cyclic collector.
        gc.collect()


class PracticePresentationTests(UiCase):
    def test_audit_opens_readable_summary_before_raw_json(self):
        from tkinter import ttk
        root = tk.Tk()
        root.withdraw()
        try:
            audit = show_audit(root, {"status": "needs_review", "raw_crc_valid": False,
                "raw_packets": 3, "header_roundtrip_mismatches": 0, "warnings": ["尾部待核验"]})
            tabs = next(w for w in audit.winfo_children() if isinstance(w, ttk.Notebook))
            self.assertEqual([tabs.tab(i, "text") for i in tabs.tabs()], ["检查结果", "原始诊断明细"])
            self.assertEqual(tabs.index(tabs.select()), 0)
        finally:
            close_root(root)

    def test_practice_uses_dark_readable_controls(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "analysis.db"
            make_database(database)
            root = tk.Tk()
            root.withdraw()
            try:
                window = PracticeWindow(root, practice_report(AnalysisRepository(database), "7"), lambda: None)
                self.assertEqual(window.root.cget("background"), "#10141c")
                self.assertEqual(window.lap_table.cget("style"), "Metrics.Treeview")
            finally:
                close_root(root)

    def test_refresh_replaces_snapshot_and_preserves_selected_lap_and_tab(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "analysis.db"
            make_database(database)
            report = practice_report(AnalysisRepository(database), "7")
            root = tk.Tk()
            root.withdraw()
            try:
                refreshed = []
                window = PracticeWindow(root, report, lambda: None, on_refresh=lambda target: refreshed.append(target))
                window.tabs.select(1)
                window.lap_table.selection_set(window.lap_table.get_children()[0])
                before = window.lap_table.item(window.lap_table.selection()[0], "values")[0]
                window.refresh_button.invoke()
                self.assertEqual(refreshed, [window])
                window.set_report(report)
                self.assertEqual(window.tabs.index(window.tabs.select()), 1)
                self.assertEqual(window.lap_table.item(window.lap_table.selection()[0], "values")[0], before)
                self.assertIn("更新于", window.snapshot_var.get())
            finally:
                close_root(root)


class CapturePresentationTests(UiCase):
    def test_connection_and_recording_details_do_not_crowd_lap_table(self):
        root = tk.Tk()
        root.withdraw()
        try:
            with patch.object(root, "after"):
                window = CollectorWindow(root, Mock(), logging.getLogger("ui-upgrade"), Path("data"), Path("settings.json"))
            root.geometry("960x650+30000+30000")
            root.deiconify()
            root.update()
            self.assertFalse(window.port_entry.winfo_ismapped())
            self.assertFalse(window.details.winfo_ismapped())
            self.assertTrue(window.error_label.winfo_ismapped())
            window.port_button.invoke()
            root.update()
            self.assertTrue(window.port_entry.winfo_ismapped())
            window.details_button.invoke()
            root.update()
            self.assertTrue(window.details.winfo_ismapped())
        finally:
            close_root(root)


class AnalysisPresentationTests(UiCase):
    def test_loaded_laps_leave_usable_chart_at_small_screen_scalings(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "analysis.db"
            make_database(database)
            for dpi in (96, 120, 144, 192):
                with self.subTest(dpi=dpi):
                    root = tk.Tk()
                    root.withdraw()
                    previous = root.tk.call("tk", "scaling")
                    try:
                        root.tk.call("tk", "scaling", dpi / 72)
                        with patch.object(root, "winfo_screenwidth", return_value=1366), patch.object(root, "winfo_screenheight", return_value=768):
                            window = ready_window(root, Path(directory), database)
                        root.geometry("+30000+30000")
                        root.deiconify()
                        root.update()
                        self.assertGreater(window.chart.winfo_height(), 150)
                        self.assertTrue(window.details_button.winfo_ismapped())
                        self.assertLessEqual(window.status_label.winfo_rooty() - root.winfo_rooty()
                                             + window.status_label.winfo_height(), root.winfo_height())
                    finally:
                        root.tk.call("tk", "scaling", previous)
                        close_root(root)
                        gc.collect()

    def test_refresh_stays_bound_to_original_source_and_closed_view_is_not_reopened(self):
        with tempfile.TemporaryDirectory() as directory:
            original, other = (Path(directory) / name for name in ("original.db", "other.db"))
            make_database(original)
            make_database(other)
            root = tk.Tk()
            root.withdraw()
            try:
                window = ready_window(root, Path(directory), original)
                window.choose_practice()
                wait_for_tasks(window)
                review = window._practice_windows[0]
                window.load_database(other)
                wait_for_tasks(window)
                with closing(sqlite3.connect(original)) as con:
                    con.execute("UPDATE laps SET lap_time_ms=13000 WHERE lap_number=2")
                    con.commit()
                review.refresh_button.invoke()
                wait_for_tasks(window)
                self.assertEqual(next(r for r in review.report['rows'] if r['lap'] == 2)['time_ms'], 13000)
                self.assertEqual(window.repository.database, other.resolve())
                review.refresh_button.invoke()
                review.root.destroy()
                wait_for_tasks(window)
                self.assertEqual(window._practice_windows, [])
            finally:
                close_root(root)

    def test_comparison_traces_have_non_color_identity_and_keyboard_values(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "analysis.db"
            make_database(database)
            root = tk.Tk()
            root.withdraw()
            try:
                window = ready_window(root, Path(directory), database)
                root.geometry("960x650+30000+30000")
                root.deiconify()
                root.update()
                chart = window.chart
                blue = [i for i in chart.find_withtag("series") if chart.itemcget(i, "fill") == "#49b8ff"]
                self.assertTrue(blue)
                self.assertTrue(all(chart.itemcget(i, "dash") == "7 4" for i in blue))
                chart.focus_force()
                chart.event_generate("<Right>")
                root.update()
                self.assertIn("油门", chart.hover_var.get())
                self.assertIn("刹车", chart.hover_var.get())
            finally:
                close_root(root)

    def test_practice_refresh_reads_a_new_snapshot_in_same_window(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "analysis.db"
            make_database(database)
            root = tk.Tk()
            root.withdraw()
            try:
                window = ready_window(root, Path(directory), database)
                window.choose_practice()
                wait_for_tasks(window)
                review = window._practice_windows[0]
                with closing(sqlite3.connect(database)) as con:
                    con.execute("UPDATE laps SET lap_time_ms=12000 WHERE lap_number=2")
                    con.commit()
                review.refresh_button.invoke()
                wait_for_tasks(window)
                self.assertEqual(len(window._practice_windows), 1)
                self.assertEqual(next(r for r in review.report['rows'] if r['lap'] == 2)['time_ms'], 12000)
                self.assertEqual(review.refresh_button.cget("state"), "normal")
            finally:
                close_root(root)

    def test_advanced_tools_and_metrics_can_be_disclosed_without_losing_selection(self):
        root = tk.Tk()
        root.withdraw()
        try:
            window = AnalysisWindow(root, Path("data"))
            root.geometry("960x650+30000+30000")
            root.deiconify()
            root.update()
            self.assertFalse(window.build_button.winfo_ismapped())
            self.assertFalse(window.cancel_button.winfo_ismapped())
            self.assertFalse(window.metrics.winfo_ismapped())
            window.tools_button.invoke()
            root.update()
            self.assertTrue(window.build_button.winfo_ismapped())
            window.reference_var.set("保留的基准圈")
            window.details_button.invoke()
            root.update()
            self.assertTrue(window.metrics.winfo_ismapped())
            window.details_button.invoke()
            root.update()
            self.assertFalse(window.metrics.winfo_ismapped())
            self.assertEqual(window.reference_var.get(), "保留的基准圈")
            self.assertGreater(window.chart.winfo_height(), 150)
        finally:
            for callback in root.tk.splitlist(root.tk.call("after", "info")):
                root.after_cancel(callback)
            close_root(root)


if __name__ == "__main__":
    unittest.main()
