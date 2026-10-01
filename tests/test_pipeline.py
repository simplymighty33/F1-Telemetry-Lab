from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest

from collector.packet_capture import PacketCapture
from collector.pipeline import CapturePipeline
from collector.udp_receiver import ReceivedDatagram
from decoder.header import HEADER_STRUCT
from storage.raw_writer import iter_archive


class PipelineTests(unittest.TestCase):
    def test_idle_pipeline_persists_an_incomplete_block(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            capture = PacketCapture(Path(temporary), 20777)
            pipeline = CapturePipeline(capture)
            pipeline.start()
            try:
                pipeline.submit(ReceivedDatagram(1, 2, "127.0.0.1", 20777, b"short"))
                deadline = time.monotonic() + 3.0
                while time.monotonic() < deadline:
                    if capture.raw_writer.persisted_packet_count == 1:
                        break
                    time.sleep(0.02)
                raw = capture.session_directory / "raw_packets.bin"
                self.assertEqual([packet.payload for packet in iter_archive(raw)], [b"short"])
            finally:
                pipeline.close()

    def test_pipeline_drains_queue_before_close(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            capture = PacketCapture(Path(temporary), 20777)
            pipeline = CapturePipeline(capture, capacity=512)
            pipeline.start()
            for frame in range(300):
                payload = HEADER_STRUCT.pack(
                    2023, 23, 1, 0, 1, 6, 123, frame / 60,
                    frame, frame, 0, 255,
                ) + b"body"
                pipeline.submit(
                    ReceivedDatagram(
                        received_at_ns=1_800_000_000_000_000_000 + frame,
                        monotonic_ns=2_000_000_000 + frame,
                        source_ip="127.0.0.1",
                        source_port=20777,
                        payload=payload,
                    )
                )
            summary = pipeline.close()
            self.assertEqual(summary["packet_count"], 300)
            self.assertEqual(summary["software_drop_count"], 0)
            self.assertEqual(
                sum(1 for _ in iter_archive(capture.session_directory / "raw_packets.bin")),
                300,
            )
            with closing(sqlite3.connect(capture.session_directory / "telemetry.db")) as connection:
                indexed = connection.execute("SELECT COUNT(*) FROM packets").fetchone()[0]
            self.assertEqual(indexed, 300)


if __name__ == "__main__":
    unittest.main()
