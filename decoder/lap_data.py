"""Minimal versioned Lap Data decoder used by the live stability display."""

from __future__ import annotations

from dataclasses import dataclass
from decoder.full_parser import _read_schema, compiled_schema, player_wire_schema
from decoder.header import HEADER_SIZE, PacketHeader, decode_header
from decoder.protocol import protocol_for_format
from decoder.stream import validate_packet


LAP_DATA_PACKET_ID = 2
LAP_DATA_SIZE = 50  # F1 23 compatibility constant
CAR_COUNT = 22      # F1 23 compatibility constant


class LapDataDecodeError(ValueError):
    """Raised when a packet cannot contain the requested player's lap data."""


@dataclass(frozen=True, slots=True)
class PlayerLapData:
    session_uid: int
    overall_frame_identifier: int
    player_car_index: int
    last_lap_time_ms: int
    current_lap_time_ms: int
    car_position: int
    current_lap_number: int
    current_lap_invalid: bool


@dataclass(frozen=True, slots=True)
class CompletedLap:
    lap_number: int
    lap_time_ms: int
    received_at_ns: int


def decode_player_lap_data(
    packet: bytes | bytearray | memoryview,
    header: PacketHeader | None = None,
) -> PlayerLapData:
    header = header or decode_header(packet)
    if header.packet_id != LAP_DATA_PACKET_ID:
        raise LapDataDecodeError("packet is not Lap Data")
    try:
        validate_packet(bytes(packet), header)
        profile = protocol_for_format(header.packet_format)
    except ValueError as exc:
        raise LapDataDecodeError(str(exc)) from exc
    if not 0 <= header.player_car_index < profile.car_count:
        raise LapDataDecodeError("player car index is not available")
    schema = player_wire_schema(header.packet_format, LAP_DATA_PACKET_ID)
    if schema is None:
        raise LapDataDecodeError("Lap Data schema is not available")
    offset = HEADER_SIZE + header.player_car_index * compiled_schema(schema)[0].size
    values, _ = _read_schema(bytes(packet), offset, schema)
    return PlayerLapData(
        session_uid=header.session_uid,
        overall_frame_identifier=header.overall_frame_identifier,
        player_car_index=header.player_car_index,
        last_lap_time_ms=values["last_lap_time_ms"],
        current_lap_time_ms=values["current_lap_time_ms"],
        car_position=values["car_position"],
        current_lap_number=values["current_lap_num"],
        current_lap_invalid=bool(values["current_lap_invalid"]),
    )


class PlayerLapTracker:
    """Emit one display record when the player's current lap rolls over."""

    def __init__(self) -> None:
        self._session_uid: int | None = None
        self._previous_current_time_ms: int | None = None
        self._last_emitted_lap = 0
        self._last_emitted_time_ms = 0

    def observe(
        self,
        packet: bytes,
        received_at_ns: int,
        header: PacketHeader | None = None,
    ) -> CompletedLap | None:
        header = header or decode_header(packet)
        if header.packet_id != LAP_DATA_PACKET_ID:
            return None
        lap = decode_player_lap_data(packet, header)
        if lap.session_uid != self._session_uid:
            self._session_uid = lap.session_uid
            self._previous_current_time_ms = None
            self._last_emitted_lap = 0
            self._last_emitted_time_ms = 0

        rolled_over = (
            self._previous_current_time_ms is not None
            and self._previous_current_time_ms >= 5_000
            and lap.current_lap_time_ms < self._previous_current_time_ms
        )
        completed_number = max(0, lap.current_lap_number - 1)
        started_mid_session = (
            self._previous_current_time_ms is None
            and completed_number > 0
            and lap.last_lap_time_ms > 0
        )
        self._previous_current_time_ms = lap.current_lap_time_ms

        if lap.last_lap_time_ms <= 0 or not (rolled_over or started_mid_session):
            return None
        if completed_number <= 0:
            completed_number = self._last_emitted_lap + 1
        if (
            completed_number == self._last_emitted_lap
            and lap.last_lap_time_ms == self._last_emitted_time_ms
        ):
            return None
        self._last_emitted_lap = completed_number
        self._last_emitted_time_ms = lap.last_lap_time_ms
        return CompletedLap(completed_number, lap.last_lap_time_ms, received_at_ns)
