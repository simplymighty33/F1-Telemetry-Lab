"""Measure every startup control, not only buttons after data was loaded."""
from pathlib import Path
import tkinter as tk
import unittest
from unittest.mock import patch

from collector import APP_NAME, DISPLAY_VERSION
from collector.analysis_gui import AnalysisWindow


class AnalysisLayoutTests(unittest.TestCase):
    def assert_inside(self, root, widget):
        self.assertTrue(widget.winfo_ismapped(), str(widget))
        x = widget.winfo_rootx() - root.winfo_rootx()
        y = widget.winfo_rooty() - root.winfo_rooty()
        self.assertGreaterEqual(x, 0, str(widget))
        self.assertGreaterEqual(y, 0, str(widget))
        self.assertLessEqual(x + widget.winfo_width(), root.winfo_width(), str(widget))
        self.assertLessEqual(y + widget.winfo_height(), root.winfo_height(), str(widget))
        self.assertGreater(widget.winfo_height(), 10, str(widget))

    def test_empty_startup_controls_are_visible_at_all_scalings(self):
        for width, height in ((1366, 768), (1920, 1080)):
            for dpi in (96, 144, 192):
                with self.subTest(screen=(width, height), dpi=dpi):
                    root = tk.Tk()
                    root.withdraw()
                    previous = root.tk.call("tk", "scaling")
                    try:
                        root.tk.call("tk", "scaling", dpi / 72)
                        with patch.object(root, "winfo_screenwidth", return_value=width), patch.object(
                            root, "winfo_screenheight", return_value=height
                        ):
                            window = AnalysisWindow(root, Path("data"))
                        root.geometry("+30000+30000")
                        root.deiconify()
                        root.update()
                        self.assertLessEqual(root.winfo_width(), width - 60)
                        self.assertLessEqual(root.winfo_height(), height - 100)
                        self.assertEqual(root.title(), f"{APP_NAME} {DISPLAY_VERSION} · 单圈分析")
                        self.assertFalse(window.quality_frame.winfo_ismapped())
                        for widget in (window.heading_label, window.database_button, window.build_button,
                                       window.compress_button, window.session_combo, window.reference_combo,
                                       window.compare_combo, window.segment_combo, window.quality_toggle,
                                       window.status_label, window.chart, window.metrics, window.hover_label):
                            self.assert_inside(root, widget)
                        self.assertGreater(window.chart.winfo_height(), 120)
                        self.assertEqual(window._toolbar_columns, 3)
                    finally:
                        root.tk.call("tk", "scaling", previous)
                        for callback in root.tk.splitlist(root.tk.call("after", "info")):
                            root.after_cancel(callback)
                        root.destroy()

    def test_long_status_wraps_and_footer_stays_visible(self):
        root = tk.Tk()
        root.withdraw()
        try:
            window = AnalysisWindow(root, Path("data"))
            root.geometry("960x650+30000+30000")
            root.deiconify()
            window.status_var.set("正在分析已提交数据；" * 18)
            root.update()
            self.assert_inside(root, window.status_label)
            self.assert_inside(root, window.build_button)
            self.assertGreater(window.status_label.winfo_height(), 30)
            self.assertGreater(window.chart.winfo_height(), 100)
        finally:
            for callback in root.tk.splitlist(root.tk.call("after", "info")):
                root.after_cancel(callback)
            root.destroy()


if __name__ == "__main__":
    unittest.main()
