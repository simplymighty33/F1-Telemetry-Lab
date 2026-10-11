"""Bounded profiling of adapters/setData/cursor on isolated synthetic curves."""
import argparse
import cProfile
from dataclasses import replace
import json
import math
import os
from pathlib import Path
import platform
import pstats
import statistics
import time

from PySide6 import QtCore, QtGui, QtWidgets
from analysis.comparison import AnalysisRepository, TracePoint
from collector.qt_ui.charts import ChartStack
from collector.qt_ui.theme import install
from collector.qt_ui.adapters import channel_data


def distribution(values):
    return {'samples': len(values), 'median_ms': statistics.median(values),
            'p95_ms': sorted(values)[int(.95 * (len(values) - 1))], 'max_ms': max(values)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    QtGui.QGuiApplication.setHighDpiScaleFactorRoundingPolicy(QtCore.Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QtWidgets.QApplication([])
    if app.platformName() == 'offscreen' and os.name == 'nt':
        for name in ('segoeui.ttf', 'segoeuib.ttf', 'msyh.ttc', 'msyhbd.ttc'):
            QtGui.QFontDatabase.addApplicationFont(str(Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / name))
    install(app)
    repository = AnalysisRepository(args.database)
    session = max(repository.sessions(), key=lambda s: s.lap_count)
    laps = repository.laps(session.session_uid)
    model = repository.comparison(session.session_uid, laps[-2].lap_number, laps[-1].lap_number)
    charts = ChartStack()
    charts.resize(1020, 650)
    if app.platformName() == 'windows':
        charts.setAttribute(QtCore.Qt.WidgetAttribute.WA_DontShowOnScreen)
    charts.show()
    results = []
    for size in (len(model.trace), 2048, 32768):
        if size == len(model.trace):
            sample = model
            scope = 'historical read-only real lap'
        else:
            trace = tuple(TracePoint(i * 5800 / (size - 1), i * 40, i * 40 + math.sin(i / 80) * 100,
                                     math.sin(i / 80) * 100, 200 + 60 * math.sin(i / 20), 202 + 60 * math.sin(i / 20),
                                     .7, .72, .1, .11, 4, 4, i % 128 != 0, i % 257 != 0) for i in range(size))
            sample = replace(model, trace=trace)
            scope = 'synthetic performance fixture only; not game data'
        started = time.perf_counter()
        arrays = [channel_data(sample.trace, field) for _, _, field in charts.items]
        adapt_ms = (time.perf_counter() - started) * 1000
        started = time.perf_counter()
        charts.set_model(sample, ('benchmark', size))
        app.processEvents()
        set_data_ms = (time.perf_counter() - started) * 1000
        processing, repaint = [], []
        for i in range(100):
            d = sample.trace[i * (len(sample.trace) - 1) // 99].distance_m
            start = time.perf_counter()
            charts.inspect(d)
            processing.append((time.perf_counter() - start) * 1000)
            for p in charts.plots:
                p.viewport().repaint()
            app.processEvents()
            repaint.append((time.perf_counter() - start) * 1000)
        profile = cProfile.Profile()
        profile.enable()
        for i in range(100):
            charts.inspect(sample.trace[i * (size - 1) // 99].distance_m)
        profile.disable()
        profile.dump_stats(str(args.output / f'cursor-{size}.prof'))
        with (args.output / f'cursor-{size}.txt').open('w', encoding='utf-8') as stream:
            pstats.Stats(profile, stream=stream).sort_stats('cumulative').print_stats(12)
        results.append({'points': size, 'curves': len(charts.items), 'plots': 4, 'scope': scope,
                        'valid_segments': sum(p.valid and (i == 0 or not p.connected or not sample.trace[i - 1].valid)
                                              for i, p in enumerate(sample.trace)),
                        'adapt_ms': adapt_ms, 'set_data_and_initial_paint_ms': set_data_ms,
                        'cursor_processing': distribution(processing[10:]),
                        'forced_raster_repaint_proxy': distribution(repaint[10:])})
    evidence = {'platform': platform.platform(), 'processor': platform.processor(), 'cpu_count': os.cpu_count(),
                'python': platform.python_version(), 'Qt': QtCore.qVersion(), 'DPR': charts.devicePixelRatioF(),
                'logical_chart_size': [charts.width(), charts.height()], 'QPA': app.platformName(),
                'scope': 'Warm isolated processing/repaint proxy, not physical display latency or game endurance',
                'results': results}
    try:
        import psutil
        evidence['observed_final_process_rss_bytes'] = psutil.Process().memory_info().rss
    except ImportError:
        pass
    (args.output / 'benchmark.json').write_text(json.dumps(evidence, indent=2), encoding='utf-8')
    charts.close()
    print(json.dumps(evidence))


if __name__ == '__main__':
    main()
