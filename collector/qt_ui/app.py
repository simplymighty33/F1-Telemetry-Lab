"""Explicit optional Qt entry. Does not import Tk or change core CLI entrypoints."""
import argparse
from pathlib import Path
import sys

from PySide6 import QtCore, QtGui, QtWidgets

from collector.logging_setup import configure_logging
from collector.runtime import application_root, resolve_runtime_path
from collector.service import CollectorService
from collector.settings import load_settings
from .theme import install
from .main_window import MainWindow
from .branding import APP_NAME


def main(argv=None):
    parser = argparse.ArgumentParser(description=f'{APP_NAME} · Qt')
    parser.add_argument('--config', type=Path)
    parser.add_argument('--host')
    parser.add_argument('--port', type=int)
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--log-dir', type=Path)
    parser.add_argument('--analysis', action='store_true')
    parser.add_argument('--analysis-db', type=Path)
    parser.add_argument('--self-test-output', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--self-test-database', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--self-test-format', type=int, choices=(2023, 2026), default=2023, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.self_test_output:
        from .self_test import run
        return run(args.self_test_output, args.self_test_database, args.self_test_format)
    QtGui.QGuiApplication.setHighDpiScaleFactorRoundingPolicy(QtCore.Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([sys.argv[0]])
    install(app)
    root = application_root()
    config = args.config.expanduser().resolve() if args.config else root / 'config' / 'settings.json'
    logger = None
    try:
        settings = load_settings(config)
        if args.port is not None and not 0 <= args.port <= 65535:
            raise ValueError('UDP port must be between 0 and 65535')
        data = resolve_runtime_path(args.data_dir or settings.data_directory, root)
        logs = resolve_runtime_path(args.log_dir or settings.log_directory, root)
        data.mkdir(parents=True, exist_ok=True)
        logger = configure_logging(logs, settings.log_max_bytes, settings.log_backup_count, console_enabled=False)
        from .theme import font
        logger.info('Qt %s; platform=%s; body_font=%s; digits_font=%s', QtCore.qVersion(),
                    app.platformName(), QtGui.QFontInfo(font()).family(), QtGui.QFontInfo(font('axis')).family())
        service = None if args.analysis or args.analysis_db else CollectorService(settings, data, logger, host=args.host, port=args.port)
        window = MainWindow(service, logger, data, config, args.analysis_db)
        window.show()
    except Exception as exc:
        if logger:
            logger.exception('Qt startup failed')
        box = QtWidgets.QMessageBox()
        box.setWindowTitle(f'{APP_NAME} · 无法启动')
        box.setText(f'{type(exc).__name__}: {exc}\n请检查配置和日志；现有录制文件不会被删除。')
        box.exec()
        from collector.logging_setup import close_logging
        if logger:
            close_logging(logger)
        return 1
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
