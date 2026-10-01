from __future__ import annotations

from contextlib import redirect_stdout
import io
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest

from collector.inspect_session import inspect
from collector.main import main
from decoder.header import HEADER_STRUCT


class CliEndToEndTests(unittest.TestCase):
    def test_udp_to_archive_to_verified_summary(self) -> None:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        data_root = root / "data"
        log_root = root / "logs"
        expected = 20

        def send_packets() -> None:
            time.sleep(0.1)
            sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                for frame in range(expected):
                    payload = HEADER_STRUCT.pack(
                        2023, 23, 1, 0, 1, 6, 123456, frame / 60,
                        frame, frame, 0, 255
                    ) + b"synthetic-body"
                    sender.sendto(payload, ("127.0.0.1", port))
                    time.sleep(0.002)
            finally:
                sender.close()

        sender_thread = threading.Thread(target=send_packets)
        sender_thread.start()
        try:
            with redirect_stdout(io.StringIO()):
                result = main(
                    [
                        "--host", "127.0.0.1",
                        "--port", str(port),
                        "--data-dir", str(data_root),
                        "--log-dir", str(log_root),
                        "--duration", "0.7",
                    ]
                )
            sender_thread.join(timeout=2)
            self.assertEqual(result, 0)
            sessions = list(data_root.glob("session_*"))
            self.assertEqual(len(sessions), 1)
            report = inspect(sessions[0], verify_raw=True)
            self.assertEqual(report["indexed_packet_count"], expected)
            self.assertEqual(report["raw_packet_count"], expected)
            self.assertTrue(report["raw_crc_valid"])
            self.assertTrue(report["raw_matches_index"])
        finally:
            sender_thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
