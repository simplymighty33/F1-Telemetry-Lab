"""SQLite index for captured packets."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Any

from decoder.header import PacketHeader
from storage.raw_writer import RawReference


SCHEMA = """
CREATE TABLE sessions (
    session_id TEXT PRIMARY KEY,
    start_time TEXT NOT NULL,
    end_time TEXT,
    track TEXT,
    session_type TEXT,
    packet_count INTEGER NOT NULL DEFAULT 0,
    raw_archive TEXT NOT NULL,
    status TEXT NOT NULL
);

CREATE TABLE packets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    timestamp TEXT NOT NULL,
    received_at_ns INTEGER NOT NULL,
    source_ip TEXT NOT NULL,
    source_port INTEGER NOT NULL,
    packet_id INTEGER,
    packet_format INTEGER,
    game_major_version INTEGER,
    packet_version INTEGER,
    session_uid TEXT,
    frame_identifier INTEGER,
    overall_frame_identifier INTEGER,
    player_car_index INTEGER,
    secondary_player_car_index INTEGER,
    size INTEGER NOT NULL,
    raw_reference TEXT NOT NULL,
    raw_offset INTEGER NOT NULL,
    raw_length INTEGER NOT NULL,
    crc32 INTEGER NOT NULL,
    parse_error TEXT,
    archive_version INTEGER NOT NULL,
    raw_block_offset INTEGER,
    raw_block_record_offset INTEGER
);

CREATE INDEX idx_packets_session_packet_id
    ON packets(session_id, packet_id);
CREATE INDEX idx_packets_session_frame
    ON packets(session_id, frame_identifier);

-- Reserved for Phase 2 decoders; intentionally empty in Phase 1.
CREATE TABLE telemetry_raw (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    time REAL,
    car_index INTEGER,
    speed INTEGER,
    throttle REAL,
    brake REAL,
    steer REAL,
    gear INTEGER,
    rpm INTEGER
);
"""


class TelemetryDatabase:
    def __init__(self, path: Path, batch_size: int = 256) -> None:
        self.path = path
        self.batch_size = max(1, batch_size)
        # PacketCapture is created by the runtime thread and consumed by the
        # pipeline's single writer thread.  The pipeline is fully joined before
        # close(), so access is serialized even though two thread identities are
        # involved during the connection lifetime.
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.executescript(SCHEMA)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self._pending: list[tuple[Any, ...]] = []

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def begin_session(
        self,
        session_id: str,
        start_time: str,
        raw_archive: str,
        track: str | None,
        session_type: str | None,
    ) -> None:
        self.connection.execute(
            """INSERT INTO sessions
               (session_id, start_time, track, session_type, raw_archive, status)
               VALUES (?, ?, ?, ?, ?, 'recording')""",
            (session_id, start_time, track, session_type, raw_archive),
        )
        self.connection.commit()

    def add_packet(
        self,
        session_id: str,
        received_at_ns: int,
        source_ip: str,
        source_port: int,
        size: int,
        raw_reference: RawReference,
        header: PacketHeader | None,
        parse_error: str | None,
    ) -> None:
        timestamp = datetime.fromtimestamp(
            received_at_ns / 1_000_000_000, tz=timezone.utc
        ).isoformat(timespec="microseconds").replace("+00:00", "Z")
        values = (
            session_id,
            timestamp,
            received_at_ns,
            source_ip,
            source_port,
            header.packet_id if header else None,
            header.packet_format if header else None,
            header.game_major_version if header else None,
            header.packet_version if header else None,
            str(header.session_uid) if header else None,
            header.frame_identifier if header else None,
            header.overall_frame_identifier if header else None,
            header.player_car_index if header else None,
            header.secondary_player_car_index if header else None,
            size,
            (
                f"raw_packets.bin:{raw_reference.offset}:{raw_reference.record_length}"
                if raw_reference.archive_version == 1 else
                f"raw_packets.bin:v2:{raw_reference.block_offset}:{raw_reference.block_record_offset}"
            ),
            raw_reference.offset,
            raw_reference.record_length,
            raw_reference.crc32,
            parse_error,
            raw_reference.archive_version,
            raw_reference.block_offset,
            raw_reference.block_record_offset,
        )
        self._pending.append(values)
        if len(self._pending) >= self.batch_size:
            self.flush()

    def flush(self) -> None:
        if not self._pending:
            return
        self.connection.executemany(
            """INSERT INTO packets (
                session_id, timestamp, received_at_ns, source_ip, source_port,
                packet_id, packet_format, game_major_version, packet_version,
                session_uid, frame_identifier, overall_frame_identifier,
                player_car_index, secondary_player_car_index, size, raw_reference,
                raw_offset, raw_length, crc32, parse_error, archive_version,
                raw_block_offset, raw_block_record_offset
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            self._pending,
        )
        self.connection.commit()
        self._pending.clear()

    def end_session(
        self, session_id: str, end_time: str, packet_count: int, status: str = "complete"
    ) -> None:
        self.flush()
        self.connection.execute(
            """UPDATE sessions
               SET end_time = ?, packet_count = ?, status = ?
               WHERE session_id = ?""",
            (end_time, packet_count, status, session_id),
        )
        self.connection.commit()

    def close(self) -> None:
        self.flush()
        self.connection.close()

