"""OS-owned single-writer leases; process death releases locks automatically."""
from __future__ import annotations

from pathlib import Path
import os


class LeaseBusyError(RuntimeError):
    pass


class FileLease:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._stream = path.open("a+b")
        try:
            if path.stat().st_size == 0:
                self._stream.write(b"1")
                self._stream.flush()
            self._stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self._stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._stream.close()
            raise LeaseBusyError(f"另一个任务正在使用该数据：{path.name}") from exc

    def close(self) -> None:
        if not self._stream.closed:
            try:
                self._stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(self._stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._stream.fileno(), fcntl.LOCK_UN)
            finally:
                self._stream.close()

    def __enter__(self) -> FileLease:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def lease_active(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        with FileLease(path):
            return False
    except LeaseBusyError:
        return True
