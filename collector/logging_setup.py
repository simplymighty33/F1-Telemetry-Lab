"""Console and rotating-file logging for the collector application."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


LOGGER_NAME = "f1_telemetry_collector"


def configure_logging(
    log_directory: Path,
    max_bytes: int,
    backup_count: int,
    verbose: bool = False,
    console_enabled: bool = True,
) -> logging.Logger:
    log_directory.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    file_handler = RotatingFileHandler(
        log_directory / "collector.log",
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(threadName)s %(name)s: %(message)s"
        )
    )
    if console_enabled:
        console = logging.StreamHandler()
        console.setLevel(logging.DEBUG if verbose else logging.INFO)
        console.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(console)
    logger.addHandler(file_handler)
    return logger


def close_logging(logger: logging.Logger) -> None:
    """Flush and close application handlers, including on repeated test runs."""
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.flush()
        finally:
            handler.close()
