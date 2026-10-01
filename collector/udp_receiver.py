"""Low-overhead UDP receiver for telemetry datagrams."""

from __future__ import annotations

from dataclasses import dataclass
import socket
import time
from typing import Iterator


MAX_UDP_DATAGRAM = 65_535


@dataclass(frozen=True, slots=True)
class ReceivedDatagram:
    received_at_ns: int
    monotonic_ns: int
    source_ip: str
    source_port: int
    payload: bytes


class UdpReceiver:
    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 20_777,
        receive_buffer_bytes: int = 4 * 1024 * 1024,
        timeout_seconds: float = 0.5,
    ) -> None:
        self.host = host
        self.port = port
        self.receive_buffer_bytes = receive_buffer_bytes
        self.timeout_seconds = timeout_seconds
        self._socket: socket.socket | None = None

    @property
    def actual_receive_buffer_bytes(self) -> int:
        if self._socket is None:
            return 0
        return int(self._socket.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF))

    @property
    def bound_port(self) -> int:
        if self._socket is None:
            return self.port
        return int(self._socket.getsockname()[1])

    def open(self) -> None:
        if self._socket is not None:
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, self.receive_buffer_bytes)
            sock.settimeout(self.timeout_seconds)
            sock.bind((self.host, self.port))
        except Exception:
            sock.close()
            raise
        self._socket = sock

    def receive(self) -> ReceivedDatagram | None:
        if self._socket is None:
            raise RuntimeError("receiver is not open")
        try:
            payload, source = self._socket.recvfrom(MAX_UDP_DATAGRAM)
        except socket.timeout:
            return None
        # Timestamp immediately after recvfrom returns, before parsing or I/O.
        monotonic_ns = time.monotonic_ns()
        received_at_ns = time.time_ns()
        return ReceivedDatagram(
            received_at_ns=received_at_ns,
            monotonic_ns=monotonic_ns,
            source_ip=source[0],
            source_port=source[1],
            payload=payload,
        )

    def packets(self) -> Iterator[ReceivedDatagram]:
        while self._socket is not None:
            packet = self.receive()
            if packet is not None:
                yield packet

    def close(self) -> None:
        if self._socket is not None:
            self._socket.close()
            self._socket = None

    def __enter__(self) -> "UdpReceiver":
        self.open()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

