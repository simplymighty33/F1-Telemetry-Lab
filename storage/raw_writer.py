"""Compatibility import path for the versioned raw archive implementation."""

from storage.raw_archive import (
    ArchiveReadError, ArchivedPacket, BLOCK_HEADER, BLOCK_MAGIC,
    COMPRESSED_VERSION, DEFAULT_BLOCK_BYTES, FILE_HEADER, FILE_MAGIC,
    FORMAT_VERSION, MAX_BLOCK_BYTES, MAX_PAYLOAD_BYTES, RECORD_HEADER,
    RECORD_MAGIC, RawPacketWriter, RawReference, encode_record, iter_archive,
    read_packet_at,
)


