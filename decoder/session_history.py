"""Authoritative player lap history with flashback-safe replacement semantics."""

from __future__ import annotations

from dataclasses import dataclass

from decoder.header import HEADER_STRUCT, PacketHeader, decode_header
from decoder.protocol import protocol_for_format
from decoder.stream import decode_context, decode_player_record
from decoder.tyres import LapTyreInfo, PlayerTyreTracker


SESSION_HISTORY_PACKET_ID = 11


@dataclass(frozen=True, slots=True)
class LapRecord:
    lap_number: int
    lap_time_ms: int
    sector1_ms: int
    sector2_ms: int
    sector3_ms: int
    lap_valid: bool
    sector1_valid: bool
    sector2_valid: bool
    sector3_valid: bool
    updated_at_ns: int
    tyre: LapTyreInfo = LapTyreInfo()


@dataclass(frozen=True, slots=True)
class LapHistoryUpdate:
    session_uid: int
    laps: tuple[LapRecord, ...]


def _sector(milliseconds: int, minutes: int) -> int:
    return minutes * 60_000 + milliseconds


class PlayerSessionHistoryTracker:
    """Replace the displayed history whenever the game publishes a new snapshot.

    The games rewind Session History after a flashback. Rebuilding the whole list
    from the latest packet therefore removes invalid pre-flashback completions
    instead of appending a duplicate lap number.
    """

    def __init__(self) -> None:
        self._session_uid: int | None = None
        self._signature: tuple[tuple[object, ...], ...] = ()
        self._updated_times: dict[int, tuple[tuple[int, ...], int]] = {}
        self._tyre_key: tuple[int, int] | None = None
        self._tyres = PlayerTyreTracker()
        self._history: dict | None = None

    def observe(
        self,
        packet: bytes,
        received_at_ns: int,
        header: PacketHeader | None = None,
    ) -> LapHistoryUpdate | None:
        header = header or decode_header(packet)
        if header.packet_id not in PlayerTyreTracker.PACKET_IDS:
            return None
        try:
            expected_size = protocol_for_format(header.packet_format).packet_sizes[header.packet_id]
        except (ValueError, KeyError):
            return None
        # Only the player is displayed. AI packets are still archived by the
        # collector; avoid allocating 100 decoded lap dictionaries for each AI.
        # Malformed lengths continue through the normal decoder error path.
        if (
            header.packet_id in {11, 12}
            and len(packet) == expected_size
            and packet[HEADER_STRUCT.size] != header.player_car_index
        ):
            return None
        session_changed = (header.session_uid, header.player_car_index) != self._tyre_key
        if session_changed:
            self._tyre_key = (header.session_uid, header.player_car_index)
            self._session_uid = header.session_uid
            self._signature = ()
            self._updated_times.clear()
            self._tyres = PlayerTyreTracker()
            self._history = None

        decoded = (decode_player_record(packet, header) if header.packet_id in {2, 7, 10}
                   else decode_context(packet, header))
        if decoded is None:
            return None
        changed = self._tyres.observe(header, decoded)
        if header.packet_id == SESSION_HISTORY_PACKET_ID:
            self._history = decoded
        elif not changed or self._history is None:
            return None
        if self._tyres.invalidated_laps and self._history is not None:
            first = min(self._tyres.invalidated_laps)
            self._history = {**self._history, "num_laps": min(first - 1, self._history["num_laps"])}
        decoded = self._history
        tyre_laps = self._tyres.laps(decoded)

        laps: list[LapRecord] = []
        signature: list[tuple[object, ...]] = []
        for lap_number, entry in enumerate(
            decoded["lap_history"][: decoded["num_laps"]], start=1
        ):
            if entry["lap_time_ms"] <= 0:
                continue
            flags = entry["lap_valid_bit_flags"]
            values = (
                entry["lap_time_ms"],
                _sector(entry["sector1_time_ms"], entry["sector1_time_minutes"]),
                _sector(entry["sector2_time_ms"], entry["sector2_time_minutes"]),
                _sector(entry["sector3_time_ms"], entry["sector3_time_minutes"]),
                flags,
            )
            previous = self._updated_times.get(lap_number)
            updated_at = previous[1] if previous and previous[0] == values else received_at_ns
            self._updated_times[lap_number] = (values, updated_at)
            tyre = tyre_laps.get(lap_number, LapTyreInfo())
            signature.append((lap_number, *values, tyre))
            laps.append(
                LapRecord(
                    lap_number=lap_number,
                    lap_time_ms=values[0],
                    sector1_ms=values[1],
                    sector2_ms=values[2],
                    sector3_ms=values[3],
                    lap_valid=bool(flags & 0x01),
                    sector1_valid=bool(flags & 0x02),
                    sector2_valid=bool(flags & 0x04),
                    sector3_valid=bool(flags & 0x08),
                    updated_at_ns=updated_at,
                    tyre=tyre,
                )
            )

        current_signature = tuple(signature)
        if current_signature == self._signature and not session_changed:
            return None
        self._signature = current_signature
        active_numbers = {lap.lap_number for lap in laps}
        self._updated_times = {
            number: value
            for number, value in self._updated_times.items()
            if number in active_numbers
        }
        return LapHistoryUpdate(header.session_uid, tuple(laps))
