"""Bounded-memory raw archives with independent lossless blocks.

V1 is an uncompressed sequence of records. V2 stores the same record bytes in
independently checksummed zlib blocks. Packet offsets are logical V1 offsets in
both formats; V2 also exposes physical block and inner offsets.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import ipaddress
import os
from pathlib import Path
import struct
from typing import BinaryIO, Iterator
import zlib


FILE_MAGIC = b"F1TLRAW\0"
FILE_HEADER = struct.Struct("<8sHHI")
RECORD_MAGIC = b"PKT1"
RECORD_HEADER = struct.Struct("<4sBBHQQ16sHII")
FORMAT_VERSION = 1  # Record format is unchanged inside compressed archives.
COMPRESSED_VERSION = 2
BLOCK_MAGIC = b"BLK2"
BLOCK_HEADER = struct.Struct("<4sBBHIIIIQQQI")
MAX_PAYLOAD_BYTES = 65_535
MAX_BLOCK_BYTES = 8 * 1024 * 1024
DEFAULT_BLOCK_BYTES = 256 * 1024


class ArchiveReadError(ValueError):
    def __init__(self, message: str, *, truncated: bool = False) -> None:
        super().__init__(message)
        self.truncated = truncated


@dataclass(frozen=True, slots=True)
class RawReference:
    offset: int
    record_length: int
    payload_length: int
    crc32: int
    archive_version: int = FORMAT_VERSION
    block_offset: int | None = None
    block_record_offset: int | None = None


@dataclass(frozen=True, slots=True)
class ArchivedPacket:
    offset: int
    received_at_ns: int
    monotonic_ns: int
    source_ip: str
    source_port: int
    payload: bytes
    crc32: int
    block_offset: int | None = None
    block_record_offset: int | None = None


def encode_record(
    payload: bytes, received_at_ns: int, monotonic_ns: int,
    source_ip: str, source_port: int,
) -> bytes:
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise ValueError("payload exceeds maximum UDP record size")
    address = ipaddress.ip_address(source_ip)
    checksum = zlib.crc32(payload) & 0xFFFFFFFF
    return RECORD_HEADER.pack(
        RECORD_MAGIC, FORMAT_VERSION, address.version, RECORD_HEADER.size,
        received_at_ns, monotonic_ns, address.packed.ljust(16, b"\0"),
        source_port, len(payload), checksum,
    ) + payload


class RawPacketWriter:
    def __init__(
        self, path: Path, flush_every: int = 256, *, compression: str = "none",
        compression_level: int = 1, block_bytes: int = DEFAULT_BLOCK_BYTES,
    ) -> None:
        if compression not in {"none", "zlib"}:
            raise ValueError("raw compression must be 'none' or 'zlib'")
        if not 0 <= compression_level <= 9:
            raise ValueError("compression level must be 0 through 9")
        if not RECORD_HEADER.size + MAX_PAYLOAD_BYTES <= block_bytes <= MAX_BLOCK_BYTES:
            raise ValueError("raw block size must be between 65585 and 8388608 bytes")
        self.path = path
        self.flush_every = max(1, flush_every)
        self.compression = compression
        self.compression_level = compression_level
        self.block_bytes = block_bytes
        self.archive_version = COMPRESSED_VERSION if compression == "zlib" else FORMAT_VERSION
        self._file = path.open("xb", buffering=256 * 1024)
        self._file.write(FILE_HEADER.pack(FILE_MAGIC, self.archive_version, FILE_HEADER.size, 0))
        self._since_flush = 0
        self._buffer = bytearray()
        self._block_count = 0
        self._first_received = self._last_received = 0
        self._logical_offset = FILE_HEADER.size
        self.blocks_written = 0
        self.packets_written = 0
        self.persisted_packet_count = 0
        self.persisted_file_bytes = 0
        self._failed = False

    @property
    def has_pending(self) -> bool:
        return self._since_flush > 0

    def write(
        self, payload: bytes, received_at_ns: int, monotonic_ns: int,
        source_ip: str, source_port: int,
    ) -> RawReference:
        if self._file.closed or self._failed:
            raise RuntimeError("raw writer is closed")
        record = encode_record(payload, received_at_ns, monotonic_ns, source_ip, source_port)
        if self.compression == "zlib" and self._buffer and len(self._buffer) + len(record) > self.block_bytes:
            self.flush()
        offset = self._logical_offset
        block_offset = self._file.tell() if self.compression == "zlib" else None
        inner_offset = len(self._buffer) if self.compression == "zlib" else None
        if self.compression == "zlib":
            if not self._block_count:
                self._first_received = received_at_ns
            self._last_received = received_at_ns
            self._buffer.extend(record)
            self._block_count += 1
        else:
            self._file.write(record)
        self._logical_offset += len(record)
        self._since_flush += 1
        self.packets_written += 1
        reference = RawReference(
            offset, len(record), len(payload), zlib.crc32(payload) & 0xFFFFFFFF,
            self.archive_version, block_offset, inner_offset,
        )
        if self._since_flush >= self.flush_every:
            self.flush()
        return reference

    def flush(self) -> None:
        if self._failed:
            raise RuntimeError("raw writer previously failed; refusing another write")
        try:
            self._flush_impl()
        except BaseException:
            self._failed = True
            raise

    def _flush_impl(self) -> None:
        if self._buffer:
            original = bytes(self._buffer)
            compressed = zlib.compress(original, self.compression_level)
            codec = 1 if len(compressed) < len(original) else 0
            data = compressed if codec else original
            header = BLOCK_HEADER.pack(
                BLOCK_MAGIC, 1, codec, BLOCK_HEADER.size,
                len(data), len(original), self._block_count,
                zlib.crc32(original) & 0xFFFFFFFF,
                self._logical_offset - len(original),
                self._first_received, self._last_received, 0,
            )
            header = header[:-4] + struct.pack("<I", zlib.crc32(header[:-4]) & 0xFFFFFFFF)
            self._file.write(header)
            self._file.write(data)
            self._buffer.clear()
            self._block_count = 0
            self.blocks_written += 1
        self._file.flush()
        os.fsync(self._file.fileno())
        self.persisted_packet_count = self.packets_written
        self.persisted_file_bytes = self._file.tell()
        self._since_flush = 0

    def statistics(self) -> dict[str, int | float | str]:
        return {
            "archive_version": self.archive_version,
            "compression": self.compression,
            "compression_level": self.compression_level,
            "block_limit_bytes": self.block_bytes,
            "blocks_written": self.blocks_written,
            "uncompressed_bytes": self._logical_offset,
            "persisted_file_bytes": self.persisted_file_bytes,
            "persisted_packet_count": self.persisted_packet_count,
            "pending_packet_count": self.packets_written - self.persisted_packet_count,
            "stored_ratio": round(self.persisted_file_bytes / self._logical_offset, 6),
            "offset_kind": "logical_v1" if self.archive_version == 2 else "physical",
        }

    def suspend(self) -> None:
        """Release an idle archive handle without forgetting its logical offsets."""
        if not self._file.closed:
            self.flush()
            self._file.close()
            stat = self.path.stat()
            self._suspended_identity = (stat.st_size, stat.st_mtime_ns)

    def resume(self) -> None:
        """Resume only this writer's unchanged, fully committed archive."""
        if self._failed:
            raise RuntimeError("raw writer previously failed")
        if not self._file.closed:
            return
        stat = self.path.stat()
        if (stat.st_size, stat.st_mtime_ns) != getattr(self, "_suspended_identity", None):
            raise RuntimeError("suspended raw archive changed; refusing to append")
        self._file = self.path.open("r+b", buffering=256 * 1024)
        self._file.seek(0, os.SEEK_END)

    def close(self) -> None:
        if not self._file.closed:
            try:
                if not self._failed:
                    self.flush()
            finally:
                self._file.close()

    def __enter__(self) -> RawPacketWriter:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _read_file_header(stream: BinaryIO) -> int:
    data = stream.read(FILE_HEADER.size)
    if len(data) != FILE_HEADER.size:
        raise ArchiveReadError("truncated raw archive header", truncated=True)
    magic, version, header_size, flags = FILE_HEADER.unpack(data)
    if magic != FILE_MAGIC or version not in {1, 2} or header_size != FILE_HEADER.size or flags:
        raise ArchiveReadError("unsupported raw archive")
    return version


def _read_record(
    stream: BinaryIO, logical_offset: int, *, block_offset: int | None = None,
    block_record_offset: int | None = None,
) -> ArchivedPacket | None:
    header = stream.read(RECORD_HEADER.size)
    if not header:
        return None
    if len(header) != RECORD_HEADER.size:
        raise ArchiveReadError(f"truncated record header at {logical_offset}", truncated=True)
    magic, version, family, size, received, monotonic, address, port, length, checksum = RECORD_HEADER.unpack(header)
    if magic != RECORD_MAGIC or version != 1 or family not in {4, 6} or size != RECORD_HEADER.size:
        raise ArchiveReadError(f"invalid record header at {logical_offset}")
    if length > MAX_PAYLOAD_BYTES:
        raise ArchiveReadError(f"oversized payload at {logical_offset}")
    payload = stream.read(length)
    if len(payload) != length:
        raise ArchiveReadError(f"truncated payload at {logical_offset}", truncated=True)
    if (zlib.crc32(payload) & 0xFFFFFFFF) != checksum:
        raise ArchiveReadError(f"CRC mismatch at {logical_offset}")
    source = str(ipaddress.ip_address(address[:4] if family == 4 else address))
    return ArchivedPacket(
        logical_offset, received, monotonic, source, port, payload, checksum,
        block_offset, block_record_offset,
    )


def _read_block(stream: BinaryIO, logical_offset: int) -> list[ArchivedPacket] | None:
    physical_offset = stream.tell()
    header = stream.read(BLOCK_HEADER.size)
    if not header:
        return None
    if len(header) != BLOCK_HEADER.size:
        raise ArchiveReadError(f"truncated block header at {physical_offset}", truncated=True)
    magic, version, codec, size, stored, original, count, checksum, logical, first, last, header_crc = BLOCK_HEADER.unpack(header)
    if (zlib.crc32(header[:-4]) & 0xFFFFFFFF) != header_crc:
        raise ArchiveReadError(f"block header CRC mismatch at {physical_offset}")
    if magic != BLOCK_MAGIC or version != 1 or codec not in {0, 1} or size != BLOCK_HEADER.size:
        raise ArchiveReadError(f"invalid block header at {physical_offset}")
    if logical != logical_offset or not 0 < original <= MAX_BLOCK_BYTES or not 0 < stored <= MAX_BLOCK_BYTES:
        raise ArchiveReadError(f"invalid block bounds at {physical_offset}")
    if not 0 < count <= original // RECORD_HEADER.size:
        raise ArchiveReadError(f"invalid block record count at {physical_offset}")
    data = stream.read(stored)
    if len(data) != stored:
        raise ArchiveReadError(f"truncated block payload at {physical_offset}", truncated=True)
    if codec:
        try:
            decoder = zlib.decompressobj()
            unpacked = decoder.decompress(data, original + 1)
            if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
                raise ArchiveReadError(f"invalid compressed stream at {physical_offset}")
        except zlib.error as exc:
            raise ArchiveReadError(f"invalid compressed data at {physical_offset}") from exc
    else:
        unpacked = data
    if len(unpacked) != original or (zlib.crc32(unpacked) & 0xFFFFFFFF) != checksum:
        raise ArchiveReadError(f"block size/CRC mismatch at {physical_offset}")
    block_stream = io.BytesIO(unpacked)
    packets = []
    for _ in range(count):
        inner = block_stream.tell()
        try:
            packet = _read_record(
                block_stream, logical + inner,
                block_offset=physical_offset, block_record_offset=inner,
            )
        except ArchiveReadError as exc:
            raise ArchiveReadError(f"invalid internal record in block at {physical_offset}: {exc}") from exc
        if packet is None:
            raise ArchiveReadError(f"missing record in block at {physical_offset}")
        packets.append(packet)
    if block_stream.tell() != original or packets[0].received_at_ns != first or packets[-1].received_at_ns != last:
        raise ArchiveReadError(f"block metadata mismatch at {physical_offset}")
    return packets


def iter_archive(path: Path) -> Iterator[ArchivedPacket]:
    """Strictly verify both archive versions; damaged tails are never hidden."""
    with path.open("rb") as stream:
        version = _read_file_header(stream)
        logical = FILE_HEADER.size
        while True:
            if version == 1:
                packet = _read_record(stream, logical)
                if packet is None:
                    return
                yield packet
                logical += RECORD_HEADER.size + len(packet.payload)
            else:
                packets = _read_block(stream, logical)
                if packets is None:
                    return
                yield from packets
                logical += sum(RECORD_HEADER.size + len(packet.payload) for packet in packets)


class ArchiveCursor:
    """Resume at a verified record/block boundary, never read beyond durability.

    Checkpoints include a SHA-256 of the consumed physical prefix. Resuming
    verifies that prefix once; normal polling does not rescan old packets.
    Explicit tail recovery stops at a truncated last record/block, never a CRC
    error. The strict iter_archive API is deliberately unchanged.
    """
    def __init__(self, path: Path, checkpoint: dict | None = None) -> None:
        self.path = path
        self._stream = path.open("rb")
        self._hash = hashlib.sha256()
        self.tail_error: str | None = None
        try:
            self.version = _read_file_header(self)
            self.logical = FILE_HEADER.size
            if checkpoint:
                physical = int(checkpoint["physical"])
                if physical < FILE_HEADER.size or physical > path.stat().st_size:
                    raise ArchiveReadError("checkpoint exceeds available Raw data")
                if self.version == 1 and physical != int(checkpoint["logical"]):
                    raise ArchiveReadError("invalid uncompressed resume cursor")
                remaining = physical - self._stream.tell()
                while remaining:
                    data = self.read(min(1024 * 1024, remaining))
                    if not data:
                        raise ArchiveReadError("Raw prefix shortened during verification")
                    remaining -= len(data)
                if self._hash.hexdigest() != checkpoint["sha256"]:
                    raise ArchiveReadError("Raw prefix changed; refusing to reuse cached data")
                self.logical = int(checkpoint["logical"])
        except BaseException:
            self._stream.close()
            raise

    def read(self, size: int = -1) -> bytes:
        data = self._stream.read(size)
        self._hash.update(data)
        return data

    def tell(self) -> int:
        return self._stream.tell()

    def checkpoint(self) -> dict:
        return {"physical": self.tell(), "logical": self.logical, "sha256": self._hash.hexdigest()}

    def next_batch(self, limit: int, *, recover_tail: bool = False, max_packets: int = 512) -> list[ArchivedPacket]:
        if self.tail_error or self.tell() >= limit:
            return []
        packets: list[ArchivedPacket] = []
        while self.tell() < limit and len(packets) < max_packets:
            physical, logical, digest = self.tell(), self.logical, self._hash.copy()
            try:
                if self.version == 1:
                    packet = _read_record(self, self.logical)
                    batch = [packet] if packet else []
                else:
                    batch = _read_block(self, self.logical) or []
                if not batch or self.tell() > limit:
                    raise ArchiveReadError("record crosses durable Raw watermark", truncated=True)
            except ArchiveReadError as exc:
                self._stream.seek(physical)
                self.logical, self._hash = logical, digest
                if recover_tail and exc.truncated:
                    self.tail_error = str(exc)
                    return packets
                raise
            self.logical += sum(RECORD_HEADER.size + len(item.payload) for item in batch)
            packets.extend(batch)
            if self.version == 2:
                break  # Commit only whole, checksummed compressed blocks.
        return packets

    def close(self) -> None:
        self._stream.close()

    def __enter__(self) -> ArchiveCursor:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def read_packet_at(path: Path, reference: RawReference) -> ArchivedPacket:
    """Resolve a V1 record or decompress only its referenced V2 block."""
    with path.open("rb") as stream:
        version = _read_file_header(stream)
        if version != reference.archive_version:
            raise ArchiveReadError("reference/archive version mismatch")
        if version == 1:
            stream.seek(reference.offset)
            packet = _read_record(stream, reference.offset)
        else:
            if reference.block_offset is None or reference.block_record_offset is None:
                raise ArchiveReadError("compressed reference needs block offsets")
            stream.seek(reference.block_offset)
            logical_start = reference.offset - reference.block_record_offset
            packets = _read_block(stream, logical_start)
            packet = next((item for item in packets or () if item.offset == reference.offset), None)
        if packet is None or len(packet.payload) != reference.payload_length or packet.crc32 != reference.crc32:
            raise ArchiveReadError("raw reference does not match stored packet")
        if RECORD_HEADER.size + len(packet.payload) != reference.record_length:
            raise ArchiveReadError("raw reference length mismatch")
        return packet
