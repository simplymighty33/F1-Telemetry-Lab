"""Build/resume/verify the foundation cache for a stopped Raw recording."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from storage.foundation import ensure_foundation


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare reusable player/context data from stopped Raw")
    parser.add_argument("source", type=Path)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--verify", action="store_true", help="Verify the consumed Raw prefix even for an unchanged cache")
    parser.add_argument("--recover-tail", action="store_true", help="Explicitly keep a verified prefix of a stopped, truncated recording; never ignore CRC errors")
    args = parser.parse_args(argv)
    source = args.source / "raw_packets.bin" if args.source.is_dir() else args.source
    try:
        result = ensure_foundation(source, args.database, verify=args.verify, recover_tail=args.recover_tail)
    except Exception as exc:
        print(f"Foundation preparation failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2 if result["errors"] or result["tail_error"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
