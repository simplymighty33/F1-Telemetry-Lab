from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from decoder.full_parser import (
    CAR_TELEMETRY_2_SCHEMA,
    LAP_SCHEMA_MODERN,
    _read_schema,
    compiled_schema,
    decode_packet,
    player_wire_schema,
)
from decoder.header import HEADER_SIZE, HEADER_STRUCT, decode_header
from decoder.protocol import PROTOCOLS, game_label, protocol_for_format
from decoder.stream import decode_foundation_record, decode_player_record, validate_packet
from storage.foundation import ensure_foundation
from storage.raw_archive import RawPacketWriter


def empty_packet(packet_format: int, packet_id: int, player_index: int = 0,
                 game_year: int | None = None) -> bytearray:
    profile = protocol_for_format(packet_format)
    packet = bytearray(profile.packet_sizes[packet_id])
    HEADER_STRUCT.pack_into(
        packet, 0, packet_format, game_year if game_year is not None else packet_format % 100,
        1, 0, 1, packet_id, 123, 1.5, 10, 12, player_index, 255,
    )
    return packet


class ProtocolVersionTests(unittest.TestCase):
    def test_every_supported_layout_consumes_its_official_size(self):
        for packet_format, profile in PROTOCOLS.items():
            with self.subTest(packet_format=packet_format):
                self.assertEqual(HEADER_SIZE, 29)
                for packet_id, expected_size in profile.packet_sizes.items():
                    packet = bytes(empty_packet(packet_format, packet_id))
                    self.assertEqual(len(packet), expected_size)
                    validate_packet(packet, decode_header(packet))
                    decoded = decode_packet(packet)
                    self.assertEqual(decoded["header"]["packet_format"], packet_format)

    def test_auto_detection_distinguishes_game_from_compatibility_protocol(self):
        native = decode_header(empty_packet(2025, 0, game_year=25))
        compatible = decode_header(empty_packet(2023, 0, game_year=25))
        season_pack = decode_header(empty_packet(2026, 0, game_year=26))
        self.assertEqual(game_label(native), "F1 25")
        self.assertEqual(game_label(compatible), "F1 25 (2023 protocol)")
        self.assertEqual(game_label(season_pack), "F1 25 - 2026 Season Pack")

    def test_modern_lap_offsets_and_f1_23_compatibility_are_separate(self):
        packet = empty_packet(2025, 2, player_index=1)
        schema = player_wire_schema(2025, 2)
        size = compiled_schema(schema)[0].size
        values = [0] * len(compiled_schema(schema)[0].unpack(bytes(size)))
        fields = compiled_schema(schema)[1]
        starts = {name: start for name, start, _ in fields}
        values[starts["current_lap_time_ms"]] = 81_234
        values[starts["delta_to_car_in_front_minutes"]] = 2
        values[starts["speed_trap_fastest_speed"]] = 341.25
        compiled_schema(schema)[0].pack_into(packet, HEADER_SIZE + size, *values)
        decoded = decode_player_record(bytes(packet), decode_header(packet))
        self.assertEqual(decoded["current_lap_time_ms"], 81_234)
        self.assertEqual(decoded["delta_to_car_in_front_minutes"], 2)
        self.assertAlmostEqual(decoded["speed_trap_fastest_speed"], 341.25)

        old = bytes(empty_packet(2023, 2))
        old_record = decode_player_record(old, decode_header(old))
        self.assertNotIn("speed_trap_fastest_speed", old_record)
        canonical = decode_foundation_record(old, decode_header(old))
        self.assertIsNone(canonical["speed_trap_fastest_speed"])

    def test_2026_supports_24_cars_and_telemetry_2(self):
        packet = empty_packet(2026, 16, player_index=23)
        parser = compiled_schema(CAR_TELEMETRY_2_SCHEMA)[0]
        parser.pack_into(packet, HEADER_SIZE + 23 * parser.size, 1, 1, 250, 1, 1, 400, 1, 0)
        decoded = decode_packet(bytes(packet))
        self.assertEqual(len(decoded["cars"]), 24)
        self.assertEqual(decoded["cars"][23]["active_aero_activation_distance"], 250)
        player = decode_player_record(bytes(packet), decode_header(packet))
        self.assertEqual(player["overtake_activation_distance"], 400)

    def test_modern_session_packet_exposes_new_boundaries(self):
        decoded_2024 = decode_packet(bytes(empty_packet(2024, 1)))
        decoded_2026 = decode_packet(bytes(empty_packet(2026, 1)))
        self.assertEqual(len(decoded_2024["weather_forecast_samples"]), 64)
        self.assertIn("sector2_lap_distance_start", decoded_2024)
        self.assertEqual(len(decoded_2026["active_aero_zones_full"]), 8)
        self.assertEqual(len(decoded_2026["drs_zones"]), 4)

    def test_foundation_accepts_each_native_player_layout(self):
        with tempfile.TemporaryDirectory() as temporary:
            raw = Path(temporary) / "mixed.bin"
            packets = [
                bytes(empty_packet(2023, 10)),
                bytes(empty_packet(2024, 2)),
                bytes(empty_packet(2025, 13)),
                bytes(empty_packet(2026, 16)),
            ]
            with RawPacketWriter(raw, flush_every=1) as writer:
                for index, packet in enumerate(packets):
                    writer.write(packet, 1_800_000_000_000_000_000 + index,
                                 index, "127.0.0.1", 20777)
            result = ensure_foundation(raw)
            self.assertEqual(result["packets"], 4)
            self.assertEqual(result["errors"], 0)
            with closing(sqlite3.connect(result["database"])) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM damage").fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM lap").fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM motion_ex").fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM telemetry_2").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
