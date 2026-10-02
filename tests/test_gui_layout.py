from __future__ import annotations

import logging
from pathlib import Path
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from collector.gui import CollectorWindow
from decoder.session_history import LapRecord
from decoder.tyres import LapTyreInfo


class CollectorLayoutTests(unittest.TestCase):
    def test_tyre_fields_render_and_remain_accessible_in_small_window(self) -> None:
        root = tk.Tk()
        root.withdraw()
        try:
            with patch.object(root, "after"):
                window = CollectorWindow(root, Mock(), logging.getLogger("tyre-layout"), Path("data"))
            lap = LapRecord(5, 80716, 27000, 27000, 26716, True, True, True, True, 1,
                            tyre=LapTyreInfo(visual_compound=16, stint_number=2, tyre_lap_number=1, wear_percent=8))
            window._render_laps((lap,))
            root.geometry("760x520+30000+30000")
            root.deiconify()
            root.update()
            self._assert_buttons_inside(window)
            item = window.lap_table.get_children()[0]
            values = window.lap_table.item(item, "values")
            self.assertEqual(values[:3], ("总第5圈", "软胎（磨损8.0%）", "第 2 套 · 第 1 圈"))
            window.lap_table.xview_moveto(1)
            root.update()
            x, _y, width, _height = window.lap_table.bbox(item, "status")
            self.assertLessEqual(x + width, window.lap_table.winfo_width())
        finally:
            for after_id in root.tk.splitlist(root.tk.call("after", "info")):
                root.after_cancel(after_id)
            root.destroy()

    def _assert_buttons_inside(self, window: CollectorWindow) -> None:
        root = window.root
        root.update()
        for button in (window.analysis_button, window.close_button, window.port_button):
            x = button.winfo_rootx() - root.winfo_rootx()
            y = button.winfo_rooty() - root.winfo_rooty()
            self.assertGreaterEqual(x, 0)
            self.assertGreaterEqual(y, 0)
            self.assertLessEqual(x + button.winfo_width(), root.winfo_width())
            self.assertLessEqual(y + button.winfo_height(), root.winfo_height())
            self.assertGreater(button.winfo_height(), 20)

    def test_buttons_remain_visible_at_default_and_minimum_size(self) -> None:
        # Tk scaling is pixels/point: 96, 144 and 192 DPI respectively.
        for scaling in (96 / 72, 144 / 72, 192 / 72):
            with self.subTest(scaling=scaling):
                root = tk.Tk()
                root.withdraw()
                previous_scaling = root.tk.call("tk", "scaling")
                try:
                    root.tk.call("tk", "scaling", scaling)
                    service = Mock()
                    with patch.object(root, "winfo_screenwidth", return_value=1366), patch.object(
                        root, "winfo_screenheight", return_value=768
                    ), patch.object(root, "after"):
                        window = CollectorWindow(root, service, logging.getLogger("layout-test"), Path("data"))
                    # Map offscreen so Tk calculates real geometry without a popup.
                    root.geometry("+30000+30000")
                    root.deiconify()
                    root.update()
                    self.assertLessEqual(root.winfo_height(), 668)
                    self._assert_buttons_inside(window)
                    window.session_var.set("Session：C:/" + "long-folder-name/" * 6)
                    window.error_var.set("错误：测试错误信息，请检查日志。")
                    root.geometry("760x520+30000+30000")
                    self._assert_buttons_inside(window)
                    service.start.assert_called_once()
                finally:
                    root.tk.call("tk", "scaling", previous_scaling)
                    for after_id in root.tk.splitlist(root.tk.call("after", "info")):
                        root.after_cancel(after_id)
                    root.destroy()


if __name__ == "__main__":
    unittest.main()
