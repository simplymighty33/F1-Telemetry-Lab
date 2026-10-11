"""Isolated real Qt tools workflow. No historical/user archive is written."""
import argparse
import hashlib
import json
import logging
from pathlib import Path
import time
from unittest.mock import patch

from PySide6 import QtCore, QtWidgets
from collector.packet_capture import PacketCapture
from collector.udp_receiver import ReceivedDatagram
from decoder.header import HEADER_STRUCT
from decoder.full_parser import PACKET_SIZES
from storage.archive_tools import verify_archives
from collector.qt_ui.main_window import MainWindow
from collector.qt_ui.theme import install


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    app = QtWidgets.QApplication([])
    install(app)
    capture = PacketCapture(args.output / 'data', 20777)
    for frame in range(100):
        payload = bytearray(PACKET_SIZES[6])
        HEADER_STRUCT.pack_into(payload, 0, 2023, 23, 1, 0, 1, 6, 787878, frame / 60, frame, frame, 0, 255)
        capture.process(ReceivedDatagram(time.time_ns(), time.monotonic_ns(), '127.0.0.1', 20777, bytes(payload)))
    capture.close()
    source = capture.session_directory
    raw = source / 'raw_packets.bin'
    before = hashlib.sha256(raw.read_bytes()).hexdigest()
    window = MainWindow(None, logging.getLogger('qt-tools-acceptance'), source, args.output / 'unused-settings.json')
    window.setAttribute(QtCore.Qt.WidgetAttribute.WA_DontShowOnScreen)
    window.show()
    results = []
    window.show_result = lambda kind, result: results.append((kind, result))
    def pump(predicate):
        end = time.monotonic() + 40
        while not predicate():
            if time.monotonic() > end:
                raise TimeoutError(window.footer_message.text())
            app.processEvents()
            time.sleep(.005)
    window.start_build(source)
    pump(lambda: len(results) == 1)
    first_db = Path(results[-1][1]['analysis_database'])
    assert first_db.is_file()
    original_db = hashlib.sha256(first_db.read_bytes()).hexdigest()
    window.start_build(source, rebuild=True)
    pump(lambda: len(results) == 2)
    assert Path(results[-1][1]['analysis_database']) != first_db
    assert hashlib.sha256(first_db.read_bytes()).hexdigest() == original_db
    target = source / 'verified-copy.bin'
    with patch('collector.qt_ui.main_window.QtWidgets.QFileDialog.getOpenFileName', return_value=(str(raw), '')), patch(
            'collector.qt_ui.main_window.QtWidgets.QFileDialog.getSaveFileName', return_value=(str(target), '')):
        window.choose_compress()
    pump(lambda: len(results) == 3)
    assert verify_archives(raw, target)['verified_packet_count'] == 100
    with patch('collector.qt_ui.main_window.QtWidgets.QFileDialog.getExistingDirectory', return_value=str(source)):
        window.choose_audit()
    pump(lambda: len(results) == 4)
    assert results[-1][1]['raw_crc_valid']
    assert results[-1][1]['header_roundtrip_mismatches'] == 0
    assert hashlib.sha256(raw.read_bytes()).hexdigest() == before
    window.close()
    pump(lambda: window.allow_close)
    result = {'scope': 'Qt tools on isolated synthetic 100-packet archive; not game endurance',
              'build_created_database': True, 'independent_rebuild_preserved_original': True,
              'compression_verified_packets': 100, 'raw_preserved': True,
              'audit_header_mismatches': 0, 'safe_close': True, 'results': results}
    (args.output / 'evidence.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k != 'results'}))


if __name__ == '__main__':
    main()
