from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from dataclasses import replace
from unittest.mock import patch

from analysis.incremental import update_analysis, read_summary
from analysis.comparison import AnalysisRepository
from collector.analysis_gui import AnalysisWindow
from tests.test_foundation import write_packets
from tests.test_analysis_pipeline import session_packet, motion_packet, lap_packet, telemetry_packet
from tests.test_tyres import history_packet, player_packet


class IncrementalGuiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.rootdir = Path(self.temp.name)
        raw = self.rootdir / "raw_packets.bin"
        packets = [session_packet()]
        for number in (1, 2):
            for n, distance in enumerate(range(0, 101, 10)):
                frame = number * 30 + n
                lap = bytearray(lap_packet(frame, frame, distance))
                lap[29 + 31] = number
                packets += [motion_packet(frame, frame, distance), bytes(lap), telemetry_packet(frame, frame, distance, False)]
            packets += [history_packet([10000] * number, [(255, 18, 16)], frame=frame + 1),
                        player_packet(2, frame + 2, {"current_lap_num": number, "driver_status": 0})]
        write_packets(raw, packets)
        self.database = Path(update_analysis(raw)["analysis_database"])
        self.root = tk.Tk()
        self.root.withdraw()
        self.errors = []
        self.root.report_callback_exception = lambda _kind, value, _trace: self.errors.append(str(value))
        self.window = AnalysisWindow(self.root, self.rootdir, self.database)

    def tearDown(self):
        for callback in self.root.tk.splitlist(self.root.tk.call("after", "info")):
            self.root.after_cancel(callback)
        self.root.destroy()
        self.temp.cleanup()
        self.assertEqual(self.errors, [])

    def test_segment_selector_filters_ready_laps(self):
        window = self.window
        self.assertEqual(len(window.laps), 2)
        self.assertEqual(len(window._segment_labels), 3)
        for label, key in window._segment_labels.items():
            if key is not None:
                window.segment_var.set(label)
                window._segment_selected()
                self.assertEqual(len(window.laps), 1)
                self.assertEqual(len(window.quality_tree.get_children()), 1)
                self.assertIsNotNone(window.comparison)

    def test_auto_refresh_preserves_selected_segment_and_lap(self):
        window = self.window
        label = list(window._segment_labels)[1]
        window.segment_var.set(label)
        window._segment_selected()
        old_lap = window.laps[0].lap_number
        summary = read_summary(self.database)
        summary["total_raw_packets"] += 1
        with patch("collector.analysis_gui.read_summary", return_value=summary):
            window._poll_live_analysis()
        self.assertEqual(window.segment_var.get(), label)
        self.assertEqual(window.laps[0].lap_number, old_lap)

    def test_withdrawn_lap_clears_old_chart(self):
        window = self.window
        self.assertIsNotNone(window.comparison)
        with patch.object(window.repository, "laps", return_value=()):
            window._load_laps("77")
        self.assertIsNone(window.comparison)
        self.assertIsNone(window.chart.comparison)
        self.assertEqual(window._lap_labels, {})

    def test_new_lap_count_keeps_session_identity_and_duplicate_labels_are_distinct(self):
        window = self.window
        original = window.sessions[0]
        window._manual_session = True
        choices = (replace(original, lap_count=3), replace(original, session_uid="88", lap_count=3))
        with patch.object(AnalysisRepository, "sessions", return_value=choices):
            window.load_database(self.database)
        self.assertEqual(len(window._session_labels), 2)
        self.assertEqual(window._session_labels[window.session_var.get()], original.session_uid)

    def test_controls_and_status_fit_small_window(self):
        self.root.geometry("960x650+30000+30000")
        self.root.deiconify()
        self.root.update()
        for widget in (self.window.build_button, self.window.segment_combo, self.window.compare_combo, self.window.quality_tree):
            x = widget.winfo_rootx() - self.root.winfo_rootx()
            y = widget.winfo_rooty() - self.root.winfo_rooty()
            self.assertGreaterEqual(x, 0)
            self.assertGreaterEqual(y, 0)
            self.assertLessEqual(x + widget.winfo_width(), self.root.winfo_width())
            self.assertLessEqual(y + widget.winfo_height(), self.root.winfo_height())

    def test_quality_table_is_collapsible_without_losing_records(self):
        window = self.window
        self.root.geometry("960x650+30000+30000")
        self.root.deiconify()
        self.root.update()
        self.assertFalse(window.quality_frame.winfo_ismapped())
        rows = window.quality_tree.get_children()
        window.quality_toggle.invoke()
        self.root.update()
        self.assertTrue(window.quality_frame.winfo_ismapped())
        self.assertEqual(window.quality_tree.get_children(), rows)
        window.quality_toggle.invoke()
        self.root.update()
        self.assertFalse(window.quality_frame.winfo_ismapped())
        window._load_laps("77")
        self.assertFalse(window.quality_frame.winfo_ismapped())


if __name__ == "__main__":
    unittest.main()
