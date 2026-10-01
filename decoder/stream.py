"""Shared versioned player/context decoding for live ingestion and Replay."""

from __future__ import annotations

import math
from typing import Any

from decoder.full_parser import (
    CAR_DAMAGE_SCHEMA,
    CAR_SETUP_SCHEMA,
    CAR_STATUS_SCHEMA,
    CAR_TELEMETRY_SCHEMA,
    LAP_SCHEMA,
    MOTION_EX_SCHEMA,
    MOTION_SCHEMA,
    PacketDecodeError,
    Schema,
    _read_schema,
    canonical_player_schema,
    compiled_schema,
    decode_packet,
    normalize_player_record,
    player_wire_schema,
)
from decoder.header import HEADER_SIZE, PacketHeader
from decoder.protocol import protocol_for_format


# Compatibility view used by existing F1 23 callers.
PLAYER_SCHEMAS: dict[int, Schema] = {
    0: MOTION_SCHEMA, 2: LAP_SCHEMA, 5: CAR_SETUP_SCHEMA,
    6: CAR_TELEMETRY_SCHEMA, 7: CAR_STATUS_SCHEMA,
    10: CAR_DAMAGE_SCHEMA, 13: MOTION_EX_SCHEMA,
}

# Stable superset stored by the disposable multi-game foundation cache.
FOUNDATION_SCHEMAS: dict[int, Schema] = {
    packet_id: schema
    for packet_id in (0, 2, 5, 6, 7, 10, 13, 16)
    if (schema := canonical_player_schema(packet_id)) is not None
}
DECODER_VERSION = 2


def validate_packet(packet: bytes, header: PacketHeader) -> None:
    if not math.isfinite(header.session_time):
        raise PacketDecodeError("non-finite session time")
    try:
        profile = protocol_for_format(header.packet_format)
    except ValueError as exc:
        raise PacketDecodeError(str(exc)) from exc
    if header.packet_version != 1:
        raise PacketDecodeError(f"unsupported packet version {header.packet_version}")
    expected = profile.packet_sizes.get(header.packet_id)
    if expected is None or len(packet) != expected:
        raise PacketDecodeError(
            f"format {header.packet_format} packet {header.packet_id} has {len(packet)} bytes; expected {expected}"
        )


def decode_player_record(packet: bytes, header: PacketHeader) -> dict[str, Any] | None:
    validate_packet(packet, header)
    profile = protocol_for_format(header.packet_format)
    if not 0 <= header.player_car_index < profile.car_count:
        return None
    wire_schema = player_wire_schema(header.packet_format, header.packet_id)
    if wire_schema is None:
        return None
    size = compiled_schema(wire_schema)[0].size
    offset = HEADER_SIZE if header.packet_id == 13 else HEADER_SIZE + header.player_car_index * size
    return _read_schema(packet, offset, wire_schema)[0]


def decode_foundation_record(packet: bytes, header: PacketHeader) -> dict[str, Any] | None:
    body = decode_player_record(packet, header)
    return None if body is None else normalize_player_record(body, header.packet_id)


def decode_context(packet: bytes, header: PacketHeader) -> dict[str, Any] | None:
    validate_packet(packet, header)
    if header.packet_id in {11, 12} and packet[HEADER_SIZE] != header.player_car_index:
        return None
    result = decode_packet(packet, header)
    return {name: value for name, value in result.items() if name not in {"header", "packet_name"}}
