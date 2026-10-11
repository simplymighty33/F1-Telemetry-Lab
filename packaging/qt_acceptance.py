"""Explicit isolated GUI acceptance harness, never attach to user recordings."""
import argparse
import ctypes
import hashlib
import json
import logging
import os
from pathlib import Path
import platform
import statistics
import sys
import time
from dataclasses import replace

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtTest import QTest
import numpy
import pyqtgraph

from collector.qt_ui.main_window import MainWindow
from collector.qt_ui.theme import install, font
from collector.qt_ui.self_test import typography_evidence, check_typography
from collector.service import CollectorService
from collector.settings import load_settings


def pump(app, predicate, timeout=30):
    end = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > end:
            raise TimeoutError('Acceptance wait expired')
        app.processEvents()
        time.sleep(.005)
    # Worker completion precedes deferred layout requests. Let the actual UI
    # settle before measuring/grabbing, including page and word-wrap changes.
    QTest.qWait(80)


def awareness():
    if os.name != 'nt':
        return {'platform': sys.platform}
    user = ctypes.WinDLL('user32', use_last_error=True)
    user.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    user.AreDpiAwarenessContextsEqual.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    context = user.GetThreadDpiAwarenessContext()
    return {'per_monitor_v2': bool(user.AreDpiAwarenessContextsEqual(context, ctypes.c_void_p(-4 & ((1 << 64) - 1))))}


def text_geometry(window):
    """Shaped glyph bounds, not a whole-font line box used as a clipping proxy."""
    rows = []
    for item in window.findChildren(QtWidgets.QLabel):
        if not item.isVisible() or not item.text() or item.textFormat() != QtCore.Qt.TextFormat.PlainText:
            continue
        available = item.contentsRect()
        height = 0.
        layouts, line_count = [], 0
        # QLabel treats newlines as paragraph boundaries. A single QTextLayout
        # would silently mismeasure those breaks and explicit blank lines.
        for paragraph in item.text().split('\n'):
            if not paragraph:
                height += QtGui.QFontMetricsF(item.font()).height()
                line_count += 1
                continue
            layout = QtGui.QTextLayout(paragraph, item.font())
            option = QtGui.QTextOption()
            option.setWrapMode(QtGui.QTextOption.WrapMode.WordWrap if item.wordWrap() else QtGui.QTextOption.WrapMode.NoWrap)
            layout.setTextOption(option)
            layout.beginLayout()
            while True:
                line = layout.createLine()
                if not line.isValid():
                    break
                line.setLineWidth(available.width())
                x = 0.
                if item.alignment() & QtCore.Qt.AlignmentFlag.AlignHCenter:
                    x = (available.width() - line.naturalTextWidth()) / 2
                elif item.alignment() & QtCore.Qt.AlignmentFlag.AlignRight:
                    x = available.width() - line.naturalTextWidth()
                line.setPosition(QtCore.QPointF(x, height))
                height += line.height()
                line_count += 1
            layout.endLayout()
            layouts.append(layout)
        origin_y = available.top()
        if item.alignment() & QtCore.Qt.AlignmentFlag.AlignVCenter:
            origin_y += (available.height() - height) / 2
        families, missing, ink = set(), False, QtCore.QRectF()
        for layout in layouts:
            for run in layout.glyphRuns():
                raw = run.rawFont()
                families.add(raw.familyName())
                for glyph, pos in zip(run.glyphIndexes(), run.positions()):
                    missing |= glyph == 0
                    bounds = raw.boundingRect(glyph).translated(pos + QtCore.QPointF(available.left(), origin_y))
                    if not bounds.isEmpty():
                        ink = ink.united(bounds)
        fits = QtCore.QRectF(available).adjusted(-.5, -.5, .5, .5).contains(ink)
        rows.append({'text': item.text(), 'role': item.property('typeRole'),
                     'line_count': line_count, 'layout_height': height,
                     'families': sorted(families), 'missing_glyph': missing,
                     'ink_bounds': [ink.x(), ink.y(), ink.width(), ink.height()],
                     'available': [available.x(), available.y(), available.width(), available.height()],
                     'fits': fits})
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--width', type=int, default=1366)
    parser.add_argument('--height', type=int, default=768)
    parser.add_argument('--benchmark', action='store_true')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    QtGui.QGuiApplication.setHighDpiScaleFactorRoundingPolicy(QtCore.Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QtWidgets.QApplication([])
    if app.platformName() == 'offscreen' and os.name == 'nt':
        # The Windows offscreen plugin does not enumerate installed fonts. Test
        # rendering registers the existing system files; never redistribute them.
        for name in ('segoeui.ttf', 'segoeuib.ttf', 'seguisb.ttf', 'msyh.ttc', 'msyhbd.ttc', 'consola.ttf'):
            QtGui.QFontDatabase.addApplicationFont(str(Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / name))
    install(app)
    # Read-only historical database; every test output lives under --output.
    before = hashlib.sha256(args.database.read_bytes()).hexdigest()
    window = MainWindow(None, logging.getLogger('qt-acceptance'), args.output / 'scratch', args.output / 'unused-config.json', args.database)
    if os.environ.get('QT_QPA_PLATFORM') != 'offscreen':
        window.setAttribute(QtCore.Qt.WidgetAttribute.WA_DontShowOnScreen)
    window.resize(args.width, args.height)
    window.show()
    pump(app, lambda: window.controller.catalog is not None)
    choices = window.controller.catalog.sessions
    selected = max(choices, key=lambda s: s.lap_count)
    window.controller.set_scope(selected.session_uid, None)
    pump(app, lambda: window.analysis_page.model is not None)
    model = window.analysis_page.model
    for _ in range(5):
        app.processEvents()
    window.grab().save(str(args.output / 'analysis.png'))
    text_states = {'analysis': text_geometry(window)}
    window.analysis_page.charts.inspect(model.trace[len(model.trace) // 2].distance_m)
    app.processEvents()
    window.grab().save(str(args.output / 'cursor.png'))
    text_states['cursor'] = text_geometry(window)
    window.analysis_page.charts.clear_cursor()
    window.show_practice()
    pump(app, lambda: window.practice_page.report is not None or (window.practice_operation.ready() and window.practice_page.status.text().startswith(('ValueError', 'KeyError'))))
    window.grab().save(str(args.output / 'practice.png'))
    text_states['practice'] = text_geometry(window)
    for index in range(1, window.practice_page.tabs.count()):
        window.practice_page.tabs.setCurrentIndex(index)
        pump(app, lambda: window.practice_page.tabs.currentIndex() == index)
        window.grab().save(str(args.output / f'practice-tab-{index}.png'))
        text_states[f'practice_tab_{index}'] = text_geometry(window)
    window.practice_page.tabs.setCurrentIndex(0)
    window.set_page(0)
    pump(app, lambda: window.stack.currentIndex() == 0)
    window.grab().save(str(args.output / 'empty-capture.png'))
    text_states['empty_capture'] = text_geometry(window)
    snapshot = CollectorService(load_settings(args.output / 'scratch/settings.json'),
                                args.output / 'unused-data', logging.getLogger('isolated-render')).snapshot()
    window.capture_page.update_snapshot(replace(snapshot, foundation_decode_errors=10641))
    pump(app, lambda: window.capture_page.warning.isVisible())
    window.grab().save(str(args.output / 'decode-warning.png'))
    text_states['decode_warning'] = text_geometry(window)
    window.set_page(1)
    timing = {}
    if args.benchmark:
        charts = window.analysis_page.charts
        timings = []
        # A visible-update proxy via forced raster repaint, not claimed real desktop latency.
        for i in range(120):
            point = model.trace[i * (len(model.trace) - 1) // 119]
            start = time.perf_counter()
            charts.inspect(point.distance_m)
            for plot in charts.plots:
                plot.viewport().repaint()
            app.processEvents()
            timings.append((time.perf_counter() - start) * 1000)
        timing = {'cursor_samples': len(timings), 'median_ms': statistics.median(timings),
                  'p95_ms': sorted(timings)[113], 'max_ms': max(timings),
                  'scope': 'offscreen/native hidden raster repaint proxy, not physical display latency'}
    typography = typography_evidence(window)
    fonts = {key: QtGui.QFontInfo(font(role)).family() for key, role in
             [('body', 'body'), ('timing', 'timing_compact'), ('ticks', 'axis')]}
    curves = [{'field': field, 'AA': curve.opts['antialias'], 'width_px': curve.opts['pen'].widthF(),
               'cosmetic': curve.opts['pen'].isCosmetic(), 'autoDownsample': curve.opts['autoDownsample']}
              for plot, curve, field in window.analysis_page.charts.items]
    timing_fit = typography['timing_text_fit']
    plot_lefts = [p.mapTo(window, QtCore.QPoint(0, 0)).x() + p.view.sceneBoundingRect().left()
                  for p in window.analysis_page.charts.plots]
    stop_bounds = QtCore.QRect(window.stop_button.mapTo(window.centralWidget(), QtCore.QPoint(0, 0)), window.stop_button.size())
    checks = {'stop_visible': window.stop_button.isVisible(), 'real_trace_points': len(model.trace),
              'application_name': app.applicationName(), 'window_title': window.windowTitle(),
              'icon_sizes': [size.width() for size in window.windowIcon().availableSizes()],
              'stop_inside_client': window.centralWidget().rect().contains(stop_bounds),
              'timing_text_fit': timing_fit, 'plot_lefts_DIP': plot_lefts,
              'details_folded': window.analysis_page.details_button.isVisible(),
              'navigation_width_DIP': window.rail.width(), 'analysis_width_DIP': window.analysis_page.right.width(),
              'platform': app.platformName(),
              'plot_axis_widths': [p.getAxis('left').width() for p in window.analysis_page.charts.plots],
              'logical_size': [window.width(), window.height()], 'DPR': window.devicePixelRatioF(),
              'physical_size': [window.grab().width(), window.grab().height()], 'fonts': fonts, **typography,
              'curves': curves, 'awareness': awareness(), 'timing': timing, 'text_geometry': text_states,
              'python': platform.python_version(), 'qt': QtCore.qVersion(),
              'pyside': __import__('PySide6').__version__, 'pyqtgraph': pyqtgraph.__version__, 'numpy': numpy.__version__,
              'source_session': model.reference.session_uid,
              'source_sha256': before, 'source_unchanged': hashlib.sha256(args.database.read_bytes()).hexdigest() == before}
    window.close()
    pump(app, lambda: window.allow_close)
    checks['safe_close'] = True
    (args.output / 'evidence.json').write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding='utf-8')
    assert checks['stop_inside_client']
    assert checks['source_unchanged']
    check_typography(typography)
    assert all(row['fits'] and not row['missing_glyph'] for rows in text_states.values() for row in rows), text_states
    assert max(plot_lefts) - min(plot_lefts) <= 1, plot_lefts
    print(json.dumps(checks, ensure_ascii=True))


if __name__ == '__main__':
    main()
