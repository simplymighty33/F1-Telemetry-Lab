"""Runtime path helpers that work from source and from a frozen executable."""

from __future__ import annotations

from pathlib import Path
import sys


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def application_root() -> Path:
    """Return the user-writable directory that owns config, data, and logs."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def resolve_runtime_path(value: str | Path, root: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = root / path
    return path.resolve()
