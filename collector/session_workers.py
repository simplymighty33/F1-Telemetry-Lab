"""Bounded background consumers for the current game-session archive.

Rotation/consumer shutdown runs here, never on the UDP or Raw writer thread.
Retired sessions keep resumable checkpoints for offline analysis.
"""
from __future__ import annotations

import threading

from collector.foundation_worker import FoundationWorker, FoundationSnapshot
from collector.analysis_worker import AnalysisWorker, AnalysisSnapshot


class SessionDerivedWorker:
    def __init__(self, capture, logger, *, analysis_enabled=True, pressure=None):
        self.capture, self.logger = capture, logger
        self.analysis_enabled = analysis_enabled
        self.pressure = pressure or (lambda: False)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="session-derived-manager", daemon=False)
        self._current = None
        self._foundation = None
        self._analysis = None
        self._error = None

    def start(self):
        self._thread.start()

    def snapshot(self):
        if self._error:
            return FoundationSnapshot(state="error", error=self._error)
        if self._current is not self.capture.active_capture():
            return FoundationSnapshot()
        return self._foundation.snapshot() if self._foundation else FoundationSnapshot()

    def analysis_snapshot(self):
        if not self.analysis_enabled:
            return AnalysisSnapshot(state="disabled")
        if self._error:
            return AnalysisSnapshot(state='error', error=self._error)
        if self._current is not self.capture.active_capture():
            return AnalysisSnapshot()
        return self._analysis.snapshot() if self._analysis else AnalysisSnapshot()

    def _finish(self):
        try:
            if self._foundation:
                self._foundation.close()
        finally:
            if self._analysis:
                self._analysis.close()

    def _start_current(self, current):
        self._foundation = self._analysis = None
        self._current = current
        if current:
            raw = current.session_directory / "raw_packets.bin"
            self._foundation = FoundationWorker(raw, lambda c=current: c.raw_writer.persisted_file_bytes,
                                                self.logger, pressure=self.pressure)
            self._foundation.start()
            if self.analysis_enabled:
                self._analysis = AnalysisWorker(raw, self.logger, pressure=self.pressure)
                self._analysis.start()

    def _run(self):
        try:
            while not self._stop.is_set():
                current = self.capture.active_capture(ready_only=True)
                if current is not self._current:
                    self._finish()
                    if not self._stop.is_set():
                        self._start_current(current)
                self._stop.wait(0.15)
        except Exception as exc:
            self._error = f"{type(exc).__name__}: {exc}"
            self.logger.exception("Session analysis manager paused; Raw capture remains active")
        finally:
            # No valid Session body: keep the pending folder name, but still
            # prepare the last archive at shutdown. Its path is now immutable.
            try:
                current = self.capture.active_capture()
                if current and current.packet_count and current is not self._current and not self._error:
                    self._finish()
                    self._start_current(current)
                self._finish()
            except Exception as exc:
                self._error = f'{type(exc).__name__}: {exc}'
                self.logger.exception('Session derived shutdown failed; committed Raw remains preserved')

    def close(self):
        self._stop.set()
        if self._thread.ident is not None:
            self._thread.join()
