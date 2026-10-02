"""Background collector service shared by the desktop UI."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import logging
from pathlib import Path
import shutil
import struct
import threading
import time
from typing import Any

from collector.session_capture import SessionCapture
from collector.session_workers import SessionDerivedWorker
from collector.pipeline import CapturePipeline
from collector.settings import Settings
from collector.udp_receiver import UdpReceiver
from collector.health import resource_decision, process_memory_bytes
from decoder.header import HeaderDecodeError, decode_header
from decoder.full_parser import PacketDecodeError, decode_packet
from decoder.protocol import game_label
from decoder.stream import validate_packet
from decoder.session_history import LapHistoryUpdate, PlayerSessionHistoryTracker


@dataclass(frozen=True, slots=True)
class CollectorSnapshot:
    state: str
    message: str
    received_packets: int
    persisted_packets: int
    packet_rate_hz: float
    last_packet_at_ns: int | None
    queue_size: int
    queue_capacity: int
    session_directory: Path | None
    error: str | None
    game_mode: str
    session_type: str
    track_name: str
    game_name: str = "等待数据"
    foundation_state: str = "disabled"
    foundation_error: str | None = None
    analysis_state: str = "disabled"
    analysis_error: str | None = None
    health_warnings: tuple[str, ...] = ()
    session_uid: int | None = None
    player_car_index: int | None = None
    packet_format: int | None = None
    udp_port: int | None = None
    display_error: str | None = None


class CollectorService:
    def __init__(
        self,
        settings: Settings,
        data_directory: Path,
        logger: logging.Logger,
        host: str | None = None,
        port: int | None = None,
    ) -> None:
        self.settings = settings
        self.data_directory = data_directory
        self.logger = logger
        self.host = host or settings.bind_host
        self.port = settings.udp_port if port is None else port
        self._lock = threading.Lock()
        self._stop_requested = threading.Event()
        self.completed = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="collector-runtime",
            daemon=False,
        )
        self._latest_lap_update: LapHistoryUpdate | None = None
        self._pressure = threading.Event()
        self._health_stop = threading.Event()
        self._health_thread = None
        self._health_warnings = ()
        self._resource_error = None
        self._state = "starting"
        self._message = "正在启动采集器"
        self._error: str | None = None
        self._received_packets = 0
        self._last_packet_at_ns: int | None = None
        self._recent_packet_times: deque[float] = deque(maxlen=8192)
        self._capture: SessionCapture | None = None
        self._pipeline: CapturePipeline | None = None
        self._foundation: SessionDerivedWorker | None = None
        self._game_mode = "等待 Session 数据"
        self._session_type = "—"
        self._track_name = "—"
        self._game_name = "等待数据"
        self._game_context = None
        self._bound_port = None
        self._display_failures = 0
        self._display_error = None

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop_requested.set()
        with self._lock:
            if self._state not in {"error", "stopped"}:
                self._state = "stopping"
                self._message = "正在保存数据并安全停止"

    def wait(self, timeout: float | None = None) -> bool:
        started = time.monotonic()
        if not self.completed.wait(timeout):
            return False
        remaining = None if timeout is None else max(0.0, timeout - (time.monotonic() - started))
        self._thread.join(remaining)
        return not self._thread.is_alive()

    def drain_lap_updates(self) -> list[LapHistoryUpdate]:
        # History updates are complete snapshots, not individual lap events.
        # Coalesce only UI state; every packet and authoritative lap stays on disk.
        with self._lock:
            update = self._latest_lap_update
            self._latest_lap_update = None
        return [update] if update is not None else []

    def _monitor_resources(self):
        previous = ()
        stats = {"checks": 0, "pressure_checks": 0, "queue_peak": 0, "foundation_lag_peak": 0,
                 "analysis_lag_peak": 0, "min_free_bytes": None, "disk_stop": False, "disk_check_failures": 0}
        while not self._health_stop.is_set():
            try:
                free = shutil.disk_usage(self.data_directory).free
            except OSError:
                free = None
            foundation = self._foundation.snapshot() if self._foundation else None
            analysis = self._foundation.analysis_snapshot() if self._foundation else None
            queued = self._pipeline.queue_size
            current = self._capture.active_capture(ready_only=True)
            foundation_lag = max(0, current.packet_count - foundation.packets) if foundation and current else 0
            analysis_lag = max(0, foundation.packets - analysis.packets) if foundation and analysis else 0
            decision = resource_decision(queued, self.settings.queue_capacity, free,
                paused=self._pressure.is_set(),
                foundation_lag=foundation_lag, analysis_lag=analysis_lag)
            stats["checks"] += 1
            memory = process_memory_bytes()
            stats["memory_checks"] = stats.get("memory_checks", 0) + int(memory is not None)
            if memory is not None:
                for name, value in memory.items():
                    stats.setdefault("memory_start_" + name, value)
                    stats["memory_last_" + name] = value
                    stats["memory_peak_" + name] = max(stats.get("memory_peak_" + name, 0), value)
            stats["pressure_checks"] += int(decision.pause_derived)
            stats["queue_peak"] = max(stats["queue_peak"], queued)
            stats["foundation_lag_peak"] = max(stats["foundation_lag_peak"], foundation_lag)
            stats["analysis_lag_peak"] = max(stats["analysis_lag_peak"], analysis_lag)
            stats["disk_stop"] |= decision.stop_capture
            if free is None:
                stats["disk_check_failures"] += 1
            else:
                stats["min_free_bytes"] = min(stats["min_free_bytes"], free) if stats["min_free_bytes"] is not None else free
            self._capture.resource_health = dict(stats)
            self._pressure.set() if decision.pause_derived else self._pressure.clear()
            with self._lock:
                self._health_warnings = decision.warnings
                if decision.stop_capture:
                    self._resource_error = "磁盘可用空间低于安全预留，已停止接收并保存队列内数据"
                    self._stop_requested.set()
            if decision.warnings != previous:
                if decision.warnings:
                    self.logger.warning("Collector health: %s", "; ".join(decision.warnings))
                elif previous:
                    self.logger.info("Collector resource pressure cleared")
                previous = decision.warnings
            self._health_stop.wait(1.0)

    def snapshot(self) -> CollectorSnapshot:
        now = time.monotonic()
        with self._lock:
            while self._recent_packet_times and now - self._recent_packet_times[0] > 2.0:
                self._recent_packet_times.popleft()
            rate = 0.0
            if len(self._recent_packet_times) >= 2:
                span = self._recent_packet_times[-1] - self._recent_packet_times[0]
                if span > 0:
                    rate = (len(self._recent_packet_times) - 1) / span
            pipeline = self._pipeline
            capture = self._capture
            return CollectorSnapshot(
                state=self._state,
                message=self._message,
                received_packets=self._received_packets,
                persisted_packets=capture.packet_count if capture else 0,
                packet_rate_hz=rate,
                last_packet_at_ns=self._last_packet_at_ns,
                queue_size=pipeline.queue_size if pipeline else 0,
                queue_capacity=self.settings.queue_capacity,
                session_directory=(capture.directory_for(self._game_context[0], self._game_context[2])
                                   if capture and self._game_context else capture.session_directory if capture else None),
                error=self._error,
                game_mode=self._game_mode,
                session_type=self._session_type,
                track_name=self._track_name,
                game_name=self._game_name,
                foundation_state=self._foundation.snapshot().state if self._foundation else "disabled",
                foundation_error=self._foundation.snapshot().error if self._foundation else None,
                analysis_state=self._foundation.analysis_snapshot().state if self._foundation else "disabled",
                analysis_error=self._foundation.analysis_snapshot().error if self._foundation else None,
                health_warnings=self._health_warnings,
                session_uid=self._game_context[0] if self._game_context else None,
                player_car_index=self._game_context[1] if self._game_context else None,
                packet_format=self._game_context[2] if self._game_context else None,
                udp_port=self._bound_port,
                display_error=self._display_error,
            )

    def _observe_context(self, header):
        key = (header.session_uid, header.player_car_index, header.packet_format)
        with self._lock:
            if key != self._game_context:
                self._game_context = key
                self._track_name, self._session_type = "—", "—"
                self._game_mode = "等待 Session 数据"
                self._latest_lap_update = LapHistoryUpdate(header.session_uid, ())

    def _set_state(self, state: str, message: str, error: str | None = None) -> None:
        with self._lock:
            self._state = state
            self._message = message
            self._error = error

    def _record_packet(self, received_at_ns: int) -> None:
        now = time.monotonic()
        with self._lock:
            self._received_packets += 1
            self._last_packet_at_ns = received_at_ns
            self._recent_packet_times.append(now)
            if self._state == "listening":
                self._state = "receiving"
                self._message = "正在接收 UDP 遥测数据"

    def _run(self) -> None:
        receiver = UdpReceiver(
            self.host,
            self.port,
            receive_buffer_bytes=self.settings.receive_buffer_bytes,
            timeout_seconds=self.settings.receiver_timeout_seconds,
        )
        tracker = PlayerSessionHistoryTracker()
        failure: BaseException | None = None
        summary: dict[str, Any] | None = None
        try:
            receiver.open()
            self._bound_port = receiver.bound_port
            capture = SessionCapture(
                self.data_directory,
                receiver.bound_port,
                database_batch_size=self.settings.database_batch_size,
                raw_flush_every=self.settings.raw_flush_every,
                metadata_checkpoint_every=self.settings.metadata_checkpoint_every,
                raw_compression=self.settings.raw_compression,
                raw_compression_level=self.settings.raw_compression_level,
                raw_block_bytes=self.settings.raw_block_bytes,
            )
            pipeline = CapturePipeline(
                capture,
                capacity=self.settings.queue_capacity,
                put_timeout_seconds=self.settings.queue_put_timeout_seconds,
            )
            with self._lock:
                self._capture = capture
                self._pipeline = pipeline
            pipeline.start()
            if self.settings.foundation_enabled:
                self._foundation = SessionDerivedWorker(
                    capture, self.logger,
                    analysis_enabled=self.settings.analysis_enabled,
                    pressure=self._pressure.is_set,
                )
                self._foundation.start()
            self._health_thread = threading.Thread(target=self._monitor_resources, name="collector-health", daemon=False)
            self._health_thread.start()
            self.logger.info("Desktop collector listening on %s:%s", self.host, receiver.bound_port)
            self.logger.info("Recording: %s", capture.session_directory)
            self._set_state("listening", f"正在监听 UDP {receiver.bound_port}，等待游戏数据")

            while not self._stop_requested.is_set():
                datagram = receiver.receive()
                if datagram is None:
                    pipeline.raise_if_failed()
                    continue
                pipeline.submit(datagram)
                self._record_packet(datagram.received_at_ns)
                try:
                    header = decode_header(datagram.payload)
                    validate_packet(datagram.payload, header)
                    self._observe_context(header)
                    detected_game = game_label(header)
                    with self._lock:
                        self._game_name = detected_game
                        if self._state == "receiving":
                            self._message = f"正在接收 {detected_game} 遥测数据"
                    if header.packet_id == 1:
                        session = decode_packet(datagram.payload)
                        with self._lock:
                            self._game_mode = session["game_mode_name"]
                            self._session_type = session["session_type_name"]
                            self._track_name = session["track_name"]
                    history_update = tracker.observe(
                        datagram.payload, datagram.received_at_ns, header
                    )
                except (HeaderDecodeError, PacketDecodeError, ValueError, struct.error):
                    history_update = None
                except Exception:
                    # Display/reducer faults cannot justify stopping Raw reception.
                    self._display_failures += 1
                    with self._lock:
                        self._display_error = "圈速显示曾发生异常；Raw采集仍继续，请停止后重新分析核验。"
                    if self._display_failures & (self._display_failures - 1) == 0:
                        self.logger.exception("Display decoding failed (%s); Raw capture continues",
                                              self._display_failures)
                    history_update = None
                if history_update is not None:
                    with self._lock:
                        self._latest_lap_update = history_update
                    if history_update.laps:
                        latest = history_update.laps[-1]
                        self.logger.info(
                            "Authoritative lap history updated: lap %s, %s ms",
                            latest.lap_number,
                            latest.lap_time_ms,
                        )
                pipeline.raise_if_failed()
        except BaseException as exc:
            failure = exc
            self.logger.exception("Collector service failed: %s", exc)
        finally:
            receiver.close()
            self._health_stop.set()
            if self._health_thread is not None:
                self._health_thread.join()
            if failure is None and self._resource_error is not None:
                failure = RuntimeError(self._resource_error)
            pipeline = self._pipeline
            if pipeline is not None:
                try:
                    summary = pipeline.close("error" if failure else "complete")
                except BaseException as exc:
                    if failure is None:
                        failure = exc
                    self.logger.exception("Collector shutdown failed: %s", exc)
            if summary is not None:
                self.logger.info(
                    "Session closed: packets=%s, status=%s, queue_peak=%s",
                    summary["packet_count"],
                    summary["status"],
                    pipeline.peak_queue_size if pipeline else 0,
                )
            if self._foundation is not None:
                self._foundation.close()
            if failure is not None:
                detail = f"{type(failure).__name__}: {failure}"
                self._set_state("error", "采集发生错误；窗口将保持打开", detail)
            else:
                self._set_state("stopped", "采集已安全停止")
            self.completed.set()
