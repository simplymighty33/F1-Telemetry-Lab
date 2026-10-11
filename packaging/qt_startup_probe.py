"""Opt-in typography smoke through the normal application entry, isolated paths."""
import argparse
import hashlib
import json
from pathlib import Path
import time

from PySide6 import QtCore, QtWidgets
from collector.qt_ui import app as entry
from collector.qt_ui.self_test import typography_evidence, check_typography


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    before = hashlib.sha256(args.database.read_bytes()).hexdigest()
    QtWidgets.QApplication.setHighDpiScaleFactorRoundingPolicy(QtCore.Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    application = QtWidgets.QApplication([])
    actual_window = entry.MainWindow
    windows, errors, proof = [], [], {}

    class HiddenWindow(actual_window):
        def __init__(self, *items, **options):
            super().__init__(*items, **options)
            self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DontShowOnScreen)
            windows.append(self)

    entry.MainWindow = HiddenWindow
    started = time.monotonic()

    def finish():
        if not windows:
            if time.monotonic() - started > 30:
                errors.append('normal entry did not create a window')
                application.exit(1)
            return
        window = windows[0]
        if window.closing:
            if window.allow_close:
                timer.stop()
                application.exit(0 if not errors else 1)
            return
        if window.analysis_page.model is not None:
            try:
                proof.update(typography_evidence(window))
                check_typography(proof)
            except Exception as exc:
                errors.append(repr(exc))
            window.close()
        elif time.monotonic() - started > 30:
            errors.append('normal analysis load timed out')
            window.close()

    timer = QtCore.QTimer()
    timer.timeout.connect(finish)
    timer.start(25)
    try:
        code = entry.main(['--analysis-db', str(args.database.resolve()), '--config', str(args.output / 'config/settings.json'),
                           '--data-dir', str(args.output / 'data'), '--log-dir', str(args.output / 'logs')])
    finally:
        entry.MainWindow = actual_window
    proof.update(exit_code=code, errors=errors, safe_close=bool(windows and windows[0].allow_close),
                 source_sha256=before, source_unchanged=hashlib.sha256(args.database.read_bytes()).hexdigest() == before,
                 scope='normal app.main, hidden native window, isolated config/log/data paths, read-only historical analysis')
    (args.output / 'startup-evidence.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding='utf-8')
    assert code == 0 and not errors and proof['safe_close'] and proof['source_unchanged'], proof


if __name__ == '__main__':
    main()
