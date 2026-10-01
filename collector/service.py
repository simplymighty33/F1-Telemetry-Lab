"""Background collector service shared by the desktop UI."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import logging
from pathlib import Path
from queue import Empty, SimpleQueue
import struct
import threading
import time
from typing import Any

from collector.packet_capture import PacketCapture
from collector.foundation_worker import FoundationWorker
from collector.analysis_worker import AnalysisWorker
from collector.pipeline import CapturePipeline
from collector.settings import Settings
from collector.udp_receiver import UdpReceiver
from decoder.header import HeaderDecodeError, decode_header
from decoder.full_parser import PacketDecodeError, decode_packet
from decoder.protocol import game_label
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
        self._lap_events: SimpleQueue[LapHistoryUpdate] = SimpleQueue()
        self._state = "starting"
        self._message = "正在启动采集器"
        self._error: str | None = None
        self._received_packets = 0
        self._last_packet_at_ns: int | None = None
        self._recent_packet_times: deque[float] = deque()
        self._capture: PacketCapture | None = None
        self._pipeline: CapturePipeline | None = None
        self._foundation: FoundationWorker | None = None
        self._analysis: AnalysisWorker | None = None
        self._game_mode = "等待 Session 数据"
        self._session_type = "—"
        self._track_name = "—"
        self._game_name = "等待数据"

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
        updates: list[LapHistoryUpdate] = []
        while True:
            try:
                updates.append(self._lap_events.get_nowait())
            except Empty:
                return updates

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
                session_directory=capture.session_directory if capture else None,
                error=self._error,
                game_mode=self._game_mode,
                session_type=self._session_type,
                track_name=self._track_name,
                game_name=self._game_name,
                foundation_state=self._foundation.snapshot().state if self._foundation else "disabled",
                foundation_error=self._foundation.snapshot().error if self._foundation else None,
                analysis_state=self._analysis.snapshot().state if self._analysis else "disabled",
                analysis_error=self._analysis.snapshot().error if self._analysis else None,
            )

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
            capture = PacketCapture(
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
                self._foundation = FoundationWorker(
                    capture.session_directory / "raw_packets.bin",
                    lambda: capture.raw_writer.persisted_file_bytes,
                    self.logger,
                )
                self._foundation.start()
                if self.settings.analysis_enabled:
                    self._analysis = AnalysisWorker(capture.session_directory / "raw_packets.bin", self.logger)
                    self._analysis.start()
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
                            capture.track = session["track_name"]
                            capture.session_type = session["session_type_name"]
                    history_update = tracker.observe(
                        datagram.payload, datagram.received_at_ns, header
                    )
                except (HeaderDecodeError, PacketDecodeError, ValueError, struct.error):
                    history_update = None
                if history_update is not None:
                    self._lap_events.put(history_update)
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
            if self._analysis is not None:
                self._analysis.close()
            if failure is not None:
                detail = f"{type(failure).__name__}: {failure}"
                self._set_state("error", "采集发生错误；窗口将保持打开", detail)
            else:
                self._set_state("stopped", "采集已安全停止")
            self.completed.set()
