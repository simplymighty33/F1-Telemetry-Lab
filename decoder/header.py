"""Common 29-byte EA F1 23/24/25 UDP packet header decoding."""

from __future__ import annotations

from dataclasses import dataclass
import struct


HEADER_STRUCT = struct.Struct("<HBBBBBQfIIBB")
HEADER_SIZE = HEADER_STRUCT.size


class HeaderDecodeError(ValueError):
    """Raised when a datagram cannot contain a complete common header."""


@dataclass(frozen=True, slots=True)
class PacketHeader:
    packet_format: int
    game_year: int
    game_major_version: int
    game_minor_version: int
    packet_version: int
    packet_id: int
    session_uid: int
    session_time: float
    frame_identifier: int
    overall_frame_identifier: int
    player_car_index: int
    secondary_player_car_index: int


def decode_header(packet: bytes | bytearray | memoryview) -> PacketHeader:
    """Decode the packed common header without interpreting the packet body."""
    if len(packet) < HEADER_SIZE:
        raise HeaderDecodeError(
            f"packet is {len(packet)} bytes; F1 UDP header requires {HEADER_SIZE}"
        )
    return PacketHeader(*HEADER_STRUCT.unpack_from(packet))

