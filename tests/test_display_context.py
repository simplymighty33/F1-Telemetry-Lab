from dataclasses import replace
import hashlib
import logging
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from analysis.comparison import AnalysisRepository
from collector.analysis_gui import lap_label
from tests.gui_wait import ready_window as AnalysisWindow
from collector.gui import CollectorWindow
from collector.service import CollectorService, CollectorSnapshot
from collector.settings import load_settings
from decoder.display import stage_label, session_label, tyre_label
from decoder.header import decode_header
from decoder.session_history import LapRecord
from decoder.tyres import LapTyreInfo
from tests.test_analysis_pipeline import session_packet
from tests.test_time_regions import make_database
from tests.gui_wait import wait_for_tasks


def snapshot(**changes):
    values = dict(state="receiving", message="接收中", received_packets=1, persisted_packets=1,
        packet_rate_hz=60, last_packet_at_ns=1, queue_size=0, queue_capacity=8192,
        session_directory=None, error=None, game_mode="Career", session_type="Practice 1",
        track_name="Melbourne", session_uid=7, player_car_index=0, packet_format=2023,
        foundation_state="ready", analysis_state="ready")
    values.update(changes)
    return CollectorSnapshot(**values)


class DisplayTests(unittest.TestCase):
    def test_stage_mapping_does_not_guess_sprint_race(self):
        for value in ("Race", "Race 2", "Race 3"):
            self.assertIn("待确认", stage_label(value))
        self.assertEqual(stage_label("Practice 3"), "练习赛3")
        self.assertEqual(stage_label("Sprint Shootout 2"), "冲刺排位赛SQ2")
        self.assertEqual(stage_label("Qualifying 1"), "排位赛Q1")
        self.assertEqual(stage_label("Unknown (99)"), "Unknown (99)")
        self.assertEqual(session_label("Melbourne", "Practice 1"), "澳大利亚 · 练习赛1")
        self.assertNotEqual(session_label("Silverstone", "Time Trial"), session_label("Silverstone Reverse", "Time Trial"))

    def test_wear_zero_unknown_and_unusable_values(self):
        self.assertEqual(tyre_label(LapTyreInfo(visual_compound=16, wear_percent=0)), "软胎（磨损0.0%）")
        for value in (None, float("nan"), float("inf"), -1, 101):
            self.assertEqual(tyre_label(LapTyreInfo(visual_compound=16, wear_percent=value)), "软胎（磨损未知）")

    def test_live_service_changes_clear_old_context_and_laps(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings = load_settings(Path(temporary) / "settings.json")
            service = CollectorService(settings, Path(temporary), logging.getLogger("context"))
            header = decode_header(session_packet())
            service._observe_context(header)
            service._track_name, service._session_type = "Melbourne", "Practice 1"
            service.drain_lap_updates()
            service._observe_context(header)
            self.assertEqual(service.drain_lap_updates(), [])
            service._observe_context(replace(header, session_uid=88))
            self.assertEqual(service.snapshot().session_uid, 88)
            self.assertEqual(service.snapshot().track_name, "—")
            self.assertEqual(service.drain_lap_updates()[0].laps, ())
            service._track_name = "Melbourne"
            service._observe_context(replace(header, session_uid=88, player_car_index=1))
            self.assertEqual(service.snapshot().track_name, "—")


class ContextGuiTests(unittest.TestCase):
    def test_new_lap_preserves_old_row_view_and_selection(self):
        with patch.object(self.root, 'after'):
            window = CollectorWindow(self.root, Mock(), logging.getLogger('scroll-ui'), self.directory)
        self.root.geometry('960x650+30000+30000')
        self.root.deiconify()
        laps = tuple(LapRecord(i, 82000,27000,27000,28000,True,True,True,True,1) for i in range(1,101))
        window._render_laps(laps)
        self.root.update()
        window.lap_table.yview_moveto(.5)
        window.lap_table.selection_set('lap-40')
        self.root.update()
        visible = window.lap_table.identify_row(35)
        window._render_laps(laps+(replace(laps[-1],lap_number=101),))
        self.root.update()
        self.assertEqual(window.lap_table.identify_row(35),visible)
        self.assertEqual(window.lap_table.selection(),('lap-40',))
        self.assertEqual(window.lap_table.get_children()[0],'lap-101')

    def test_invalid_database_preserves_existing_selection_and_focus(self):
        import sqlite3
        from contextlib import closing
        invalid = self.directory/'invalid.db'
        make_database(invalid)
        with closing(sqlite3.connect(invalid)) as con:
            con.execute('ALTER TABLE lap_metrics RENAME COLUMN maximum_speed_kph TO unsupported_speed')
            con.commit()
        window = AnalysisWindow(self.root, self.directory, self.db)
        window._manual_session = True
        before = window.reference_var.get()
        window.chart.focus((100.,200.))
        with patch('collector.analysis_gui.messagebox.showerror'):
            self.assertFalse(window.load_database(invalid))
        self.assertEqual(window.repository.database, self.db.resolve())
        self.assertTrue(window._manual_session)
        self.assertEqual(window.reference_var.get(), before)
        self.assertEqual(window.chart.focus_range, (100.,200.))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.db = self.directory / "telemetry_analysis.db"
        make_database(self.db)
        self.root = tk.Tk()
        self.root.withdraw()

    def tearDown(self):
        for callback in self.root.tk.splitlist(self.root.tk.call("after", "info")):
            self.root.after_cancel(callback)
        self.root.destroy()
        self.temp.cleanup()

    def test_lap_names_are_contextual_and_database_remains_read_only(self):
        before = hashlib.sha256(self.db.read_bytes()).digest()
        repository = AnalysisRepository(self.db)
        lap = repository.laps("7")[0]
        label = lap_label(replace(lap, track="Melbourne", session_type="Practice 2",
                                 tyre=LapTyreInfo(visual_compound=16, wear_percent=12)))
        self.assertEqual(label, "澳大利亚 · 练习赛2 · 总第1圈｜软胎（磨损12.0%）｜0:10.000")
        self.assertNotEqual(label, lap_label(replace(lap, lap_number=2)))
        window = AnalysisWindow(self.root, self.directory, self.db)
        self.assertIn("计时赛", window.heading_label.cget("text"))
        self.assertIn("总第1圈", tuple(window._lap_labels)[0])
        self.assertEqual(before, hashlib.sha256(self.db.read_bytes()).digest())

    def test_main_heading_and_rows_clear_on_session_or_player_change(self):
        with patch.object(self.root, "after"):
            window = CollectorWindow(self.root, Mock(), logging.getLogger("context-ui"), self.directory)
        state = snapshot()
        window._render_snapshot(state)
        lap = LapRecord(33, 82000, 27000, 27000, 28000, True, True, True, True, 1,
                        tyre=LapTyreInfo(visual_compound=16, wear_percent=12))
        window._render_laps((lap,))
        self.assertIn("澳大利亚 · 练习赛1", window.lap_heading_var.get())
        self.assertEqual(window.lap_table.item(window.lap_table.get_children()[0], "values")[0], "总第33圈")
        window._render_snapshot(replace(state, session_uid=8, session_type="Qualifying 1"))
        self.assertEqual(window.lap_table.get_children(), ())
        self.assertIn("排位赛Q1", window.lap_heading_var.get())
        window._render_laps((lap,))
        window._render_snapshot(replace(state, session_uid=8, player_car_index=1))
        self.assertEqual(window.lap_table.get_children(), ())

    def test_live_window_waits_without_starting_raw_replay(self):
        state = snapshot(session_directory=self.directory / "live")
        window = AnalysisWindow(self.root, self.directory)
        with patch("collector.analysis_gui.build_analysis") as rebuild:
            window.attach_live(lambda: state)
            window._poll_live_analysis()
            rebuild.assert_not_called()
        self.assertIn("等待完整有效圈", window.database_var.get())
        self.assertIn("无需导入Raw", window.database_var.get())

    def test_live_rebind_clears_old_chart_and_reports_pressure_or_disabled(self):
        state = snapshot(session_directory=self.directory)
        window = AnalysisWindow(self.root, self.directory, self.db)
        window.attach_live(lambda: state)
        window.load_database(self.db)
        wait_for_tasks(window)
        self.assertIsNotNone(window.comparison)
        state = replace(state, session_directory=self.directory / "new", session_uid=8, analysis_state="throttled")
        window._sync_live_connection()
        self.assertIsNone(window.comparison)
        self.assertEqual(window.laps, ())
        self.assertFalse(window.reference_combo["values"])
        self.assertIn("资源压力", window.database_var.get())
        state = replace(state, analysis_state="disabled")
        window._sync_live_connection()
        self.assertIn("自动分析未开启", window.database_var.get())

    def test_new_game_stage_never_relabels_old_chart(self):
        state = snapshot(session_directory=self.directory)
        window = AnalysisWindow(self.root, self.directory, self.db)
        window.attach_live(lambda: state)
        window.load_database(self.db)
        state = replace(state, session_uid=88, session_type="Qualifying 3")
        window._sync_live_connection()
        window.load_database(self.db)
        window._sync_live_connection()
        self.assertIsNone(window.comparison)
        self.assertEqual(window.laps, ())
        self.assertEqual(window.session_var.get(), "")
        self.assertIn("排位赛Q3", window.heading_label.cget("text"))
        # An explicit historical selection remains available and labelled as history.
        window.session_var.set(next(iter(window._session_labels)))
        window._session_selected(object())
        wait_for_tasks(window)
        window._sync_live_connection()
        self.assertIsNotNone(window.comparison)
        self.assertIn("手动查看历史会话", window.database_var.get())

    def test_failed_database_selection_keeps_live_connection(self):
        state = snapshot(session_directory=self.directory)
        window = AnalysisWindow(self.root, self.directory, self.db)
        provider = lambda: state
        window.attach_live(provider)
        with patch("collector.analysis_gui.filedialog.askopenfilename", return_value=str(self.directory / "missing.db")), patch(
            "collector.analysis_gui.messagebox.showerror"):
            window.choose_database()
        self.assertIs(window._live_provider, provider)
        self.assertIn("已连接当前录制", window.database_var.get())

    def test_full_lap_name_hint_and_close_cancel_pending_hint(self):
        window = AnalysisWindow(self.root, self.directory, self.db)
        window._show_lap_hint(window.reference_combo)
        hint = window._lap_hint
        self.assertEqual(hint.winfo_children()[0].cget("text"), window.reference_var.get())
        window._hide_lap_hint()
        window._schedule_lap_hint(window.reference_combo)
        self.assertIsNotNone(window._lap_hint_after)
        window.prepare_close()
        self.assertIsNone(window._lap_hint_after)
        self.assertIsNone(window._lap_hint)


if __name__ == "__main__":
    unittest.main()
