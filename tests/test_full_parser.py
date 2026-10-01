from __future__ import annotations

import struct
import unittest

from decoder.full_parser import PACKET_SIZES, decode_packet
from decoder.header import HEADER_STRUCT
from decoder.session_history import PlayerSessionHistoryTracker


def empty_packet(packet_id: int, session_uid: int = 77, player_index: int = 3) -> bytearray:
    packet = bytearray(PACKET_SIZES[packet_id])
    HEADER_STRUCT.pack_into(
        packet, 0, 2023, 23, 1, 0, 1, packet_id, session_uid,
        12.5, 750, 800, player_index, 255,
    )
    return packet


def history_packet(lap4_time: int, session_uid: int = 77) -> bytes:
    packet = empty_packet(11, session_uid)
    offset = HEADER_STRUCT.size
    struct.pack_into("<7B", packet, offset, 3, 5, 0, 3, 1, 2, 3)
    offset += 7
    lap_struct = struct.Struct("<IHBHBHBB")
    for index, lap_time in enumerate((90_000, 89_000, 88_000, lap4_time)):
        lap_struct.pack_into(
            packet, offset + index * lap_struct.size,
            lap_time, 30_000, 0, 29_000, 0, lap_time - 59_000, 0, 0x0F,
        )
    return bytes(packet)


class FullParserTests(unittest.TestCase):
    def test_every_official_packet_layout_consumes_exact_packet_size(self) -> None:
        for packet_id, size in PACKET_SIZES.items():
            with self.subTest(packet_id=packet_id):
                decoded = decode_packet(bytes(empty_packet(packet_id)))
                self.assertEqual(decoded["header"]["packet_id"], packet_id)
                self.assertEqual(size, len(empty_packet(packet_id)))

    def test_session_history_replaces_flashback_lap(self) -> None:
        tracker = PlayerSessionHistoryTracker()
        first = tracker.observe(history_packet(93_268), 1)
        self.assertIsNotNone(first)
        assert first is not None
        self.assertEqual(first.laps[-1].lap_number, 4)
        self.assertEqual(first.laps[-1].lap_time_ms, 93_268)

        replacement = tracker.observe(history_packet(93_478), 2)
        self.assertIsNotNone(replacement)
        assert replacement is not None
        self.assertEqual(len(replacement.laps), 4)
        self.assertEqual(replacement.laps[-1].lap_number, 4)
        self.assertEqual(replacement.laps[-1].lap_time_ms, 93_478)


if __name__ == "__main__":
    unittest.main()
