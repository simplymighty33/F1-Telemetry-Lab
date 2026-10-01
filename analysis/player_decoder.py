"""Fast player-only decoding for high-frequency car-array packets."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from decoder.full_parser import Schema
from decoder.header import PacketHeader
from decoder.stream import PLAYER_SCHEMAS


PLAYER_ARRAY_SCHEMAS: dict[int, Schema] = {
    packet_id: schema for packet_id, schema in PLAYER_SCHEMAS.items()
    if packet_id not in {13}
}


@lru_cache(maxsize=128)
def schema_size(schema: Schema) -> int:
    from decoder.full_parser import compiled_schema
    return compiled_schema(schema)[0].size


def decode_player_record(packet: bytes, header: PacketHeader) -> dict[str, Any] | None:
    """Compatibility entry; live ingestion and Replay share one implementation."""
    from decoder.stream import decode_player_record as shared_decode
    return shared_decode(packet, header)
