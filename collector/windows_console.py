"""Best-effort graceful shutdown for Windows console close/logoff events."""

from __future__ import annotations

import os
import threading
from typing import Any


class WindowsConsoleCloseHandler:
    def __init__(
        self,
        stop_requested: threading.Event,
        shutdown_complete: threading.Event,
        close_wait_seconds: float = 10.0,
    ) -> None:
        self.stop_requested = stop_requested
        self.shutdown_complete = shutdown_complete
        self.close_wait_seconds = close_wait_seconds
        self._kernel32: Any = None
        self._callback: Any = None

    def register(self) -> None:
        if os.name != "nt":
            return
        import ctypes
        from ctypes import wintypes

        handler_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

        @handler_type
        def callback(control_type: int) -> bool:
            if control_type not in (0, 1, 2, 5, 6):
                return False
            self.stop_requested.set()
            if control_type in (2, 5, 6):
                self.shutdown_complete.wait(self.close_wait_seconds)
            return True

        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._callback = callback
        if not self._kernel32.SetConsoleCtrlHandler(self._callback, True):
            self._callback = None
            raise OSError(ctypes.get_last_error(), "SetConsoleCtrlHandler failed")

    def close(self) -> None:
        if self._kernel32 is not None and self._callback is not None:
            self._kernel32.SetConsoleCtrlHandler(self._callback, False)
        self._callback = None
        self._kernel32 = None
