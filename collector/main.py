"""Stable public entry point for the collector application."""

from __future__ import annotations

import sys

from collector.app import main


__all__ = ["main"]


if __name__ == "__main__":
    sys.exit(main())

