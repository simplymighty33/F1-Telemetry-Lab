"""Observable Canvas/event acceptance tests for separate driving inputs."""
from dataclasses import replace
import gc
from pathlib import Path
import tempfile
import tkinter as tk
import unittest

from analysis.comparison import AnalysisRepository
from collector.analysis_gui import ComparisonChart
from tests.test_time_regions import make_database
from tests.gui_wait import ready_window


class InputChartTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        database = Path(self.temporary.name) / "analysis.db"
        make_database(database)
        comparison = AnalysisRepository(database).comparison("7", 1, 2)
        self.comparison = replace(comparison, trace=tuple(
            replace(p, reference_throttle=.65, comparison_throttle=.30,
                    reference_brake=.80, comparison_brake=.40)
            for p in comparison.trace))
        self.root = tk.Tk()
        self.previous_scaling = self.root.tk.call("tk", "scaling")
        self.root.geometry("900x500+30000+30000")
        self.chart = ComparisonChart(self.root)
        self.chart.pack(fill="both", expand=True)
        self.chart.set_comparison(self.comparison)
        self.root.update()

    def tearDown(self):
        self.root.tk.call("tk", "scaling", self.previous_scaling)
        self.root.destroy()
        self.temporary.cleanup()
        self.chart = self.root = None
        gc.collect()

    def series(self, color):
        return [i for i in self.chart.find_withtag("series")
                if self.chart.itemcget(i, "fill") == color][-1]

    def hover_series(self, color):
        coordinates = self.chart.coords(self.series(color))
        self.chart.event_generate("<Motion>", x=round((coordinates[0] + coordinates[-2]) / 2),
                                  y=round(coordinates[1] - self.chart.canvasy(0)))
        self.root.update()
        return "\n".join(self.chart.itemcget(i, "text")
                         for i in self.chart.find_withtag("hover-values")
                         if self.chart.type(i) == "text")

    def test_throttle_and_brake_have_separate_aligned_panels(self):
        throttle = self.chart.coords(self.series("#49b8ff"))
        brake = self.chart.coords(self.series("#ff6468"))
        self.assertLess(max(throttle[1::2]), min(brake[1::2]))
        self.assertEqual(throttle[::2], brake[::2])
        titles = [self.chart.itemcget(i, "text") for i in self.chart.find_all()
                  if self.chart.type(i) == "text"]
        self.assertIn("油门", titles)
        self.assertIn("刹车", titles)
        bands = [self.chart.coords(i) for i in self.chart.find_all()
                 if self.chart.type(i) == "rectangle"]
        self.assertEqual(len(bands), 4)
        lines = self.chart.find_withtag("series")
        self.assertEqual(len(lines), 7)
        for band in bands[1:]:
            self.assertEqual(sum(all(band[1] <= y <= band[3]
                                     for y in self.chart.coords(i)[1::2]) for i in lines), 2)

    def test_hover_shows_each_inputs_two_values_beside_the_pointer(self):
        text = self.hover_series("#49b8ff")
        self.assertIn("油门", text)
        self.assertIn("基准 65.0%", text)
        self.assertIn("对比 30.0%", text)
        text = self.hover_series("#ff6468")
        self.assertIn("刹车", text)
        self.assertIn("基准 80.0%", text)
        self.assertIn("对比 40.0%", text)
        self.assertNotIn("65.0%", text)

    def test_small_high_dpi_chart_keeps_labels_and_popup_inside(self):
        self.root.tk.call("tk", "scaling", 192 / 72)
        self.chart.destroy()
        self.chart = ComparisonChart(self.root)
        self.chart.pack(fill="both", expand=True)
        self.chart.set_comparison(self.comparison)
        self.root.geometry("420x158+30000+30000")
        self.root.update()
        titles = [i for i in self.chart.find_all() if self.chart.type(i) == "text"
                  and self.chart.itemcget(i, "text") in ("时间差", "速度", "油门", "刹车")]
        boxes = [self.chart.bbox(i) for i in titles]
        self.assertEqual(len(boxes), 4)
        for before, after in zip(boxes, boxes[1:]):
            self.assertLessEqual(before[3], after[1])
        for item in self.chart.find_all():
            if self.chart.type(item) == "text":
                bounds = self.chart.bbox(item)
                self.assertGreaterEqual(bounds[0], 0)
                self.assertGreaterEqual(bounds[1], 0)
                self.assertLessEqual(bounds[2], self.chart.winfo_width())
                self.assertLessEqual(bounds[3], float(self.chart.cget("scrollregion").split()[3]))
        self.chart.scroll_plot("moveto", 1)
        self.hover_series("#ff6468")
        bounds = self.chart.bbox("hover-values")
        self.assertIsNotNone(bounds)
        self.assertGreaterEqual(bounds[0], 0)
        self.assertGreaterEqual(bounds[1], self.chart.canvasy(0))
        self.assertLessEqual(bounds[2], self.chart.winfo_width())
        self.assertLessEqual(bounds[3], self.chart.canvasy(0) + self.chart.winfo_height())

    def test_gap_hover_never_shows_operation_values_and_clears_on_leave(self):
        self.chart.set_comparison(replace(self.comparison, trace=tuple(
            replace(p, valid=False) if 400 <= p.distance_m <= 600 else p
            for p in self.comparison.trace)))
        # The last segment's midpoint lies beyond the gap; move to the whole-lap midpoint.
        coords = self.chart.coords(self.series("#ff6468"))
        first = self.chart.coords(self.chart.find_withtag("series")[0])[0]
        self.chart.event_generate("<Motion>", x=round((first + coords[-2]) / 2), y=round(coords[1]))
        self.root.update()
        text = "\n".join(self.chart.itemcget(i, "text")
                          for i in self.chart.find_withtag("hover-values")
                          if self.chart.type(i) == "text")
        self.assertIn("数据缺口", text)
        self.assertNotIn("80.0%", text)
        self.assertNotIn("40.0%", text)
        self.chart.event_generate("<Leave>")
        self.root.update()
        self.assertFalse(self.chart.find_withtag("hover"))

    def test_keyboard_values_and_range_changes_do_not_leave_stale_popup(self):
        self.chart.focus_force()
        self.chart.event_generate("<Right>")
        self.root.update()
        self.assertIn("油门", self.chart.hover_var.get())
        self.assertTrue(self.chart.find_withtag("hover-values"))
        self.chart.focus((400, 600))
        self.assertFalse(self.chart.find_withtag("hover"))
        self.hover_series("#49b8ff")
        self.chart.event_generate("<Motion>", x=1, y=1)
        self.root.update()
        self.assertFalse(self.chart.find_withtag("hover"))
        self.hover_series("#49b8ff")
        self.chart.set_comparison(None)
        self.assertFalse(self.chart.find_withtag("hover"))

    def test_popup_remains_visible_in_loaded_analysis_window(self):
        self.chart.destroy()
        self.root.withdraw()
        self.root.tk.call("tk", "scaling", 144 / 72)
        window = ready_window(self.root, Path(self.temporary.name),
                              Path(self.temporary.name) / "analysis.db")
        self.chart = window.chart
        self.root.geometry("960x650+30000+30000")
        self.root.deiconify()
        self.root.update()
        self.chart.set_comparison(self.comparison)
        self.root.update()
        text = self.hover_series("#49b8ff")
        self.assertIn("基准 65.0%", text)
        self.assertGreater(self.chart.winfo_height(), 150)


if __name__ == "__main__":
    unittest.main()
