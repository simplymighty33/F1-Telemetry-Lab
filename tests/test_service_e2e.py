from __future__ import annotations

from contextlib import closing
import logging
from pathlib import Path
import socket
import sqlite3
import tempfile
import time
import unittest

from collector.service import CollectorService
from collector.settings import load_settings
from decoder.header import HEADER_STRUCT
from storage.raw_writer import iter_archive


class ServiceEndToEndTests(unittest.TestCase):
    def test_background_service_persists_more_than_one_database_batch(self) -> None:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = load_settings(root / "config" / "settings.json")
            logger = logging.getLogger(f"service-test-{port}")
            logger.handlers.clear()
            logger.addHandler(logging.NullHandler())
            logger.propagate = False
            service = CollectorService(settings, root / "data", logger, "127.0.0.1", port)
            service.start()
            deadline = time.monotonic() + 3
            while service.snapshot().state == "starting" and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(service.snapshot().state, "listening")

            sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                for frame in range(400):
                    payload = HEADER_STRUCT.pack(
                        2023, 23, 1, 0, 1, 6, 456789, frame / 60,
                        frame, frame, 0, 255,
                    ) + b"service-e2e"
                    sender.sendto(payload, ("127.0.0.1", port))
            finally:
                sender.close()

            deadline = time.monotonic() + 5
            while service.snapshot().received_packets < 400 and time.monotonic() < deadline:
                time.sleep(0.01)
            service.stop()
            self.assertTrue(service.wait(5))
            snapshot = service.snapshot()
            self.assertEqual(snapshot.state, "stopped")
            self.assertIsNone(snapshot.error)
            self.assertEqual(snapshot.received_packets, 400)
            self.assertEqual(snapshot.persisted_packets, 400)
            assert snapshot.session_directory is not None
            self.assertEqual(
                sum(1 for _ in iter_archive(snapshot.session_directory / "raw_packets.bin")),
                400,
            )
            with closing(sqlite3.connect(snapshot.session_directory / "telemetry.db")) as connection:
                indexed = connection.execute("SELECT COUNT(*) FROM packets").fetchone()[0]
            self.assertEqual(indexed, 400)


if __name__ == "__main__":
    unittest.main()
