"""Paced background analysis; never runs on the UDP or Raw writer thread."""
from dataclasses import dataclass
import threading
import time
import sqlite3

from analysis.incremental import IncrementalAnalysisStore
from storage.foundation import foundation_path, read_progress


@dataclass(frozen=True)
class AnalysisSnapshot:
    state: str = "waiting"
    laps: int = 0
    error: str | None = None
    packets: int = 0


class AnalysisWorker:
    def __init__(self, source, logger, pressure=None):
        self.source, self.logger = source, logger
        self.pressure = pressure or (lambda: False)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._snapshot = AnalysisSnapshot()
        self._thread = threading.Thread(target=self._run, name="incremental-lap-analysis", daemon=False)

    def start(self):
        self._thread.start()

    def snapshot(self):
        with self._lock:
            return self._snapshot

    def _publish(self, state, laps=0, error=None, packets=None):
        with self._lock:
            self._snapshot = AnalysisSnapshot(state, laps, error,
                                              self._snapshot.packets if packets is None else packets)

    def _run(self):
        try:
            database = foundation_path(self.source)
            while True:
                try:
                    prepared = database.is_file() and read_progress(database) is not None
                except (sqlite3.Error, ValueError):
                    prepared = False  # foundation may be between CREATE and its first commit.
                if prepared:
                    break
                if self._stop.wait(0.5):
                    self._publish("stopped")
                    return
            with IncrementalAnalysisStore(self.source) as store:
                deadline = None
                while True:
                    closing = self._stop.is_set()
                    if closing and deadline is None:
                        deadline = time.monotonic() + 5.0
                    if not closing and self.pressure():
                        self._publish("throttled", self.snapshot().laps)
                        self._stop.wait(0.25)
                        continue
                    result = store.update(final=closing, batch_size=2000 if not closing else 10000)
                    self._publish("ready" if result["caught_up"] else "processing", result["laps_resampled"],
                                  packets=result["total_raw_packets"])
                    if closing and result["caught_up"]:
                        self._publish("stopped" if result.get("source_complete") else "pending", result["laps_resampled"])
                        return
                    if deadline and time.monotonic() >= deadline:
                        self._publish("pending", result["laps_resampled"])
                        return  # checkpoint committed; offline entry resumes safely.
                    if not closing:
                        self._stop.wait(0.75 if result["caught_up"] else 0.05)
        except Exception as exc:
            self.logger.exception("Background lap analysis paused; Raw capture is independent")
            self._publish("error", error=f"{type(exc).__name__}: {exc}")

    def close(self):
        self._stop.set()
        self._thread.join()
