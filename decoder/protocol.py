"""EA F1 UDP protocol registry and automatic game/protocol identification.

The common 29-byte header is stable across the supported formats.  Body
layouts are selected exclusively from ``packetFormat``; ``gameYear`` is used
for the user-facing game label because a newer game can intentionally emit an
older compatibility format.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from decoder.header import PacketHeader


@dataclass(frozen=True, slots=True)
class ProtocolProfile:
    packet_format: int
    protocol_label: str
    native_game_label: str
    car_count: int
    packet_sizes: Mapping[int, int]


def _sizes(values: dict[int, int]) -> Mapping[int, int]:
    return MappingProxyType(values)


PROTOCOLS: Mapping[int, ProtocolProfile] = MappingProxyType({
    2023: ProtocolProfile(
        2023, "2023", "F1 23", 22,
        _sizes({
            0: 1349, 1: 644, 2: 1131, 3: 45, 4: 1306, 5: 1107,
            6: 1352, 7: 1239, 8: 1020, 9: 1218, 10: 953,
            11: 1460, 12: 231, 13: 217,
        }),
    ),
    2024: ProtocolProfile(
        2024, "2024", "F1 24", 22,
        _sizes({
            0: 1349, 1: 753, 2: 1285, 3: 45, 4: 1350, 5: 1133,
            6: 1352, 7: 1239, 8: 1020, 9: 1306, 10: 953,
            11: 1460, 12: 231, 13: 237, 14: 101,
        }),
    ),
    2025: ProtocolProfile(
        2025, "2025", "F1 25", 22,
        _sizes({
            0: 1349, 1: 753, 2: 1285, 3: 45, 4: 1284, 5: 1133,
            6: 1352, 7: 1239, 8: 1042, 9: 954, 10: 1041,
            11: 1460, 12: 231, 13: 273, 14: 101, 15: 1131,
        }),
    ),
    # F1 25 can emit this natively after installing the 2026 Season Pack.
    2026: ProtocolProfile(
        2026, "2026 Season Pack", "F1 25 - 2026 Season Pack", 24,
        _sizes({
            0: 1469, 1: 926, 2: 1399, 3: 45, 4: 1470, 5: 1233,
            6: 1472, 7: 1445, 8: 1134, 9: 1062, 10: 1133,
            11: 1460, 12: 231, 13: 273, 14: 104, 15: 1231, 16: 269,
        }),
    ),
})


SUPPORTED_PACKET_FORMATS = tuple(PROTOCOLS)


def protocol_for_format(packet_format: int) -> ProtocolProfile:
    try:
        return PROTOCOLS[packet_format]
    except KeyError as exc:
        supported = ", ".join(str(value) for value in SUPPORTED_PACKET_FORMATS)
        raise ValueError(
            f"unsupported packet format {packet_format}; supported formats: {supported}"
        ) from exc


def game_label(header: PacketHeader) -> str:
    """Return the detected game while retaining compatibility-format detail."""
    profile = protocol_for_format(header.packet_format)
    by_year = {
        23: "F1 23",
        24: "F1 24",
        25: "F1 25",
        26: "F1 25 - 2026 Season Pack",
    }
    label = by_year.get(header.game_year, profile.native_game_label)
    native_year = {2023: 23, 2024: 24, 2025: 25, 2026: 26}[profile.packet_format]
    if header.game_year not in {0, native_year}:
        return f"{label} ({profile.protocol_label} protocol)"
    return label

