"""Publish verified compressed, restored or recovered copies without replacing sources."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import time
from typing import Any
import uuid

from storage.raw_writer import (
    ArchiveReadError, FILE_HEADER, FILE_MAGIC, RawPacketWriter,
    encode_record, iter_archive,
)


def _canonical(packet) -> tuple:
    return (
        packet.offset, packet.received_at_ns, packet.monotonic_ns,
        packet.source_ip, packet.source_port, packet.payload, packet.crc32,
    )


def verify_archives(
    source: Path, target: Path, *, allow_truncated_source: bool = False,
    control=None,
) -> dict[str, Any]:
    source_iter = iter_archive(source)
    target_iter = iter_archive(target)
    digest = hashlib.sha256(FILE_HEADER.pack(FILE_MAGIC, 1, FILE_HEADER.size, 0))
    count = 0
    tail_error = None
    try:
        while True:
            if control:
                control.report('逐包校验', count)
            try:
                left = next(source_iter, None)
            except ArchiveReadError as exc:
                if not allow_truncated_source or not exc.truncated:
                    raise
                tail_error = str(exc)
                left = None
            right = next(target_iter, None)
            if left is None and right is None:
                break
            if left is None or right is None or _canonical(left) != _canonical(right):
                raise ValueError(f"archive differs at record {count + 1}")
            digest.update(encode_record(
                left.payload, left.received_at_ns, left.monotonic_ns,
                left.source_ip, left.source_port,
            ))
            count += 1
    finally:
        source_iter.close()
        target_iter.close()
    return {
        "verified_packet_count": count,
        "all_packet_bytes_and_envelopes_identical": True,
        "canonical_v1_sha256": digest.hexdigest(),
        "truncated_source_tail": tail_error,
    }


def convert_archive(
    source: Path, target: Path, *, compression: str = "zlib",
    compression_level: int = 1, recover_tail: bool = False,
    control=None,
) -> dict[str, Any]:
    source = source.expanduser().resolve()
    target = target.expanduser().resolve()
    if source == target:
        raise ValueError("source and target must differ; source archives are never replaced")
    if target.exists():
        raise FileExistsError(f"target already exists: {target}")
    if not source.is_file():
        raise FileNotFoundError(source)
    source_stat = source.stat()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp-" + uuid.uuid4().hex)
    started = time.perf_counter()
    read_error = None
    try:
        with RawPacketWriter(
            temporary, compression=compression, compression_level=compression_level,
        ) as writer:
            try:
                for packet in iter_archive(source):
                    if control:
                        control.report('压缩原始数据', writer.packets_written)
                    writer.write(
                        packet.payload, packet.received_at_ns, packet.monotonic_ns,
                        packet.source_ip, packet.source_port,
                    )
            except ArchiveReadError as exc:
                if not recover_tail or not exc.truncated:
                    raise
                read_error = str(exc)
                if not writer.packets_written:
                    raise ValueError("archive contains no recoverable complete records") from exc
        verification = verify_archives(source, temporary, allow_truncated_source=recover_tail, control=control)
        if (source.stat().st_size, source.stat().st_mtime_ns) != (source_stat.st_size, source_stat.st_mtime_ns):
            raise RuntimeError("source changed during conversion; stop the capture before converting")
        compressed_bytes = temporary.stat().st_size
        # Hard-link publication is atomic and fails if another task created the
        # target. Both paths are on the same volume, unlike the input archive.
        if control:
            control.report('发布已校验副本', verification['verified_packet_count'])
        os.link(temporary, target)
        stats = writer.statistics()
        return {
            "status": "recovered_prefix" if read_error else "complete",
            "source": str(source), "target": str(target),
            "source_bytes": source_stat.st_size, "target_bytes": compressed_bytes,
            "saved_percent": round(100 * (1 - compressed_bytes / source_stat.st_size), 3),
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "storage": stats, **verification,
            "source_preserved": True,
        }
    finally:
        if temporary.exists():
            temporary.unlink()
