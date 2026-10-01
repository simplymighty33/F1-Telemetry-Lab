from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3
import struct
import tempfile
import unittest

from analysis.build import build_analysis
from decoder.full_parser import PACKET_SIZES
from decoder.header import HEADER_STRUCT
from storage.raw_writer import RawPacketWriter


SESSION_UID = 77


def empty_packet(
    packet_id: int,
    frame: int,
    overall: int,
    session_time: float,
) -> bytearray:
    packet = bytearray(PACKET_SIZES[packet_id])
    HEADER_STRUCT.pack_into(
        packet, 0, 2023, 23, 1, 0, 1, packet_id, SESSION_UID,
        session_time, frame, overall, 0, 255,
    )
    return packet


def session_packet() -> bytes:
    packet = empty_packet(1, 0, 0, 0.0)
    packet[32] = 1
    struct.pack_into("<H", packet, 33, 100)
    packet[35] = 13
    packet[36] = 0
    return bytes(packet)


def motion_packet(frame: int, overall: int, distance: float) -> bytes:
    packet = empty_packet(0, frame, overall, frame / 10)
    struct.pack_into(
        "<6f6h6f", packet, HEADER_STRUCT.size,
        distance, 0.0, distance / 2, 10.0, 0.0, 0.0,
        0, 0, 0, 0, 0, 0,
        0.1, 0.2, 1.0, 0.0, 0.0, 0.0,
    )
    return bytes(packet)


def lap_packet(frame: int, overall: int, distance: float) -> bytes:
    packet = empty_packet(2, frame, overall, frame / 10)
    offset = HEADER_STRUCT.size
    struct.pack_into("<I", packet, offset + 4, int(distance * 100))
    struct.pack_into("<f", packet, offset + 18, distance)
    struct.pack_into("<f", packet, offset + 22, distance)
    packet[offset + 31] = 1
    packet[offset + 34] = min(2, int(distance // 34))
    packet[offset + 42] = 4
    return bytes(packet)


def telemetry_packet(frame: int, overall: int, distance: float, replacement: bool) -> bytes:
    packet = empty_packet(6, frame, overall, frame / 10)
    offset = HEADER_STRUCT.size
    speed = int((200 if replacement else 100) + distance)
    struct.pack_into("<H", packet, offset, speed)
    throttle = 0.0 if distance <= 50 else min(1.0, (distance - 50) / 50)
    brake = 0.8 if 20 <= distance <= 40 else (0.2 if distance == 50 else 0.0)
    steer = {-1: -0.2, 0: 0.0, 1: 0.2}[int(distance // 20) % 3 - 1]
    struct.pack_into("<f", packet, offset + 2, throttle)
    struct.pack_into("<f", packet, offset + 6, steer)
    struct.pack_into("<f", packet, offset + 10, brake)
    struct.pack_into("<b", packet, offset + 15, 3 if distance < 60 else 4)
    struct.pack_into("<H", packet, offset + 16, 10_000)
    return bytes(packet)


def flashback_packet(overall: int, target_frame: int) -> bytes:
    packet = empty_packet(3, 11, overall, 1.1)
    packet[HEADER_STRUCT.size:HEADER_STRUCT.size + 4] = b"FLBK"
    struct.pack_into("<If", packet, HEADER_STRUCT.size + 4, target_frame, 0.5)
    return bytes(packet)


def history_packet(overall: int) -> bytes:
    packet = empty_packet(11, 11, overall, 1.1)
    offset = HEADER_STRUCT.size
    struct.pack_into("<7B", packet, offset, 0, 1, 0, 1, 1, 1, 1)
    offset += 7
    struct.pack_into("<IHBHBHBB", packet, offset, 10_000, 3_000, 0, 3_000, 0, 4_000, 0, 0x0F)
    return bytes(packet)


class AnalysisPipelineTests(unittest.TestCase):
    def test_flashback_replaces_branch_and_lap_is_resampled(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            session = root / "session_test"
            session.mkdir()
            raw = session / "raw_packets.bin"
            received = 1_800_000_000_000_000_000
            with RawPacketWriter(raw, flush_every=1, compression="zlib") as writer:
                packets = [
                    session_packet(),
                    motion_packet(0, 0, 100.0),
                    lap_packet(0, 0, 100.0),
                    telemetry_packet(0, 0, 100.0, False),
                ]
                for frame, distance in enumerate(range(0, 101, 10), 1):
                    packets.extend(
                        (
                            motion_packet(frame, frame, float(distance)),
                            lap_packet(frame, frame, float(distance)),
                            telemetry_packet(frame, frame, float(distance), False),
                        )
                    )
                packets.append(flashback_packet(12, 5))
                for frame, distance in enumerate(range(50, 101, 10), 6):
                    overall = frame + 7
                    packets.extend(
                        (
                            motion_packet(frame, overall, float(distance)),
                            lap_packet(frame, overall, float(distance)),
                            telemetry_packet(frame, overall, float(distance), True),
                        )
                    )
                packets.append(history_packet(20))
                for index, payload in enumerate(packets):
                    writer.write(payload, received + index, index, "127.0.0.1", 20777)

            output = root / "analysis"
            summary = build_analysis(session, output=output, distance_step_m=10)
            self.assertEqual(summary["flashback_count"], 1)
            self.assertEqual(summary["superseded_telemetry_samples"], 6)
            self.assertEqual(summary["active_telemetry_samples"], 12)
            self.assertEqual(summary["laps_resampled"], 1)
            self.assertEqual(summary["braking_event_count"], 1)
            self.assertEqual(summary["throttle_event_count"], 1)
            self.assertEqual(summary["gear_shift_event_count"], 1)
            self.assertEqual(summary["lap_metric_count"], 1)

            with closing(sqlite3.connect(output / "telemetry_analysis.db")) as connection:
                speed = connection.execute(
                    """
                    SELECT speed_kph FROM resampled_lap_samples
                    WHERE session_uid = ? AND lap_number = 1 AND distance_m = 100
                    """,
                    (str(SESSION_UID),),
                ).fetchone()[0]
                status = connection.execute(
                    "SELECT quality_status FROM lap_analysis"
                ).fetchone()[0]
                metrics = connection.execute(
                    """
                    SELECT braking_event_count, throttle_event_count,
                           upshift_count, delta_to_best_ms
                    FROM lap_metrics
                    """
                ).fetchone()
            self.assertEqual(speed, 300)
            self.assertEqual(status, "ready")
            self.assertEqual(metrics, (1, 1, 1, 0))


if __name__ == "__main__":
    unittest.main()
