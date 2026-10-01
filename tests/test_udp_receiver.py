from __future__ import annotations

import socket
import unittest

from collector.udp_receiver import UdpReceiver


class UdpReceiverTests(unittest.TestCase):
    def test_receives_datagram_and_source(self) -> None:
        with UdpReceiver("127.0.0.1", 0, timeout_seconds=1.0) as receiver:
            sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sender.sendto(b"telemetry", ("127.0.0.1", receiver.bound_port))
                datagram = receiver.receive()
            finally:
                sender.close()
        self.assertIsNotNone(datagram)
        assert datagram is not None
        self.assertEqual(datagram.payload, b"telemetry")
        self.assertEqual(datagram.source_ip, "127.0.0.1")
        self.assertGreater(datagram.received_at_ns, 0)


if __name__ == "__main__":
    unittest.main()

