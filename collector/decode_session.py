"""Stream a raw capture into a complete, portable decoded data set."""

from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import gzip
import json
import os
from pathlib import Path
import sys
from typing import Any

from decoder.full_parser import decode_packet
from decoder.header import PacketHeader
from decoder.protocol import game_label
from storage.raw_writer import ArchivedPacket, iter_archive


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fully decode an F1 capture session")
    parser.add_argument("session", type=Path, help="Session directory or raw_packets.bin")
    parser.add_argument("--output", type=Path, help="Output directory (default: SESSION/decoded)")
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Validate every packet but do not create decoded_packets.jsonl.gz",
    )
    return parser


def _iso(timestamp_ns: int) -> str:
    return datetime.fromtimestamp(timestamp_ns / 1_000_000_000, timezone.utc).isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")


def _archive_path(path: Path) -> tuple[Path, Path]:
    resolved = path.expanduser().resolve()
    if resolved.is_dir():
        return resolved / "raw_packets.bin", resolved
    return resolved, resolved.parent


def _archive_envelope(archived: ArchivedPacket, decoded: dict[str, Any]) -> dict[str, Any]:
    return {
        "archive": {
            "offset": archived.offset,
            "block_offset": archived.block_offset,
            "block_record_offset": archived.block_record_offset,
            "received_at_ns": archived.received_at_ns,
            "received_at": _iso(archived.received_at_ns),
            "monotonic_ns": archived.monotonic_ns,
            "source_ip": archived.source_ip,
            "source_port": archived.source_port,
            "payload_size": len(archived.payload),
            "crc32": archived.crc32,
        },
        "packet": decoded,
    }


def _write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    os.replace(temporary, path)


def _history_rows(
    session_uid: int,
    received_at_ns: int,
    decoded: dict[str, Any],
) -> list[dict[str, Any]]:
    rows = []
    for lap_number, lap in enumerate(
        decoded["lap_history"][: decoded["num_laps"]], start=1
    ):
        if lap["lap_time_ms"] <= 0:
            continue
        flags = lap["lap_valid_bit_flags"]
        rows.append(
            {
                "session_uid": str(session_uid),
                "lap_number": lap_number,
                "lap_time_ms": lap["lap_time_ms"],
                "sector1_ms": lap["sector1_time_minutes"] * 60_000 + lap["sector1_time_ms"],
                "sector2_ms": lap["sector2_time_minutes"] * 60_000 + lap["sector2_time_ms"],
                "sector3_ms": lap["sector3_time_minutes"] * 60_000 + lap["sector3_time_ms"],
                "lap_valid": bool(flags & 0x01),
                "sector1_valid": bool(flags & 0x02),
                "sector2_valid": bool(flags & 0x04),
                "sector3_valid": bool(flags & 0x08),
                "snapshot_received_at": _iso(received_at_ns),
            }
        )
    return rows


def decode_session(
    source: Path,
    output: Path | None = None,
    write_packets: bool = True,
    progress_every: int = 25_000,
) -> dict[str, Any]:
    archive, session_directory = _archive_path(source)
    if not archive.is_file():
        raise FileNotFoundError(f"raw archive not found: {archive}")
    output_directory = (output or session_directory / "decoded").expanduser().resolve()
    output_directory.mkdir(parents=True, exist_ok=True)

    packet_counts: Counter[int] = Counter()
    size_counts: Counter[int] = Counter()
    session_info: dict[int, dict[str, Any]] = {}
    player_histories: dict[int, list[dict[str, Any]]] = {}
    events: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    total = 0
    first_received_at_ns: int | None = None
    last_received_at_ns: int | None = None
    packet_target = output_directory / "decoded_packets.jsonl.gz"
    packet_temporary = output_directory / "decoded_packets.jsonl.gz.tmp"
    packet_stream = (
        gzip.open(packet_temporary, "wt", encoding="utf-8", newline="\n", compresslevel=6)
        if write_packets
        else None
    )
    try:
        for archived in iter_archive(archive):
            total += 1
            first_received_at_ns = first_received_at_ns or archived.received_at_ns
            last_received_at_ns = archived.received_at_ns
            try:
                decoded = decode_packet(archived.payload)
            except Exception as exc:
                errors.append(
                    {
                        "record_number": total,
                        "offset": archived.offset,
                        "payload_size": len(archived.payload),
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                continue

            header = decoded["header"]
            packet_id = header["packet_id"]
            session_uid = header["session_uid"]
            packet_counts[packet_id] += 1
            size_counts[len(archived.payload)] += 1
            if packet_stream is not None:
                json.dump(
                    _archive_envelope(archived, decoded),
                    packet_stream,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                packet_stream.write("\n")

            if packet_id == 1:
                session_info[session_uid] = {
                    "session_uid": str(session_uid),
                    "game": game_label(PacketHeader(**header)),
                    "game_year": header["game_year"],
                    "packet_format": header["packet_format"],
                    "game_mode": decoded["game_mode_name"],
                    "game_mode_id": decoded["game_mode"],
                    "session_type": decoded["session_type_name"],
                    "session_type_id": decoded["session_type"],
                    "track": decoded["track_name"],
                    "track_id": decoded["track_id"],
                    "rule_set": decoded["rule_set_name"],
                    "rule_set_id": decoded["rule_set"],
                    "total_laps": decoded["total_laps"],
                    "track_length": decoded["track_length"],
                }
            elif packet_id == 3:
                events.append(
                    {
                        "session_uid": str(session_uid),
                        "received_at": _iso(archived.received_at_ns),
                        "session_time": header["session_time"],
                        "frame_identifier": header["frame_identifier"],
                        "overall_frame_identifier": header["overall_frame_identifier"],
                        "event_code": decoded["event_code"],
                        "event_name": decoded["event_name"],
                        "event_details": decoded["event_details"],
                    }
                )
            elif packet_id == 11 and decoded["car_idx"] == header["player_car_index"]:
                # A Session History packet is an authoritative snapshot. Replacing
                # the prior list makes the exported result flashback-safe.
                player_histories[session_uid] = _history_rows(
                    session_uid, archived.received_at_ns, decoded
                )

            if progress_every and total % progress_every == 0:
                print(f"Decoded {total:,} packets...", flush=True)
    finally:
        if packet_stream is not None:
            packet_stream.close()

    if errors:
        if packet_temporary.exists():
            packet_temporary.unlink()
    elif write_packets:
        os.replace(packet_temporary, packet_target)

    all_laps = [row for uid in sorted(player_histories) for row in player_histories[uid]]
    lap_fields = [
        "session_uid", "lap_number", "lap_time_ms", "sector1_ms", "sector2_ms",
        "sector3_ms", "lap_valid", "sector1_valid", "sector2_valid", "sector3_valid",
        "snapshot_received_at",
    ]
    with (output_directory / "player_laps.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=lap_fields)
        writer.writeheader()
        writer.writerows(all_laps)

    event_fields = [
        "session_uid", "received_at", "session_time", "frame_identifier",
        "overall_frame_identifier", "event_code", "event_name", "event_details",
    ]
    with (output_directory / "events.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=event_fields)
        writer.writeheader()
        for event in events:
            event = dict(event)
            event["event_details"] = json.dumps(
                event["event_details"], ensure_ascii=False, separators=(",", ":")
            )
            writer.writerow(event)

    sessions = [session_info[uid] for uid in sorted(session_info)]
    _write_json(output_directory / "sessions.json", sessions)
    _write_json(output_directory / "decode_errors.json", errors)
    summary = {
        "source_archive": str(archive),
        "output_directory": str(output_directory),
        "status": "complete" if not errors else "completed_with_errors",
        "total_records": total,
        "decoded_records": total - len(errors),
        "decode_error_count": len(errors),
        "all_crc_checks_passed": not errors,
        "first_received_at": _iso(first_received_at_ns) if first_received_at_ns else None,
        "last_received_at": _iso(last_received_at_ns) if last_received_at_ns else None,
        "packet_counts": {str(key): packet_counts[key] for key in sorted(packet_counts)},
        "payload_size_counts": {str(key): size_counts[key] for key in sorted(size_counts)},
        "session_count": len(session_info),
        "event_count": len(events),
        "final_player_lap_count": len(all_laps),
        "decoded_packet_archive": packet_target.name if write_packets and not errors else None,
    }
    _write_json(output_directory / "decode_summary.json", summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        summary = decode_session(
            args.session,
            args.output,
            write_packets=not args.summary_only,
        )
    except Exception as exc:
        print(f"Decode failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["decode_error_count"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
