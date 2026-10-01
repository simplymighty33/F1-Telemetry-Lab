"""Bounded producer/consumer pipeline between UDP reception and disk I/O."""

from __future__ import annotations

from queue import Empty, Full, Queue
import threading
from typing import Any

from collector.packet_capture import PacketCapture
from collector.udp_receiver import ReceivedDatagram


class CaptureOverloadError(RuntimeError):
    """Raised instead of silently dropping a datagram when storage falls behind."""


_STOP = object()


class CapturePipeline:
    def __init__(
        self,
        capture: PacketCapture,
        capacity: int = 8192,
        put_timeout_seconds: float = 0.25,
    ) -> None:
        self.capture = capture
        self.put_timeout_seconds = put_timeout_seconds
        self._queue: Queue[ReceivedDatagram | object] = Queue(maxsize=max(1, capacity))
        self._thread = threading.Thread(
            target=self._run,
            name="telemetry-writer",
            daemon=False,
        )
        self._failure: BaseException | None = None
        self._started = False
        self._closed = False
        self.peak_queue_size = 0

    @property
    def queue_size(self) -> int:
        return self._queue.qsize()

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._thread.start()

    def submit(self, datagram: ReceivedDatagram) -> None:
        self.raise_if_failed()
        if not self._started or self._closed:
            raise RuntimeError("capture pipeline is not running")
        try:
            self._queue.put(datagram, timeout=self.put_timeout_seconds)
        except Full as exc:
            self.capture.software_drop_count += 1
            raise CaptureOverloadError(
                "capture queue is full; stopping rather than silently losing telemetry"
            ) from exc
        self.peak_queue_size = max(self.peak_queue_size, self._queue.qsize())

    def raise_if_failed(self) -> None:
        if self._failure is not None:
            raise RuntimeError("telemetry writer failed") from self._failure

    def _run(self) -> None:
        try:
            while True:
                try:
                    item = self._queue.get(timeout=1.0)
                except Empty:
                    if self.capture.raw_writer.has_pending:
                        self.capture.checkpoint()
                    continue
                try:
                    if item is _STOP:
                        return
                    assert isinstance(item, ReceivedDatagram)
                    self.capture.process(item)
                finally:
                    self._queue.task_done()
        except BaseException as exc:
            self._failure = exc
            self._discard_queued_items()

    def _discard_queued_items(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except Empty:
                return
            else:
                self._queue.task_done()

    def close(self, status: str = "complete") -> dict[str, Any]:
        if self._closed:
            return self.capture.summary(status)
        self._closed = True
        if self._started and self._thread.is_alive():
            while True:
                try:
                    self._queue.put(_STOP, timeout=0.1)
                    break
                except Full:
                    if not self._thread.is_alive():
                        break
            self._thread.join()
        failure = self._failure
        final_status = "error" if failure is not None else status
        summary = self.capture.close(final_status)
        if failure is not None:
            raise RuntimeError("telemetry writer failed") from failure
        return summary
