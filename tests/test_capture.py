from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from collector.packet_capture import PacketCapture
from collector.udp_receiver import ReceivedDatagram
from decoder.header import HEADER_STRUCT
from storage.raw_writer import iter_archive


def packet(packet_id: int, frame: int) -> bytes:
    return HEADER_STRUCT.pack(
        2023, 23, 1, 0, 1, packet_id, 18_446_744_073_709_551_000,
        frame / 60, frame, frame + 10, 4, 255
    ) + bytes([packet_id]) * 32


class CaptureTests(unittest.TestCase):
    def test_raw_first_capture_and_database_index(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            capture = PacketCapture(root, 20777, metadata_checkpoint_every=2)
            payloads = [packet(0, 100), packet(6, 100), b"short"]
            for index, payload in enumerate(payloads):
                capture.process(
                    ReceivedDatagram(
                        received_at_ns=1_800_000_000_000_000_000 + index,
                        monotonic_ns=2_000_000_000 + index * 16_666_667,
                        source_ip="127.0.0.1",
                        source_port=20777,
                        payload=payload,
                    )
                )
            summary = capture.close()
            session = capture.session_directory

            self.assertEqual(summary["packet_count"], 3)
            self.assertEqual(summary["parse_error_count"], 1)
            archived = list(iter_archive(session / "raw_packets.bin"))
            self.assertEqual([item.payload for item in archived], payloads)
            self.assertTrue(all(item.source_ip == "127.0.0.1" for item in archived))

            with closing(sqlite3.connect(session / "telemetry.db")) as connection:
                rows = connection.execute(
                    "SELECT packet_id, frame_identifier, size, session_uid FROM packets ORDER BY id"
                ).fetchall()
                db_session = connection.execute(
                    "SELECT status, packet_count FROM sessions"
                ).fetchone()
            self.assertEqual(rows[0][0:3], (0, 100, len(payloads[0])))
            self.assertEqual(rows[1][0:3], (6, 100, len(payloads[1])))
            self.assertEqual(rows[2][0], None)
            self.assertEqual(rows[0][3], "18446744073709551000")
            self.assertEqual(db_session, ("complete", 3))

            metadata = json.loads((session / "metadata.json").read_text("utf-8"))
            self.assertEqual(metadata["packet_count"], 3)
            self.assertEqual(metadata["summary"]["status"], "complete")


if __name__ == "__main__":
    unittest.main()

