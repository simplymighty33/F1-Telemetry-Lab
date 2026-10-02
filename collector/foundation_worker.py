"""Independent durable-Raw consumer; never adds backpressure to UDP capture."""
from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import threading
import time
from typing import Callable

from storage.foundation import FoundationStore
from storage.raw_archive import ArchiveCursor, FILE_HEADER


@dataclass(frozen=True, slots=True)
class FoundationSnapshot:
    state: str = "waiting"
    packets: int = 0
    errors: int = 0
    error: str | None = None


class FoundationWorker:
    def __init__(self, source: Path, watermark: Callable[[], int], logger: logging.Logger, pressure=None) -> None:
        self.source, self.watermark, self.logger = source, watermark, logger
        self.pressure = pressure or (lambda: False)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._snapshot = FoundationSnapshot()
        self._thread = threading.Thread(target=self._run, name="foundation-ingest", daemon=False)

    def start(self) -> None:
        self._thread.start()

    def snapshot(self) -> FoundationSnapshot:
        with self._lock:
            return self._snapshot

    def _publish(self, state: str, packets: int = 0, errors: int = 0, error: str | None = None) -> None:
        with self._lock:
            self._snapshot = FoundationSnapshot(state, packets, errors, error)

    def _run(self) -> None:
        try:
            while self.watermark() < FILE_HEADER.size:
                if self._stop.wait(0.1) and self.watermark() < FILE_HEADER.size:
                    return
            with FoundationStore(self.source) as store, ArchiveCursor(self.source, store.progress["cursor"]) as cursor:
                closing_deadline = None
                while True:
                    if self._stop.is_set() and closing_deadline is None:
                        closing_deadline = time.monotonic() + 2.0
                    if not self._stop.is_set() and self.pressure():
                        self._publish("throttled", store.progress["packets"], store.progress["errors"])
                        self._stop.wait(0.25)
                        continue
                    limit = self.watermark()
                    batch = cursor.next_batch(limit)
                    if batch:
                        progress = store.ingest(batch, cursor.checkpoint(), source_mtime_ns=self.source.stat().st_mtime_ns)
                        self._publish("processing", progress["packets"], progress["errors"])
                    else:
                        if store.progress["cursor"] is None:
                            store.ingest([], cursor.checkpoint(), source_mtime_ns=self.source.stat().st_mtime_ns)
                        state = "stopped" if self._stop.is_set() else "ready"
                        self._publish(state, store.progress["packets"], store.progress["errors"])
                        if self._stop.is_set() and cursor.tell() >= self.watermark():
                            break
                        self._stop.wait(0.15)
                    if closing_deadline is not None and time.monotonic() >= closing_deadline:
                        self._publish("pending", store.progress["packets"], store.progress["errors"])
                        break  # Offline resume can finish any lag without delaying shutdown.
        except Exception as exc:
            snapshot = self.snapshot()
            self._publish("error", snapshot.packets, snapshot.errors, f"{type(exc).__name__}: {exc}")
            self.logger.exception("Foundation processing failed; Raw capture remains active")

    def close(self) -> None:
        self._stop.set()
        if self._thread.ident is not None:
            self._thread.join()
