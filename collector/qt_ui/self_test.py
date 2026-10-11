"""Opt-in frozen-target smoke test using a new isolated local UDP receiver."""
import ctypes
import hashlib
import json
import logging
import os
from pathlib import Path
import platform
import socket
import sys
import time

from PySide6 import QtCore, QtGui, QtWidgets

from collector.service import CollectorService
from collector.settings import load_settings
from decoder.full_parser import PACKET_SIZES
from decoder.header import HEADER_STRUCT, decode_header
from storage.raw_archive import iter_archive
from .main_window import MainWindow
from .theme import TYPE, install, font
from .branding import APP_NAME, ICON_PATH
from collector import __version__, DISPLAY_VERSION
from decoder.full_parser import decode_packet
from decoder.stream import DECODER_VERSION


def font_record(value, role=None):
    """Requested and resolved faces; diagnosis only, never business state."""
    info = QtGui.QFontInfo(value)
    raw = QtGui.QRawFont.fromFont(value)
    os2 = bytes(raw.fontTable('OS/2'))
    tag = QtGui.QFont.Tag.fromString('tnum')
    return {'requested_role': role, 'requested_families': value.families(),
            'requested_pixel_size': value.pixelSize(), 'requested_weight': int(value.weight()),
            'resolved_family': info.family(), 'resolved_style_name': info.styleName(),
            'resolved_weight': int(info.weight()), 'raw_font_weight': raw.weight(),
            'font_face_weight_class': int.from_bytes(os2[4:6], 'big') if len(os2) >= 6 else None,
            'tnum_explicit': value.isFeatureSet(tag),
            'tnum_value': value.featureValue(tag) if value.isFeatureSet(tag) else None,
            'prefer_antialias': bool(value.styleStrategy() & QtGui.QFont.StyleStrategy.PreferAntialias)}


def typography_evidence(window):
    roles = {role: font_record(font(role), role) for role in TYPE}
    metrics = QtGui.QFontMetricsF(font('timing_display'))
    advances = {text: metrics.horizontalAdvance(text) for text in ('1:11.111', '8:88.888')}
    titles = [window.capture_page.page_title, window.analysis_page.scope, window.practice_page.heading]
    page_titles = [{'text': item.text(), 'pixel_size': item.font().pixelSize(),
                    'weight': int(item.font().weight()), 'typeRole': item.property('typeRole'),
                    **font_record(item.font(), item.property('typeRole'))} for item in titles]
    timing_fit = []
    for card in window.analysis_page.cards:
        item = card.value
        fm = QtGui.QFontMetricsF(item.font())
        ink = fm.tightBoundingRect(item.text())
        width = max(fm.horizontalAdvance(item.text()), ink.right()) - min(0., ink.left())
        timing_fit.append({'text': item.text(), 'role': item.property('typeRole'),
                           'weight': int(item.font().weight()), 'text_width': width,
                           'available_width': item.contentsRect().width(),
                           'remaining_width': item.contentsRect().width() - width,
                           'ink_height': ink.height(), 'available_height': item.contentsRect().height(),
                           **font_record(item.font(), item.property('typeRole'))})
    widgets = {'brand': window.brand, 'capture_header': window.capture_page.laps.horizontalHeader(),
               'analysis_header': window.analysis_page.cursor.horizontalHeader(),
               **{f'navigation_{i}': item for i, item in enumerate(window.nav)}}
    controls = {name: font_record(item.font(), item.property('typeRole')) for name, item in widgets.items()}
    return {'font_roles': roles, 'page_titles': page_titles, 'numeric_advances_DIP': advances,
            'tabular_width_delta': abs(advances['1:11.111'] - advances['8:88.888']),
            'timing_text_fit': timing_fit, 'control_fonts': controls}


def check_typography(evidence):
    assert evidence['tabular_width_delta'] <= .5, evidence['numeric_advances_DIP']
    assert all(card['remaining_width'] >= 8 for card in evidence['timing_text_fit']), evidence['timing_text_fit']
    assert all(card['ink_height'] <= card['available_height'] for card in evidence['timing_text_fit']), evidence['timing_text_fit']
    assert {title['pixel_size'] for title in evidence['page_titles']} == {20}
    assert all(title['typeRole'] == 'page_title' for title in evidence['page_titles'])
    for role, row in evidence['font_roles'].items():
        assert row['resolved_family'] == TYPE[role].families[0], (role, row)
        assert row['prefer_antialias']
        if TYPE[role].tabular:
            assert row['tnum_explicit'] and row['tnum_value'] == 1
            assert row['resolved_style_name'] == ('Regular' if role == 'axis' else 'Semibold'), (role, row)
    for name, row in evidence['control_fonts'].items():
        assert row['requested_weight'] == 600, (name, row)


def run(output, database=None, packet_format=2023):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    QtGui.QGuiApplication.setHighDpiScaleFactorRoundingPolicy(QtCore.Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    install(app)
    settings = load_settings(output / 'config' / 'settings.json')
    service = CollectorService(settings, output / 'data', logging.getLogger('isolated-self-test'), '127.0.0.1', 0)
    window = MainWindow(service, None, output / 'data', output / 'config' / 'settings.json')
    window.setAttribute(QtCore.Qt.WidgetAttribute.WA_DontShowOnScreen)
    window.show()
    def pump(predicate, timeout=40):
        end = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() > end:
                raise TimeoutError('Self-test timeout')
            app.processEvents()
            time.sleep(.005)
        from PySide6.QtTest import QTest
        QTest.qWait(80)
    error = None
    payloads = []
    analysis_evidence = None
    typography = None
    try:
        assert app.applicationName() == APP_NAME
        assert window.windowTitle() == f'{APP_NAME} {DISPLAY_VERSION}'
        assert window.brand.text() == APP_NAME
        assert not window.windowIcon().isNull()
        assert {16, 32, 48, 256}.issubset({size.width() for size in window.windowIcon().availableSizes()})
        pump(lambda: service.snapshot().state in ('listening', 'error'))
        if service.snapshot().state == 'error':
            raise RuntimeError(service.snapshot().error)
        port = service.snapshot().udp_port
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            for frame in range(400):
                # Literal verified wire lengths, not decoder-generated fixtures.
                data = bytearray(1448 if packet_format == 2026 else 1352)
                HEADER_STRUCT.pack_into(data, 0, packet_format, 25 if packet_format == 2026 else 23,
                                        1, 26 if packet_format == 2026 else 0, 1, 6, 818181, frame / 60, frame, frame, 0, 255)
                payloads.append(bytes(data))
                sender.sendto(data, ('127.0.0.1', port))
        pump(lambda: service.snapshot().received_packets == len(payloads))
        window.set_page(2)
        app.processEvents()
        capture_running_after_page_change = not service.wait(0)
        window.set_page(0)
        app.processEvents()
        screenshot = window.grab()
        screenshot.save(str(output / 'native-capture.png'))
        dpr = window.devicePixelRatioF()
        fonts = {'body': QtGui.QFontInfo(font('body')).family(), 'digits': QtGui.QFontInfo(font('axis')).family()}
        user = ctypes.WinDLL('user32', use_last_error=True)
        user.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        user.AreDpiAwarenessContextsEqual.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        pmv2 = bool(user.AreDpiAwarenessContextsEqual(user.GetThreadDpiAwarenessContext(), ctypes.c_void_p(-4)))
        if database is not None:
            database = Path(database).resolve()
            before = hashlib.sha256(database.read_bytes()).hexdigest()
            window.open_database(database)
            pump(lambda: window.analysis_page.model is not None)
            model = window.analysis_page.model
            typography = typography_evidence(window)
            check_typography(typography)
            window.analysis_page.charts.inspect(model.trace[len(model.trace) // 2].distance_m)
            app.processEvents()
            window.grab().save(str(output / 'native-analysis.png'))
            window.show_practice()
            pump(lambda: window.practice_page.report is not None)
            window.grab().save(str(output / 'native-practice.png'))
            analysis_evidence = {'trace_points': len(model.trace), 'reference_lap': model.reference.lap_number,
                                 'comparison_lap': model.comparison.lap_number,
                                 'practice_loaded': True, 'source_sha256': before,
                                 'source_unchanged': hashlib.sha256(database.read_bytes()).hexdigest() == before}
            if not analysis_evidence['source_unchanged']:
                raise AssertionError('Read-only historical source changed')
    except Exception as exc:
        error = repr(exc)
        capture_running_after_page_change, dpr, fonts, pmv2 = False, window.devicePixelRatioF(), {}, False
    finally:
        start = time.perf_counter()
        window.close()
        pump(lambda: window.allow_close)
        close_seconds = time.perf_counter() - start
    snap = service.snapshot()
    archive = snap.session_directory / 'raw_packets.bin' if snap.session_directory else None
    replay = list(iter_archive(archive)) if archive else []
    raw_equal = [p.payload for p in replay] == payloads
    headers_equal = [decode_header(p.payload) for p in replay] == [decode_header(p) for p in payloads]
    bodies_decoded = 0
    for packet in replay:
        decode_packet(packet.payload)
        bodies_decoded += 1
    result = {'scope': 'Actual app isolated synthetic UDP, Windows hidden window; not game endurance/mixed monitors',
              'frozen': bool(getattr(sys, 'frozen', False)), 'python': platform.python_version(), 'qt': QtCore.qVersion(),
              'platform': app.platformName(), 'DPR': dpr, 'fonts': fonts, 'per_monitor_v2': pmv2,
              'application_name': app.applicationName(), 'window_title': window.windowTitle(),
              'version': __version__, 'decoder_version': DECODER_VERSION, 'packet_format': packet_format,
              'decoded_bodies': bodies_decoded, 'foundation_decode_errors': snap.foundation_decode_errors,
              'icon_sizes': [size.width() for size in window.windowIcon().availableSizes()],
              'icon_sha256': hashlib.sha256(ICON_PATH.read_bytes()).hexdigest(),
              'sent': len(payloads), 'received': snap.received_packets, 'persisted': snap.persisted_packets,
              'raw_bytes_equal': raw_equal, 'replayed_headers_equal': headers_equal,
              'capture_running_after_page_change': capture_running_after_page_change,
              'safe_close_seconds': close_seconds, 'capture_error': snap.error, 'test_error': error,
              'historical_analysis': analysis_evidence,
              'typography': typography,
              'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest() if archive else None}
    (output / 'self-test.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if raw_equal and headers_equal and bodies_decoded == len(payloads) and not snap.foundation_decode_errors and not error and not snap.error and pmv2 else 1
