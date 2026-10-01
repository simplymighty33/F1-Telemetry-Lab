"""Compress or restore an archive into a verified new file."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from storage.archive_tools import convert_archive


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Completed Session directory or raw archive")
    parser.add_argument("--output", required=True, type=Path, help="New output file; never overwritten")
    parser.add_argument("--restore", action="store_true", help="Produce a legacy uncompressed V1 copy")
    parser.add_argument("--recover-tail", action="store_true", help="Explicitly recover complete records/blocks before a truncated tail")
    parser.add_argument("--level", type=int, choices=range(10), default=1)
    parser.add_argument("--report", type=Path, help="Write verification results to a new JSON file")
    args = parser.parse_args(argv)
    if args.report is not None and args.report.exists():
        parser.error("report path already exists; choose a new path")
    source = args.source.resolve()
    if source.is_dir():
        metadata_path = source / "metadata.json"
        if metadata_path.is_file():
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if metadata.get("summary", {}).get("status") == "recording" and not args.recover_tail:
                parser.error("Session is still marked recording; stop capture before conversion")
        source = source / "raw_packets.bin"
    try:
        result = convert_archive(
            source, args.output, compression="none" if args.restore else "zlib",
            compression_level=args.level, recover_tail=args.recover_tail,
        )
        if args.report is not None:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            with args.report.open("x", encoding="utf-8") as stream:
                json.dump(result, stream, ensure_ascii=False, indent=2)
    except Exception as exc:
        print(f"Archive conversion failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if sys.stdout is not None:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
