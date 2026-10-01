"""Packet type labels shared by the supported EA F1 UDP formats."""

from __future__ import annotations


PACKET_NAMES: dict[int, str] = {
    0: "Motion",
    1: "Session",
    2: "Lap Data",
    3: "Event",
    4: "Participants",
    5: "Car Setups",
    6: "Car Telemetry",
    7: "Car Status",
    8: "Final Classification",
    9: "Lobby Info",
    10: "Car Damage",
    11: "Session History",
    12: "Tyre Sets",
    13: "Motion Ex",
    14: "Time Trial",
    15: "Lap Positions",
    16: "Car Telemetry 2",
}


def packet_name(packet_id: int | None) -> str:
    if packet_id is None:
        return "Unparsed"
    return PACKET_NAMES.get(packet_id, f"Unknown ({packet_id})")

