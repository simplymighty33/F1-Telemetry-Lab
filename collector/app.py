"""Application orchestration for source and frozen-executable operation."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
import signal
import sys
import threading
import time
from typing import Any

from collector import __version__, APP_NAME, DISPLAY_VERSION
from collector.logging_setup import close_logging, configure_logging
from collector.packet_capture import PacketCapture
from collector.foundation_worker import FoundationWorker
from collector.analysis_worker import AnalysisWorker
from collector.pipeline import CapturePipeline
from collector.runtime import application_root, resolve_runtime_path
from collector.settings import ConfigurationError, Settings, load_settings
from collector.udp_receiver import UdpReceiver
from collector.windows_console import WindowsConsoleCloseHandler


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Raw-first F1 23/24/25 UDP telemetry collector")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--config", type=Path, help="settings JSON path")
    parser.add_argument("--host", help="UDP bind address (default from settings)")
    parser.add_argument("--port", type=int, help="UDP port (default 20777)")
    parser.add_argument("--data-dir", type=Path, help="archive root directory")
    parser.add_argument("--log-dir", type=Path, help="log directory")
    parser.add_argument("--track", help="optional operator-provided track label")
    parser.add_argument("--session-type", help="optional session label, e.g. Time Trial")
    parser.add_argument(
        "--duration",
        type=float,
        help="stop after this many seconds; otherwise run until Ctrl+C",
    )
    parser.add_argument("--verbose", action="store_true", help="show debug logging")
    return parser


def _format_duration(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _log_summary(
    logger: logging.Logger,
    summary: dict[str, Any],
    session_directory: Path,
    peak_queue_size: int,
) -> None:
    logger.info("")
    logger.info("Session Summary")
    logger.info("Archive: %s", session_directory)
    logger.info("Duration: %s", _format_duration(float(summary["duration_seconds"])))
    logger.info("Packets: %s", summary["packet_count"])
    for label, values in summary["packet_types"].items():
        logger.info(
            "  %-24s %8s  %8.3f Hz",
            label,
            values["count"],
            float(values["average_hz"]),
        )
    logger.info(
        "Average total rate: %.3f packets/s",
        float(summary["average_total_packets_per_second"]),
    )
    logger.info("Peak writer queue: %s", peak_queue_size)
    if summary["parse_error_count"]:
        logger.warning("Unparsed packets: %s", summary["parse_error_count"])
    if summary["software_drop_count"]:
        logger.error("Software-dropped packets: %s", summary["software_drop_count"])


def _validated_port(value: int) -> int:
    if not 0 <= value <= 65_535:
        raise ConfigurationError("UDP port must be between 0 and 65535")
    return value


def _run(
    args: argparse.Namespace,
    settings: Settings,
    data_directory: Path,
    logger: logging.Logger,
) -> int:
    host = args.host or settings.bind_host
    port = _validated_port(args.port if args.port is not None else settings.udp_port)
    if args.duration is not None and args.duration <= 0:
        raise ConfigurationError("duration must be greater than zero")

    stop_requested = threading.Event()
    shutdown_complete = threading.Event()

    def request_stop(_signum: int, _frame: object) -> None:
        stop_requested.set()

    previous_sigint = signal.getsignal(signal.SIGINT)
    previous_sigterm = signal.getsignal(signal.SIGTERM) if hasattr(signal, "SIGTERM") else None
    signal.signal(signal.SIGINT, request_stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, request_stop)
    console_handler = WindowsConsoleCloseHandler(stop_requested, shutdown_complete)
    try:
        console_handler.register()
    except OSError as exc:
        logger.warning("Windows close-event handler unavailable: %s", exc)

    receiver = UdpReceiver(
        host,
        port,
        receive_buffer_bytes=settings.receive_buffer_bytes,
        timeout_seconds=settings.receiver_timeout_seconds,
    )
    pipeline: CapturePipeline | None = None
    foundation: FoundationWorker | None = None
    analysis: AnalysisWorker | None = None
    failure: BaseException | None = None
    summary: dict[str, Any] | None = None
    started = time.monotonic()
    next_status = started + settings.status_interval_seconds
    try:
        receiver.open()
        capture = PacketCapture(
            data_directory,
            receiver.bound_port,
            track=args.track,
            session_type=args.session_type,
            database_batch_size=settings.database_batch_size,
            raw_flush_every=settings.raw_flush_every,
            metadata_checkpoint_every=settings.metadata_checkpoint_every,
            raw_compression=settings.raw_compression,
            raw_compression_level=settings.raw_compression_level,
            raw_block_bytes=settings.raw_block_bytes,
        )
        pipeline = CapturePipeline(
            capture,
            capacity=settings.queue_capacity,
            put_timeout_seconds=settings.queue_put_timeout_seconds,
        )
        pipeline.start()
        if settings.foundation_enabled:
            foundation = FoundationWorker(
                capture.session_directory / "raw_packets.bin",
                lambda: capture.raw_writer.persisted_file_bytes,
                logger,
            )
            foundation.start()
            if settings.analysis_enabled:
                analysis = AnalysisWorker(capture.session_directory / "raw_packets.bin", logger)
                analysis.start()
        logger.info("=" * 52)
        logger.info(" %s %s", APP_NAME, DISPLAY_VERSION)
        logger.info("=" * 52)
        logger.info("Listening: %s:%s", host, receiver.bound_port)
        logger.info("Receive buffer: %s bytes", receiver.actual_receive_buffer_bytes)
        logger.info("Recording: %s", capture.session_directory)
        logger.info("Press Ctrl+C to stop cleanly.")

        while not stop_requested.is_set():
            if args.duration is not None and time.monotonic() - started >= args.duration:
                break
            datagram = receiver.receive()
            if datagram is not None:
                pipeline.submit(datagram)
            pipeline.raise_if_failed()
            now = time.monotonic()
            if now >= next_status:
                logger.info(
                    "Captured %s packets (writer queue %s/%s)...",
                    capture.packet_count,
                    pipeline.queue_size,
                    settings.queue_capacity,
                )
                next_status = now + settings.status_interval_seconds
    except KeyboardInterrupt:
        stop_requested.set()
    except BaseException as exc:
        failure = exc
    finally:
        receiver.close()
        if pipeline is not None:
            try:
                summary = pipeline.close("error" if failure else "complete")
            except BaseException as exc:
                if failure is None:
                    failure = exc
            if summary is not None:
                _log_summary(
                    logger,
                    summary,
                    pipeline.capture.session_directory,
                    pipeline.peak_queue_size,
                )
        if foundation is not None:
            foundation.close()
        if analysis is not None:
            analysis.close()
        shutdown_complete.set()
        console_handler.close()
        signal.signal(signal.SIGINT, previous_sigint)
        if hasattr(signal, "SIGTERM") and previous_sigterm is not None:
            signal.signal(signal.SIGTERM, previous_sigterm)

    if failure is not None:
        raise failure
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = application_root()
    logger: logging.Logger | None = None
    config_path = (
        args.config.expanduser().resolve()
        if args.config is not None
        else root / "config" / "settings.json"
    )
    try:
        settings = load_settings(config_path)
        data_directory = resolve_runtime_path(
            args.data_dir if args.data_dir is not None else settings.data_directory,
            root,
        )
        log_directory = resolve_runtime_path(
            args.log_dir if args.log_dir is not None else settings.log_directory,
            root,
        )
        data_directory.mkdir(parents=True, exist_ok=True)
        logger = configure_logging(
            log_directory,
            settings.log_max_bytes,
            settings.log_backup_count,
            args.verbose,
        )
        logger.debug("Application root: %s", root)
        logger.debug("Settings: %s", config_path)
        return _run(args, settings, data_directory, logger)
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        if logger is not None and logger.handlers:
            logger.exception("Collector stopped because of an error: %s", exc)
        else:
            print(f"Collector error: {exc}", file=sys.stderr)
        return 1
    finally:
        if logger is not None:
            close_logging(logger)
