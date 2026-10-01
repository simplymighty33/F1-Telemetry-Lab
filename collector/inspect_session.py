"""Inspect a completed/in-progress capture without decoding packet bodies."""

from __future__ import annotations

import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys

from decoder.packet_parser import packet_name
from storage.raw_writer import iter_archive


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect an F1 telemetry session")
    parser.add_argument("session_directory", type=Path)
    parser.add_argument(
        "--verify-raw",
        action="store_true",
        help="scan the full raw archive and verify every CRC32",
    )
    return parser


def inspect(session_directory: Path, verify_raw: bool = False) -> dict[str, object]:
    metadata_path = session_directory / "metadata.json"
    database_path = session_directory / "telemetry.db"
    with metadata_path.open("r", encoding="utf-8") as stream:
        metadata = json.load(stream)
    with closing(sqlite3.connect(database_path)) as connection:
        rows = connection.execute(
            """SELECT packet_id, COUNT(*), MIN(received_at_ns), MAX(received_at_ns)
               FROM packets GROUP BY packet_id ORDER BY packet_id"""
        ).fetchall()
        total = connection.execute("SELECT COUNT(*) FROM packets").fetchone()[0]
    types = []
    for packet_id, count, first_ns, last_ns in rows:
        span = max(0.0, (last_ns - first_ns) / 1_000_000_000) if count else 0.0
        types.append(
            {
                "packet_id": packet_id,
                "name": packet_name(packet_id),
                "count": count,
                "observed_hz": round((count - 1) / span, 3) if span > 0 else 0.0,
            }
        )
    result: dict[str, object] = {
        "session_id": metadata.get("session_id"),
        "status": metadata.get("summary", {}).get("status"),
        "start_time": metadata.get("start_time"),
        "end_time": metadata.get("end_time"),
        "indexed_packet_count": total,
        "packet_types": types,
    }
    if verify_raw:
        raw_count = sum(1 for _ in iter_archive(session_directory / "raw_packets.bin"))
        result["raw_packet_count"] = raw_count
        result["raw_crc_valid"] = True
        result["raw_matches_index"] = raw_count == total
    return result


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = inspect(args.session_directory.resolve(), args.verify_raw)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.verify_raw and not result["raw_matches_index"]:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

