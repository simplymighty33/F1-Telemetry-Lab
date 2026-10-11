"""Qt event/lifecycle coverage; no replacement of legacy Tk tests."""
import importlib.util
import logging
import os
from pathlib import Path
import random
import socket
import tempfile
import threading
import time
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

QT_AVAILABLE = importlib.util.find_spec('PySide6') is not None and importlib.util.find_spec('pyqtgraph') is not None
if QT_AVAILABLE:
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6 import QtCore, QtWidgets, QtTest
    from collector.qt_ui.theme import install, curve_pen
    from collector.qt_ui.main_window import MainWindow
    from collector.qt_ui.charts import ChartStack
    from collector.qt_ui.analysis_controller import AnalysisController, Catalog, read_revision, query_catalog, SnapshotChanged
    from collector.qt_ui.adapters import channel_data
    from collector.service import CollectorService
    from collector.settings import load_settings
    from decoder.header import HEADER_STRUCT
    from decoder.full_parser import PACKET_SIZES
    from storage.raw_archive import iter_archive


@unittest.skipUnless(QT_AVAILABLE, 'Optional Qt GUI dependencies not installed')
class QtUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        if os.name == 'nt' and cls.app.platformName() == 'offscreen':
            from PySide6 import QtGui
            for name in ('segoeui.ttf', 'segoeuib.ttf', 'seguisb.ttf', 'msyh.ttc', 'msyhbd.ttc', 'consola.ttf'):
                QtGui.QFontDatabase.addApplicationFont(str(Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / name))
        install(cls.app)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.settings = load_settings(self.root / 'settings.json')
        self.logger = logging.getLogger('qt-tests')
        self.logger.addHandler(logging.NullHandler())
        self.window = MainWindow(None, self.logger, self.root / 'data', self.root / 'settings.json')
        self.window.show()
        self.release = threading.Event()
        self.callback_errors = []
        self.original_hook = sys.excepthook
        sys.excepthook = lambda kind, value, traceback: self.callback_errors.append(value)
        self.app.processEvents()

    def test_decode_rejections_are_visible_without_claiming_raw_loss(self):
        from dataclasses import replace
        page = self.window.capture_page
        snapshot = CollectorService(self.settings, self.root / 'unused', self.logger).snapshot()
        page.update_snapshot(replace(snapshot, foundation_decode_errors=12))
        self.assertIn('12', page.warning.text())
        self.assertIn('Raw', page.warning.text())
        self.assertIn('独立重建', page.warning.text())
        page.update_snapshot(replace(snapshot, foundation_decode_errors=0))
        self.assertFalse(page.warning.isVisible())

    def tearDown(self):
        self.release.set()
        self.window.close()
        self.wait_until(lambda: self.window.allow_close)
        self.window.deleteLater()
        self.app.processEvents()
        self.temporary.cleanup()
        sys.excepthook = self.original_hook
        self.assertEqual(self.callback_errors, [])

    def wait_until(self, predicate, timeout=8):
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() > deadline:
                self.fail('Qt lifecycle timeout')
            self.app.processEvents()
            QtTest.QTest.qWait(10)

    def test_shell_three_pages_and_footer_accessible(self):
        for index in range(3):
            QtTest.QTest.mouseClick(self.window.nav[index], QtCore.Qt.MouseButton.LeftButton)
            self.assertEqual(self.window.stack.currentIndex(), index)
            self.assertTrue(self.window.stop_button.isVisible())
            self.assertTrue(self.window.centralWidget().rect().contains(self.window.stop_button.mapTo(self.window.centralWidget(), QtCore.QPoint(0, 0))))

    def test_brand_name_and_original_multisize_icon(self):
        self.assertEqual(self.app.applicationName(), 'RaceTrace Lab')
        self.assertTrue(self.window.windowTitle().startswith('RaceTrace Lab '))
        self.assertEqual(self.window.brand.text(), 'RaceTrace Lab')
        self.assertFalse(self.window.windowIcon().isNull())
        sizes = {size.width() for size in self.window.windowIcon().availableSizes()}
        self.assertTrue({16, 32, 48, 256}.issubset(sizes))

    def test_lap_ordinals_keep_context_without_redundant_total_prefix(self):
        from decoder.display import lap_display
        from collector.qt_ui.tables import LapModel
        tyre = SimpleNamespace(name='软胎', wear_percent=8.4)
        self.assertEqual(lap_display('Melbourne', 'Practice 2', 33, tyre, '1:22.000'),
                         '澳大利亚 · 练习赛2 · 第33圈｜软胎（磨损8.4%）｜1:22.000')
        model = LapModel()
        model.replace([SimpleNamespace(lap_number=33, tyre=tyre, lap_time_ms=82000)], 33, 33)
        self.assertEqual(model.index(0).data(QtCore.Qt.ItemDataRole.DisplayRole),
                         '第33圈 · 软胎（磨损8.4%） · 1:22.000')
        for table in (self.window.capture_page.laps, self.window.analysis_page.quality_table,
                      self.window.practice_page.laps, self.window.practice_page.conditions):
            self.assertEqual(table.model().headerData(0, QtCore.Qt.Orientation.Horizontal), '圈号')

    def test_chart_legends_are_right_aligned_and_last_plot_is_scrollable(self):
        self.window.resize(1200, 700)
        self.window.set_page(1)
        QtTest.QTest.qWait(40)
        charts = self.window.analysis_page.charts
        for plot in charts.plots:
            panel = plot.parentWidget()
            labels = panel.findChildren(QtWidgets.QLabel)
            title, *legends = labels
            self.assertTrue(legends)
            for item in legends:
                self.assertGreater(item.x(), title.x() + title.width())
                self.assertTrue(panel.rect().contains(item.geometry()))
        charts.verticalScrollBar().setValue(charts.verticalScrollBar().maximum())
        QtTest.QTest.qWait(40)
        last = charts.plots[-1]
        bounds = QtCore.QRect(last.mapTo(charts.viewport(), QtCore.QPoint()), last.size())
        self.assertTrue(charts.viewport().rect().contains(bounds))
        self.assertTrue(self.window.stop_button.isVisible())

    def test_navigation_is_keyboard_operable_after_visual_polish(self):
        self.window.nav[0].setFocus(QtCore.Qt.FocusReason.TabFocusReason)
        QtTest.QTest.keyClick(self.window.nav[0], QtCore.Qt.Key.Key_Space)
        self.assertEqual(self.window.stack.currentIndex(), 0)
        QtTest.QTest.keyClick(self.window.nav[0], QtCore.Qt.Key.Key_Tab)
        self.assertTrue(self.window.nav[1].hasFocus())
        QtTest.QTest.keyClick(self.window.nav[1], QtCore.Qt.Key.Key_Space)
        self.assertEqual(self.window.stack.currentIndex(), 1)
        self.assertTrue(self.window.nav[1].isChecked())

    def test_text_acceptance_respects_hard_line_breaks(self):
        spec = importlib.util.spec_from_file_location('qt_acceptance_test',
                 Path(__file__).resolve().parents[1] / 'packaging/qt_acceptance.py')
        harness = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(harness)
        from collector.qt_ui.theme import label
        host = QtWidgets.QWidget()
        item = label('第一行\n\n最后一行')
        item.setParent(host)
        item.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop)
        item.resize(180, 15)
        host.show()
        item.show()
        self.app.processEvents()
        try:
            row = harness.text_geometry(host)[0]
            self.assertFalse(row['fits'], 'A three-line label must not fit one-line height')
            self.assertEqual(row['line_count'], 3)
            item.resize(180, 80)
            row = harness.text_geometry(host)[0]
            self.assertTrue(row['fits'])
        finally:
            host.close()
            host.deleteLater()

    def test_workspace_navigation_stays_at_top_on_capture_and_practice(self):
        heading = next(item for item in self.window.rail.findChildren(QtWidgets.QLabel)
                       if item.text() == '工作空间')
        for size in ((1366, 768), (1920, 1080)):
            self.window.resize(*size)
            positions = []
            for index in (0, 2, 1, 0, 2):
                QtTest.QTest.mouseClick(self.window.nav[index], QtCore.Qt.MouseButton.LeftButton)
                self.app.processEvents()
                with self.subTest(size=size, page=index):
                    self.assertLessEqual(heading.height(), 32)
                    self.assertTrue(heading.alignment() & QtCore.Qt.AlignmentFlag.AlignHCenter)
                    top = self.window.nav[0].mapTo(self.window.rail, QtCore.QPoint()).y()
                    self.assertLessEqual(top, 64)
                    positions.append(top)
                    self.assertEqual(self.window.analysis_page.lap_list.isVisible(), index == 1)
                    self.assertTrue(self.window.stop_button.isVisible())
            self.assertEqual(len(set(positions)), 1, 'Navigation must not jump on page changes')

    def test_sidebar_help_keeps_last_line_visible_after_resizing(self):
        help_label = self.window.rail_help
        self.assertTrue(help_label.text().endswith('历史来源不会随游戏\n会话切换。'))
        for width, height in ((1200, 700), (1366, 768), (1920, 1080), (1366, 768)):
            self.window.resize(width, height)
            for page in (0, 2, 1, 0):
                QtTest.QTest.mouseClick(self.window.nav[page], QtCore.Qt.MouseButton.LeftButton)
                QtTest.QTest.qWait(30)
                if page != 1:
                    with self.subTest(size=(width, height), page=page):
                        self.assertGreaterEqual(help_label.height(),
                                                help_label.heightForWidth(help_label.width()))
                        bounds = QtCore.QRect(help_label.mapTo(self.window.rail, QtCore.QPoint()),
                                             help_label.size())
                        self.assertTrue(self.window.rail.rect().contains(bounds))

    def test_capture_summary_is_compact_but_expands_for_visible_errors(self):
        from dataclasses import replace
        self.window.set_page(0)
        page = self.window.capture_page
        snapshot = CollectorService(self.settings, self.root / 'unused', self.logger).snapshot()
        page.update_snapshot(snapshot)
        self.app.processEvents()
        panel = page.page_title.parentWidget()
        self.assertLessEqual(panel.height(), 170)
        self.assertFalse(page.warning.isVisible())
        normal_height = panel.height()
        warning = '分析数据尚未提交，请等待后台处理；原始数据采集状态需独立查看。' * 10
        error = '原始档案写入失败，请检查磁盘可用空间及输出目录权限。' * 8
        page.update_snapshot(replace(snapshot, health_warnings=(warning,), error=error,
                                     game_mode='职业生涯 · 当前完整会话条件说明' * 6))
        QtTest.QTest.qWait(40)
        self.assertTrue(page.warning.isVisible())
        self.assertEqual(page.warning.text(), warning)
        self.assertIn(error, page.raw_status.text())
        self.assertGreater(panel.height(), normal_height)
        for label in (page.warning, page.raw_status, page.context, page.analysis_status):
            self.assertTrue(label.wordWrap())
            self.assertGreaterEqual(label.height(), label.heightForWidth(label.width()))
        self.assertTrue(self.window.stop_button.isVisible())
        page.update_snapshot(snapshot)
        QtTest.QTest.qWait(40)
        self.assertFalse(page.warning.isVisible())
        self.assertLessEqual(panel.height(), 170)

    def test_semantic_type_roles_are_immutable_and_tabular_only_for_digits(self):
        from dataclasses import FrozenInstanceError
        from PySide6 import QtGui
        from collector.qt_ui import theme
        approved = {12, 14, 16, 18, 20, 24, 50, 68}
        numeric = {'axis', 'lap_value', 'timing_compact', 'timing_display'}
        self.assertEqual(set(theme.TYPE), {'caption', 'axis', 'body', 'body_strong', 'summary', 'section',
                                         'context', 'page_title', 'brand', 'lap_value', 'timing_compact',
                                         'timing_display', 'log'})
        tag = QtGui.QFont.Tag.fromString('tnum')
        for role, token in theme.TYPE.items():
            with self.subTest(role=role):
                face = theme.font(role)
                self.assertIn(token.size, approved)
                self.assertEqual(face.pixelSize(), token.size)
                self.assertEqual(face.weight(), token.weight)
                self.assertEqual(face.families(), list(token.families))
                self.assertEqual(face.isFeatureSet(tag), role in numeric)
                self.assertEqual(face.styleStrategy(), QtGui.QFont.StyleStrategy.PreferAntialias)
                with self.assertRaises(FrozenInstanceError):
                    token.size = 13
        self.assertEqual(self.app.font().pixelSize(), 14)
        self.assertEqual(theme.TYPE['caption'].size, 12)
        self.assertEqual(theme.TYPE['page_title'].size, 20)
        self.assertEqual(theme.TYPE['lap_value'].size, 24)
        self.assertEqual(theme.TYPE['timing_display'].size, 68)
        with self.assertRaises(KeyError):
            theme.font('unknown-role')

    def test_page_title_and_control_roles_match_the_approved_hierarchy(self):
        from PySide6 import QtGui
        from collector.qt_ui import theme
        for title in (self.window.capture_page.page_title, self.window.analysis_page.scope,
                      self.window.practice_page.heading):
            self.assertEqual(title.property('typeRole'), 'page_title')
            self.assertEqual(title.font().pixelSize(), 20)
            self.assertEqual(title.font().weight(), QtGui.QFont.Weight.DemiBold)
        self.assertEqual(self.window.capture_page.heading.property('typeRole'), 'section')
        self.assertEqual(self.window.capture_page.heading.font().pixelSize(), 16)
        self.assertEqual(self.window.capture_page.path.font().pixelSize(), 12)
        self.assertEqual(self.window.practice_page.stats.property('typeRole'), 'summary')
        self.assertEqual(self.window.practice_page.stats.font().weight(), QtGui.QFont.Weight.Normal)
        self.assertEqual(self.window.brand.property('typeRole'), 'brand')
        self.assertEqual(self.window.brand.font().pixelSize(), 20)
        self.assertEqual(QtGui.QFontInfo(self.window.brand.font()).family(), 'Segoe UI')
        for item in (*self.window.nav, self.window.stop_button,
                     self.window.capture_page.laps.horizontalHeader()):
            self.assertEqual(item.property('typeRole'), 'body_strong')
            self.assertEqual(item.font().weight(), QtGui.QFont.Weight.DemiBold)
        self.assertEqual(self.window.capture_page.laps.verticalHeader().defaultSectionSize(), 34)
        self.assertEqual(theme.font('lap_value').pixelSize(), 24)
        for small, role in ((True, 'timing_compact'), (False, 'timing_display'), (True, 'timing_compact')):
            self.window.analysis_page.cards[0].compact(small)
            value = self.window.analysis_page.cards[0].value
            self.assertEqual(value.property('typeRole'), role)
            self.assertEqual(value.font().pixelSize(), theme.TYPE[role].size)

    def test_tabular_digits_resolve_semibold_without_unexpected_fallback(self):
        from PySide6 import QtGui
        from collector.qt_ui import theme
        for role in ('lap_value', 'timing_compact', 'timing_display'):
            value = theme.font(role)
            fm = QtGui.QFontMetricsF(value)
            self.assertAlmostEqual(fm.horizontalAdvance('1:11.111'), fm.horizontalAdvance('8:88.888'), delta=.5)
            self.assertEqual(QtGui.QFontInfo(value).family(), 'Segoe UI')
            self.assertEqual(QtGui.QFontInfo(value).styleName(), 'Semibold')
            self.assertEqual(value.featureValue(QtGui.QFont.Tag.fromString('tnum')), 1)
        self.assertEqual(QtGui.QFontInfo(theme.font('body')).family(), 'Microsoft YaHei UI')
        face = QtGui.QRawFont.fromFont(theme.font('body'))
        self.assertTrue(all(face.supportsCharacter(ord(char)) for char in '澳大利亚练习赛圈速磨损Δ%kgMJ…'))

    def test_1366_timing_ink_has_width_and_height_clearance(self):
        from collector.qt_ui.self_test import typography_evidence, check_typography
        self.window.resize(1366, 768)
        self.window.set_page(1)
        for card, text in zip(self.window.analysis_page.cards, ('1:19.335', '1:28.292', '+8.957')):
            card.value.setText(text)
        QtTest.QTest.qWait(40)
        check_typography(typography_evidence(self.window))
        header = self.window.brand.parentWidget()
        brand_rect = QtCore.QRect(self.window.brand.mapTo(header, QtCore.QPoint()), self.window.brand.size())
        for control in header.findChildren(QtWidgets.QAbstractButton):
            if control.isVisible():
                rect = QtCore.QRect(control.mapTo(header, QtCore.QPoint()), control.size())
                self.assertFalse(brand_rect.intersects(rect))
        self.window.analysis_page.scope.setText('单圈分析 · 澳大利亚 · 冲刺排位赛 · 职业生涯 · 当前完整会话与驾驶段条件说明' * 2)
        QtTest.QTest.qWait(40)
        self.assertTrue(self.window.stop_button.isVisible())
        bounds = QtCore.QRect(self.window.stop_button.mapTo(self.window.centralWidget(), QtCore.QPoint()), self.window.stop_button.size())
        self.assertTrue(self.window.centralWidget().rect().contains(bounds))
        self.assertTrue(self.window.analysis_page.reference.isVisible())
        self.assertTrue(self.window.analysis_page.scope.hasHeightForWidth())

    def test_lap_delegate_three_text_rows_fit_86_DIP_at_DPR_1_and_2(self):
        from PySide6 import QtGui
        from collector.qt_ui import theme
        from collector.qt_ui.tables import LapDelegate, LapModel
        from collector.qt_ui.adapters import lap_time
        from decoder.display import tyre_label
        lap = SimpleNamespace(lap_number=11, lap_time_ms=79335,
                              tyre=SimpleNamespace(wear_percent=8.4, name='软胎'))
        model = LapModel()
        model.replace([lap], 11, 11)
        delegate = LapDelegate()
        option = QtWidgets.QStyleOptionViewItem()
        option.rect = QtCore.QRect(0, 0, 160, 86)
        self.assertEqual(delegate.sizeHint(option, model.index(0)), QtCore.QSize(160, 86))
        inner = QtCore.QRectF(option.rect).adjusted(3, 3, -3, -3)
        rows = [('body_strong', inner.adjusted(10, 5, -8, -5), '第11圈  基准 / 对比'),
                ('lap_value', inner.adjusted(10, 27, -8, -5), lap_time(lap.lap_time_ms)),
                ('caption', inner.adjusted(10, 59, -8, -4), tyre_label(lap.tyre))]
        for dpr in (1, 2):
            raster = QtGui.QImage(160 * dpr, 86 * dpr, QtGui.QImage.Format.Format_ARGB32_Premultiplied)
            raster.setDevicePixelRatio(dpr)
            raster.fill(QtCore.Qt.GlobalColor.transparent)
            painter = QtGui.QPainter(raster)
            ink_rows = []
            for role, rect, text in rows:
                fm = QtGui.QFontMetricsF(theme.font(role), painter.device())
                if role in ('caption', 'body_strong'):
                    text = fm.elidedText(text, QtCore.Qt.TextElideMode.ElideRight, inner.width() - 20)
                ink = fm.tightBoundingRect(text).translated(rect.left(), rect.top() + fm.ascent())
                self.assertTrue(inner.contains(ink), (role, dpr, ink))
                ink_rows.append(ink)
            self.assertTrue(all(a.bottom() < b.top() for a, b in zip(ink_rows, ink_rows[1:])))
            delegate.paint(painter, option, model.index(0))
            painter.end()
        self.assertIn(lap_time(lap.lap_time_ms), model.index(0).data(QtCore.Qt.ItemDataRole.ToolTipRole))

    def test_small_window_folds_details_instead_of_cropping_timing(self):
        self.window.resize(1200, 700)
        self.window.set_page(1)
        self.window.analysis_page.cards[0].value.setText('1:19.335')
        QtTest.QTest.qWait(40)
        page = self.window.analysis_page
        self.assertTrue(page.details_button.isVisible())
        self.assertLessEqual(page.cards[0].value.fontMetrics().horizontalAdvance('1:19.335'), page.cards[0].value.width())
        QtTest.QTest.mouseClick(page.details_button, QtCore.Qt.MouseButton.LeftButton)
        self.assertTrue(page.right.isVisible())
        self.assertFalse(page.center_column.isVisible())
        self.assertTrue(self.window.stop_button.isVisible())

    def test_close_waits_for_running_producer_without_blocking(self):
        started = threading.Event()
        def produce(control, cancelled):
            started.set()
            self.release.wait(3)
            return {}
        self.window.operation.start('busy', produce)
        self.assertTrue(started.wait(1))
        before = time.perf_counter()
        self.window.close()
        self.assertLess(time.perf_counter() - before, .2)
        self.assertFalse(self.window.allow_close)
        self.assertTrue(self.window.closing_dialog.isVisible())
        self.window.close()
        self.release.set()
        self.wait_until(lambda: self.window.allow_close)

    def test_source_switch_discards_late_query_and_clears_plots(self):
        controller = self.window.controller
        def query(path, uid, segment):
            if path.name == 'first.db':
                self.release.wait(3)
                raise ValueError('obsolete result')
            return Catalog(SimpleNamespace(database=path), (), None, (), None, (), (), (str(path), '2'))
        seen = []
        controller.failed.connect(seen.append)
        with patch('collector.qt_ui.analysis_controller.query_catalog', side_effect=query):
            controller.set_source(self.root / 'first.db')
            QtTest.QTest.qWait(30)
            controller.set_source(self.root / 'second.db')
            self.assertIsNone(self.window.analysis_page.charts.model)
            self.release.set()
            self.wait_until(lambda: controller.catalog is not None)
        self.assertEqual(controller.catalog.repository.database.name, 'second.db')
        self.assertEqual(seen, [])

    def test_same_path_replacement_invalidates_same_metadata_revision(self):
        path = self.root / 'replace.db'
        path.write_bytes(b'old')
        with patch('collector.qt_ui.analysis_controller.read_summary', return_value={'analysis_revision': 4}):
            first = read_revision(path)
            self.assertEqual(first, read_revision(path))
            new = self.root / 'new.db'
            new.write_bytes(b'new')
            new.replace(path)
            self.assertNotEqual(first, read_revision(path))

    def test_catalog_rejects_revision_change_during_queries(self):
        repository = SimpleNamespace(sessions=lambda: (), database=self.root / 'catalog.db')
        with patch('collector.qt_ui.analysis_controller.AnalysisRepository', return_value=repository), patch(
                'collector.qt_ui.analysis_controller.read_revision', side_effect=[('id', '1'), ('id', '2')]):
            with self.assertRaises(SnapshotChanged):
                query_catalog(repository.database, None, None)

    def test_practice_rejects_revision_change_during_queries(self):
        from collector.qt_ui.main_window import query_practice
        repository = SimpleNamespace(database=self.root / 'practice.db')
        with patch('collector.qt_ui.main_window.practice_report', return_value={}), patch(
                'collector.qt_ui.main_window.read_revision', side_effect=[('id', '1'), ('id', '2')]):
            with self.assertRaises(SnapshotChanged):
                query_practice(repository, '77', None, 1, ('id', '1'), lambda: False)

    def test_unchanged_revision_refresh_does_not_reapply_catalog(self):
        controller = self.window.controller
        catalog = Catalog(SimpleNamespace(database=self.root / 'stable.db'), (), None, (), None, (), (), ('path', 'file', '1'))
        applied = []
        controller.catalog_ready.connect(applied.append)
        with patch('collector.qt_ui.analysis_controller.query_catalog', return_value=catalog):
            controller.set_source(catalog.repository.database)
            self.wait_until(lambda: len(applied) == 1)
            previous = controller.probe
            controller.refresh()
            self.wait_until(lambda: controller.catalog_task.ready())
            controller.poll()
        self.assertGreater(controller.probe, previous)
        self.assertEqual(len(applied), 1)

    def test_graph_key_steps_original_samples_not_fixed_distance(self):
        charts = self.window.analysis_page.charts
        from analysis.comparison import TracePoint
        charts.trace = tuple(TracePoint(d, 0, 0, 0, 200, 201, .5, .6, .1, .2, 4, 4) for d in (0., 7., 30.))
        charts.plots[0].setXRange(0, .03, padding=0)
        charts.inspect(7)
        QtTest.QTest.keyClick(charts.plots[0], QtCore.Qt.Key.Key_Right)
        self.assertEqual(charts.distance, 30)
        QtTest.QTest.keyClick(charts.plots[0], QtCore.Qt.Key.Key_Left)
        self.assertEqual(charts.distance, 7)
        QtTest.QTest.keyClick(charts.plots[0], QtCore.Qt.Key.Key_Escape)
        self.assertIsNone(charts.distance)
        charts.focus_region(7, 30)
        QtTest.QTest.keyClick(charts.plots[0], QtCore.Qt.Key.Key_0, QtCore.Qt.KeyboardModifier.ControlModifier)
        self.assertFalse(charts.plots[0].region.isVisible())

    def test_curve_pen_is_derived_from_base_not_existing_width(self):
        plot = self.window.analysis_page.charts.plots[0]
        dpr = plot.viewport().devicePixelRatioF()
        for _ in range(4):
            plot.refresh_pens()
            self.assertAlmostEqual(plot.curves[0][0].opts['pen'].widthF(), 1.6 * dpr)
            self.assertTrue(plot.curves[0][0].opts['pen'].isCosmetic())

    def test_generated_connections_never_cross_invalid_or_independent_break(self):
        rng = random.Random(20261006)
        for _ in range(200):
            trace = [SimpleNamespace(distance_m=i * 5., delta_ms=rng.choice([0., 42., float('nan')]),
                                     valid=rng.choice([True, False]), connected=rng.choice([True, False]))
                     for i in range(rng.randrange(0, 65))]
            x, y, connections = channel_data(trace, 'delta_ms')
            self.assertEqual(len(connections), len(trace))
            for i in range(len(trace)):
                if connections[i]:
                    self.assertLess(i + 1, len(trace))
                    self.assertTrue(trace[i].valid and trace[i + 1].valid and trace[i + 1].connected)
                    self.assertTrue(all(__import__('math').isfinite(v) for v in (x[i], x[i + 1], y[i], y[i + 1])))

    def test_real_udp_capture_close_and_raw_replay(self):
        # A fresh isolated receiver; no user recording or game settings involved.
        service = CollectorService(self.settings, self.root / 'capture', self.logger, '127.0.0.1', 0)
        self.window.service = service
        service.start()
        self.wait_until(lambda: service.snapshot().state == 'listening')
        port = service.snapshot().udp_port
        payloads = []
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            for frame in range(400):
                data = bytearray(PACKET_SIZES[6])
                HEADER_STRUCT.pack_into(data, 0, 2023, 23, 1, 0, 1, 6, 77, frame / 60, frame, frame, 0, 255)
                payloads.append(bytes(data))
                sender.sendto(data, ('127.0.0.1', port))
        self.wait_until(lambda: service.snapshot().received_packets == 400)
        self.window.set_page(2)
        self.assertFalse(service.wait(0))
        self.window.close()
        self.wait_until(lambda: self.window.allow_close, timeout=20)
        snap = service.snapshot()
        self.assertEqual(snap.persisted_packets, 400)
        self.assertIsNone(snap.error)
        self.assertEqual([r.payload for r in iter_archive(snap.session_directory / 'raw_packets.bin')], payloads)

    def test_port_restart_drains_before_new_receiver_and_keeps_config_fields(self):
        service = CollectorService(self.settings, self.root / 'first', self.logger, '127.0.0.1', 0)
        self.window.service = service
        service.start()
        self.wait_until(lambda: service.snapshot().state == 'listening')
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        with patch.object(self.window, 'confirm', return_value=True):
            self.window.apply_connection(str(port), str(self.root / 'second'))
        self.assertTrue(self.window.restarting)
        self.wait_until(lambda: self.window.service is not service and self.window.service.snapshot().state == 'listening')
        self.assertTrue(service.wait(0))
        self.assertEqual(self.window.service.snapshot().udp_port, port)
        saved = load_settings(self.root / 'settings.json')
        self.assertEqual(saved.udp_port, port)
        self.assertEqual(saved.raw_compression, self.settings.raw_compression)
        self.assertEqual(saved.data_directory, str(self.root / 'second'))

    def test_close_during_connection_confirmation_does_not_save_or_restart(self):
        from unittest.mock import Mock
        service = Mock(settings=self.settings, port=20777, host='127.0.0.1')
        service.wait.return_value = False
        self.window.service = service
        before = (self.root / 'settings.json').read_bytes()
        def confirm(*args):
            self.window.close()
            return True
        with patch.object(self.window, 'confirm', side_effect=confirm):
            self.window.apply_connection('23001', str(self.root / 'output'))
        self.assertEqual((self.root / 'settings.json').read_bytes(), before)
        self.assertIsNone(self.window.pending_restart)
        service.wait.return_value = True

    def test_native_dialog_close_does_not_submit_compression(self):
        def select(*args):
            self.window.close()
            return str(self.root / 'raw.bin'), ''
        with patch('collector.qt_ui.main_window.QtWidgets.QFileDialog.getOpenFileName', side_effect=select), patch.object(self.window.operation, 'start') as start:
            self.window.choose_compress()
            start.assert_not_called()

    def test_refresh_preserves_same_pair_view_and_input_keys(self):
        from analysis.comparison import TracePoint
        trace = tuple(TracePoint(d, 0, 0, 0, 200, 210, .7, .8, .1, .2) for d in (0, 500, 1000))
        charts = self.window.analysis_page.charts
        model = SimpleNamespace(trace=trace)
        charts.set_model(model, 'same pair')
        charts.focus_region(200, 600)
        before = charts.plots[0].view.viewRange()[0]
        charts.set_model(model, 'same pair')
        self.assertEqual(charts.plots[0].view.viewRange()[0], before)
        editor = QtWidgets.QLineEdit('20777', self.window)
        editor.show()
        editor.setFocus()
        editor.setCursorPosition(4)
        QtTest.QTest.keyClick(editor, QtCore.Qt.Key.Key_Left)
        self.assertEqual(editor.cursorPosition(), 3)
        self.assertIsNone(charts.distance)

    def test_mouse_hover_reads_original_values_and_leave_clears(self):
        from analysis.comparison import TracePoint
        trace = tuple(TracePoint(d, 0, 0, 13.125, 200, 211.123, .71, .8135, .1, .2, 4, 5) for d in (0, 500, 1000))
        self.window.set_page(1)
        charts = self.window.analysis_page.charts
        charts.set_model(SimpleNamespace(trace=trace), 'hover pair')
        QtTest.QTest.qWait(40)
        plot = charts.plots[0]
        pos = plot.mapFromScene(plot.view.mapViewToScene(QtCore.QPointF(.5, .013125)))
        self.assertTrue(plot.viewport().rect().contains(pos), f'{pos} outside {plot.viewport().rect()}')
        QtTest.QTest.mouseMove(plot.viewport(), pos)
        self.app.processEvents()
        self.assertAlmostEqual(charts.distance, 500, delta=25)
        self.assertIn('211.123', str(self.window.analysis_page.cursor.rows_model.rows))
        self.app.sendEvent(plot, QtCore.QEvent(QtCore.QEvent.Type.Leave))
        self.assertIsNone(charts.distance)


if __name__ == '__main__':
    unittest.main()
