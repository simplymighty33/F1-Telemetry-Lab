"""Streaming read-only endurance audit. Output excludes identities/IPs/paths.

Capture must be stopped. Header repacking is checked for every datagram; when
present, recording index and foundation Header bytes are verified independently.
This audit is not a claim of network delivery or a substitute for live testing.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import ExitStack, closing
from dataclasses import astuple
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

from collector import __version__
from decoder.header import HEADER_SIZE, HEADER_STRUCT, decode_header
from decoder.stream import validate_packet
from storage.foundation import foundation_path
from storage.lease import lease_active
from storage.raw_writer import iter_archive


def _read_only(path):
    if not path.is_file():
        return None
    con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    con.execute("BEGIN")
    return con


def audit_session(session: Path, cancelled=lambda: False):
    session = session.resolve()
    raw = session / "raw_packets.bin"
    if lease_active(session / "capture.lock"):
        raise ValueError("请先安全停止所选录制，再进行完整审计")
    if not raw.is_file():
        raise ValueError("所选Session没有Raw文件")
    before = raw.stat()
    metadata_file = session / "metadata.json"
    meta_before = metadata_file.stat() if metadata_file.is_file() else None
    metadata = json.loads(metadata_file.read_text(encoding="utf-8")) if meta_before else {}
    summary = metadata.get("summary", {})
    result = {"audit_version": 1, "application_version": __version__, "status": "needs_review",
              "raw_packets": 0, "raw_bytes": before.st_size, "raw_crc_valid": False,
              "header_roundtrip_mismatches": 0, "header_decode_errors": 0,
              "protocol_validation_errors": 0, "index_mismatches": 0, "foundation_mismatches": 0,
              "recording_status": summary.get("status"), "software_drop_count": summary.get("software_drop_count"),
              "resource_health": {k: summary.get("resource_health", {}).get(k) for k in
                    ("checks", "pressure_checks", "queue_peak", "foundation_lag_peak", "analysis_lag_peak", "min_free_bytes", "disk_stop", "disk_check_failures",
                     "memory_checks", "memory_start_working_set_bytes", "memory_last_working_set_bytes", "memory_peak_working_set_bytes",
                     "memory_start_private_bytes", "memory_last_private_bytes", "memory_peak_private_bytes")},
              "flashback_packets": 0, "session_count": 0, "packet_types": [], "warnings": [],
              "privacy": "No player identities, sessionUID values, IPs or private paths included.",
              "limits": "接收间隔不是丢包率。未接收到的包无法由本档案证明；需结合真实游戏测试记录。"}
    digest, header_digest = hashlib.sha256(), hashlib.sha256()
    types, sessions, bounds = Counter(), set(), {}
    index_path = session / "telemetry.db"
    cache_path = foundation_path(raw)
    with ExitStack() as stack:
        index = _read_only(index_path)
        cache = _read_only(cache_path)
        for con in (index, cache):
            if con is not None:
                stack.enter_context(closing(con))
        index_cursor = index.execute("SELECT raw_offset,received_at_ns,size,crc32,packet_format,game_major_version,packet_version,packet_id,session_uid,frame_identifier,overall_frame_identifier,player_car_index,secondary_player_car_index FROM packets ORDER BY raw_offset") if index else None
        cache_cursor = cache.execute("SELECT raw_offset,received_at_ns,header FROM packets ORDER BY raw_offset") if cache else None
        result["index_present"], result["foundation_present"] = bool(index), bool(cache)
        packets = stack.enter_context(closing(iter_archive(raw)))
        try:
            for packet in packets:
                if cancelled():
                    raise InterruptedError("审计已取消")
                result["raw_packets"] += 1
                payload = packet.payload
                header_digest.update(payload[:HEADER_SIZE])
                header = None
                try:
                    header = decode_header(payload)
                    if HEADER_STRUCT.pack(*astuple(header)) != payload[:HEADER_SIZE]:
                        result["header_roundtrip_mismatches"] += 1
                    validate_packet(payload, header)
                except ValueError:
                    if header is None:
                        result["header_decode_errors"] += 1
                    else:
                        result["protocol_validation_errors"] += 1
                if header:
                    key = (header.packet_format, header.packet_id)
                    types[key] += 1
                    sessions.add(header.session_uid)
                    first, last, gap = bounds.get(key, (packet.received_at_ns, packet.received_at_ns, 0))
                    bounds[key] = (first, packet.received_at_ns, max(gap, packet.received_at_ns - last))
                    if header.packet_id == 3 and payload[HEADER_SIZE:HEADER_SIZE+4] == b"FLBK":
                        result["flashback_packets"] += 1
                if index_cursor:
                    indexed = index_cursor.fetchone()
                    fields = (header.packet_format, header.game_major_version, header.packet_version, header.packet_id,
                              str(header.session_uid), header.frame_identifier, header.overall_frame_identifier,
                              header.player_car_index, header.secondary_player_car_index) if header else (None,) * 9
                    expected = (packet.offset, packet.received_at_ns, len(payload), packet.crc32, *fields)
                    if indexed != expected:
                        result["index_mismatches"] += 1
                if cache_cursor and cache_cursor.fetchone() != (packet.offset, packet.received_at_ns, payload[:HEADER_SIZE]):
                    result["foundation_mismatches"] += 1
            result["raw_crc_valid"] = True
        except InterruptedError:
            raise
        except (OSError, ValueError) as error:
            result["raw_error_type"] = type(error).__name__  # Never leak a private exception path.
        if index_cursor:
            result["index_mismatches"] += sum(1 for _ in index_cursor)
        if cache_cursor:
            result["foundation_mismatches"] += sum(1 for _ in cache_cursor)
        with raw.open("rb") as stream:
            while block := stream.read(1024 * 1024):
                if cancelled():
                    raise InterruptedError("审计已取消")
                digest.update(block)
    after = raw.stat()
    meta_after = metadata_file.stat() if metadata_file.is_file() else None
    meta_signature = lambda stat: (stat.st_size, stat.st_mtime_ns) if stat else None
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or meta_signature(meta_before) != meta_signature(meta_after) or lease_active(session / "capture.lock"):
        raise ValueError("录制在审计期间变化，请停止采集后重试")
    result["raw_sha256"], result["headers_sha256"] = digest.hexdigest(), header_digest.hexdigest()
    result["session_count"] = len(sessions)
    result["packet_types"] = [{"format": key[0], "packet_id": key[1], "count": count,
        "max_receive_gap_ms": bounds[key][2] / 1_000_000} for key, count in sorted(types.items())]
    recorded = summary.get("packet_count", metadata.get("packet_count"))
    result["metadata_packet_count"] = recorded
    if recorded != result["raw_packets"]:
        result["warnings"].append("metadata包数未知或与Raw不一致")
    if not result["raw_packets"]:
        result["warnings"].append("空录制不能证明连续性")
    if not index:
        result["warnings"].append("缺少录制索引，未核验逐包索引")
    if not cache:
        result["warnings"].append("缺少基础缓存，未核验独立缓存Header")
    if result["foundation_mismatches"]:
        result["warnings"].append("基础缓存未追上或与Raw不同，需要进一步核验")
    if summary.get("status") != "complete" or summary.get("software_drop_count") != 0:
        result["warnings"].append("正常结束/软件丢弃计数未全部满足，旧录制缺失字段也需人工核验")
    if result["raw_crc_valid"] and result["raw_packets"] and all(result[k] == 0 for k in
        ("header_roundtrip_mismatches", "header_decode_errors", "protocol_validation_errors", "index_mismatches", "foundation_mismatches")) and not result["warnings"]:
        result["status"] = "archive_checks_passed"
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only private-data-free Raw endurance audit")
    parser.add_argument("session", type=Path)
    args = parser.parse_args(argv)
    try:
        result = audit_session(args.session)
    except Exception as error:
        print(json.dumps({"status": "audit_failed", "error_type": type(error).__name__}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "archive_checks_passed" else 2


if __name__ == "__main__":
    sys.exit(main())
