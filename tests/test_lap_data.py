from __future__ import annotations

import struct
import unittest

from decoder.header import HEADER_STRUCT
from decoder.lap_data import (
    LAP_DATA_SIZE,
    PlayerLapTracker,
    decode_player_lap_data,
)


def lap_packet(
    current_lap_number: int,
    last_lap_time_ms: int,
    current_lap_time_ms: int,
    player_index: int = 3,
) -> bytes:
    packet = bytearray(1131)
    packet[: HEADER_STRUCT.size] = HEADER_STRUCT.pack(
        2023, 23, 1, 0, 1, 2, 987654321, current_lap_time_ms / 1000,
        current_lap_time_ms, current_lap_time_ms, player_index, 255,
    )
    offset = HEADER_STRUCT.size + player_index * LAP_DATA_SIZE
    struct.pack_into("<II", packet, offset, last_lap_time_ms, current_lap_time_ms)
    packet[offset + 30] = 5
    packet[offset + 31] = current_lap_number
    packet[offset + 35] = 0
    return bytes(packet)


class LapDataTests(unittest.TestCase):
    def test_decodes_player_record_by_header_index(self) -> None:
        decoded = decode_player_lap_data(lap_packet(4, 91_234, 12_345))
        self.assertEqual(decoded.player_car_index, 3)
        self.assertEqual(decoded.current_lap_number, 4)
        self.assertEqual(decoded.last_lap_time_ms, 91_234)
        self.assertEqual(decoded.current_lap_time_ms, 12_345)
        self.assertEqual(decoded.car_position, 5)

    def test_tracker_emits_once_when_lap_rolls_over(self) -> None:
        tracker = PlayerLapTracker()
        self.assertIsNone(tracker.observe(lap_packet(1, 0, 88_000), 1))
        completed = tracker.observe(lap_packet(2, 90_123, 120), 2)
        self.assertIsNotNone(completed)
        assert completed is not None
        self.assertEqual(completed.lap_number, 1)
        self.assertEqual(completed.lap_time_ms, 90_123)
        self.assertIsNone(tracker.observe(lap_packet(2, 90_123, 300), 3))


if __name__ == "__main__":
    unittest.main()
