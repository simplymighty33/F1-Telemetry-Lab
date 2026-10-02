"""Durable, packet-native player channels, shared by live ingestion and Replay.

This cache is disposable/rebuildable; Raw is authoritative. Each transaction
commits complete verified Raw batches and its resume cursor together. No joined
frame is required to keep a valid player record. Low-rate/context bodies are
content-deduplicated; high-rate numerical records use typed columns, not JSON.
"""
from __future__ import annotations

from collections import OrderedDict
from contextlib import closing
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import struct
import zlib
from typing import Any, Iterator

from decoder.full_parser import Schema, compiled_schema
from decoder.header import HEADER_SIZE, PacketHeader, decode_header
from decoder.stream import (
    DECODER_VERSION, FOUNDATION_SCHEMAS as PLAYER_SCHEMAS, decode_context,
    decode_foundation_record, validate_packet,
)
from storage.raw_archive import ArchiveCursor, ArchivedPacket, FILE_HEADER
from storage.lease import FileLease

FOUNDATION_VERSION = 2
CHANNEL_NAMES = {
    0: "motion", 2: "lap", 5: "setup", 6: "telemetry", 7: "status",
    10: "damage", 13: "motion_ex", 16: "telemetry_2",
}


class FoundationError(ValueError):
    pass


def foundation_path(source: Path) -> Path:
    # Keep the v0.8/F1-23 cache intact. It is derived and can coexist with the
    # versioned multi-game cache while Raw remains the authority.
    return source.parent / "foundation" / f"{source.stem}.foundation-v2.db"


def _fields(schema: Schema) -> tuple[tuple[str, str, int], ...]:
    result = []
    for name, fmt in schema:
        parser = struct.Struct("<" + fmt)
        count = len(parser.unpack(bytes(parser.size)))
        kind = "REAL" if fmt[-1] in "fd" else "INTEGER"
        for index in range(count):
            result.append((name if count == 1 else f"{name}_{index}", kind, index))
    return tuple(result)


def _flatten(body: dict, schema: Schema) -> tuple:
    return tuple(value for name, _ in schema for value in (body[name] if isinstance(body[name], list) else [body[name]]))


def _unflatten(values: tuple, schema: Schema, *, drop_missing: bool = False) -> dict:
    result = {}
    for name, start, end in compiled_schema(schema)[1]:
        value = values[start] if end - start == 1 else list(values[start:end])
        if drop_missing and (value is None or (
            isinstance(value, list) and all(item is None for item in value)
        )):
            continue
        result[name] = value
    return result


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _finite(body: dict) -> None:
    for value in body.values():
        for scalar in value if isinstance(value, list) else [value]:
            if isinstance(scalar, float) and not math.isfinite(scalar):
                raise FoundationError("non-finite numerical game value")


def read_progress(database: Path) -> dict | None:
    database = database.resolve()
    if not database.is_file():
        return None
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
        version = connection.execute("SELECT value FROM metadata WHERE key='version'").fetchone()
        if not version or json.loads(version[0]) != [FOUNDATION_VERSION, DECODER_VERSION]:
            raise FoundationError("incompatible foundation cache; existing file was not modified")
        row = connection.execute("SELECT value FROM metadata WHERE key='progress'").fetchone()
        return json.loads(row[0]) if row else None


class FoundationRepository:
    """Read-only channel/range access without touching or re-decoding Raw."""
    def __init__(self, database: Path) -> None:
        self.database = database.resolve()
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT value FROM metadata WHERE key='version'").fetchone()
            if not row or json.loads(row[0]) != [FOUNDATION_VERSION, DECODER_VERSION]:
                raise FoundationError("incompatible foundation cache")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True)

    def segments(self, session_uid: str | None = None) -> tuple[dict, ...]:
        with closing(self._connect()) as connection:
            connection.row_factory = sqlite3.Row
            query = "SELECT * FROM segments WHERE superseded=0"
            args = ()
            if session_uid is not None:
                query += " AND session_uid=?"
                args = (str(session_uid),)
            return tuple(dict(row) for row in connection.execute(query + " ORDER BY start_offset DESC", args))

    def channel(self, packet_id: int, session_uid: str, player_car_index: int,
                start_offset: int, end_offset: int, *, include_superseded: bool = False) -> Iterator[dict]:
        """Original samples only: no interpolation, forward-fill, or guessed lap.

        Source metadata accompanies every value. Range callers can supply a
        segment's bounds; unsupported channels and reversed ranges are errors.
        """
        if packet_id not in PLAYER_SCHEMAS or start_offset > end_offset:
            raise FoundationError("invalid channel or source range")
        fields = [name for name, _, _ in _fields(PLAYER_SCHEMAS[packet_id])]
        query = (
            "SELECT p.raw_offset,p.received_at_ns,p.monotonic_ns,p.header,p.superseded,"
            + ",".join("c." + name for name in fields)
            + f" FROM {CHANNEL_NAMES[packet_id]} c JOIN packets p USING(raw_offset) "
            "WHERE p.raw_offset BETWEEN ? AND ? AND p.session_uid=? AND p.player_car_index=?"
            + ("" if include_superseded else " AND p.superseded=0") + " ORDER BY p.raw_offset"
        )
        with closing(self._connect()) as connection:
            connection.execute("BEGIN")
            for offset, received, monotonic, raw_header, superseded, *values in connection.execute(
                query, (start_offset, end_offset, str(session_uid), player_car_index),
            ):
                yield {"raw_offset": offset, "received_at_ns": received, "monotonic_ns": monotonic,
                       "header": decode_header(raw_header), "superseded": bool(superseded),
                       "values": _unflatten(tuple(values), PLAYER_SCHEMAS[packet_id])}


class FoundationStore:
    def __init__(self, source: Path, database: Path | None = None) -> None:
        self.source = source.resolve()
        self.database = (database or foundation_path(self.source)).resolve()
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._lease = FileLease(self.database.with_suffix(".lock"))
        try:
            self.connection = sqlite3.connect(self.database, timeout=0.2)
        except BaseException:
            self._lease.close()
            raise
        self._body_cache: OrderedDict[str, tuple[int, dict]] = OrderedDict()
        self._insert_sql = {}
        try:
            tables = {row[0] for row in self.connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not tables:
                self._create()
            row = self.connection.execute("SELECT value FROM metadata WHERE key='version'").fetchone()
            if row is None or json.loads(row[0]) != [FOUNDATION_VERSION, DECODER_VERSION]:
                raise FoundationError("incompatible foundation cache; preserve it and choose a new output path")
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA synchronous=FULL")
            self.connection.execute("PRAGMA cache_size=-4096")
            for packet_id, schema in PLAYER_SCHEMAS.items():
                columns = ["raw_offset"] + [name for name, _, _ in _fields(schema)]
                self._insert_sql[packet_id] = (
                    f"INSERT INTO {CHANNEL_NAMES[packet_id]} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})"
                )
            self.progress = read_progress(self.database) or {"packets": 0, "errors": 0, "cursor": None}
            self._segments = self.progress.get("segment_state", {})
        except BaseException:
            self.connection.close()
            self._lease.close()
            raise

    def _create(self) -> None:
        self.connection.executescript("""
            CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE bodies(id INTEGER PRIMARY KEY, digest TEXT UNIQUE NOT NULL, body_zlib BLOB NOT NULL);
            CREATE TABLE packets(
                raw_offset INTEGER PRIMARY KEY, received_at_ns INTEGER NOT NULL,
                monotonic_ns INTEGER NOT NULL, header BLOB NOT NULL,
                session_uid TEXT, player_car_index INTEGER, packet_id INTEGER,
                frame_identifier INTEGER, overall_frame_identifier INTEGER,
                session_time REAL, superseded INTEGER NOT NULL DEFAULT 0,
                body_id INTEGER, error TEXT
            );
            CREATE INDEX packet_timeline ON packets(session_uid, frame_identifier) WHERE superseded=0;
            CREATE TABLE segments(
                id INTEGER PRIMARY KEY, session_uid TEXT NOT NULL,
                player_car_index INTEGER NOT NULL, start_offset INTEGER NOT NULL,
                end_offset INTEGER NOT NULL, start_time REAL, end_time REAL,
                first_lap INTEGER, last_lap INTEGER, start_complete INTEGER NOT NULL,
                end_reason TEXT NOT NULL, superseded INTEGER NOT NULL DEFAULT 0
            );
        """)
        for packet_id, schema in PLAYER_SCHEMAS.items():
            columns = ",".join(f"{name} {kind}" for name, kind, _ in _fields(schema))
            self.connection.execute(f"CREATE TABLE {CHANNEL_NAMES[packet_id]} (raw_offset INTEGER PRIMARY KEY,{columns})")
        self.connection.execute("INSERT INTO metadata VALUES ('version',?)", (_json([FOUNDATION_VERSION, DECODER_VERSION]),))
        self.connection.commit()

    def _context(self, payload: bytes, header: PacketHeader) -> tuple[int | None, dict | None]:
        if header.packet_id in {11, 12} and payload[HEADER_SIZE] != header.player_car_index:
            return None, None
        digest = hashlib.sha256(
            struct.pack("<HB", header.packet_format, header.packet_id) + payload[HEADER_SIZE:]
        ).hexdigest()
        cached = self._body_cache.get(digest)
        if cached:
            self._body_cache.move_to_end(digest)
            return cached
        row = self.connection.execute("SELECT id,body_zlib FROM bodies WHERE digest=?", (digest,)).fetchone()
        if row:
            result = (row[0], json.loads(zlib.decompress(row[1])))
        else:
            body = decode_context(payload, header)
            if body is None:
                return None, None
            cursor = self.connection.execute("INSERT INTO bodies(digest,body_zlib) VALUES (?,?)", (digest, zlib.compress(_json(body).encode("utf-8"), 1)))
            result = (cursor.lastrowid, body)
        self._body_cache[digest] = result
        if len(self._body_cache) > 128:
            self._body_cache.popitem(last=False)
        return result

    def _lap_segment(self, offset: int, header: PacketHeader, lap: dict) -> None:
        key = f"{header.session_uid}:{header.player_car_index}"
        state = self._segments.setdefault(key, {"garage": False, "active": None})
        driver = lap["driver_status"]
        if driver == 0:
            if state["active"] is not None:
                self.connection.execute(
                    "UPDATE segments SET end_offset=?,end_time=?,end_reason='garage' WHERE id=?",
                    (offset, header.session_time, state["active"]),
                )
            state.update(garage=True, active=None)
        elif driver in {1, 2, 3, 4}:
            if state["active"] is None:
                self.connection.execute(
                    "INSERT OR REPLACE INTO segments VALUES (?,?,?,?,?,?,?,?,?,?,?,0)",
                    (offset, str(header.session_uid), header.player_car_index, offset, offset,
                     header.session_time, header.session_time, lap["current_lap_num"],
                     lap["current_lap_num"], int(state["garage"]), "open"),
                )
                state.update(garage=False, active=offset)
            else:
                self.connection.execute(
                    "UPDATE segments SET end_offset=?,end_time=?,last_lap=? WHERE id=?",
                    (offset, header.session_time, lap["current_lap_num"], state["active"]),
                )

    def _flashback(self, header: PacketHeader, target: int, event_offset: int) -> None:
        uid = str(header.session_uid)
        self.connection.execute(
            "UPDATE packets SET superseded=1 WHERE session_uid=? AND superseded=0 AND frame_identifier>? AND raw_offset<?",
            (uid, target, event_offset),
        )
        self.connection.execute("UPDATE segments SET superseded=1 WHERE session_uid=?", (uid,))
        self._segments = {key: state for key, state in self._segments.items() if not key.startswith(uid + ":")}
        for offset, raw_header, driver, lap_num in self.connection.execute(
            "SELECT p.raw_offset,p.header,l.driver_status,l.current_lap_num FROM packets p JOIN lap l USING(raw_offset) "
            "WHERE p.session_uid=? AND p.superseded=0 ORDER BY p.raw_offset", (uid,),
        ):
            self._lap_segment(offset, decode_header(raw_header), {"driver_status": driver, "current_lap_num": lap_num})

    def ingest(self, packets: list[ArchivedPacket], cursor: dict, *, source_mtime_ns: int) -> dict:
        """An unexpected storage/reducer failure rolls back the whole batch."""
        previous = json.loads(_json(self._segments))
        errors = 0
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            for packet in packets:
                header = None
                body_id = None
                body = None
                error = None
                try:
                    header = decode_header(packet.payload)
                    validate_packet(packet.payload, header)
                    if header.packet_id in PLAYER_SCHEMAS:
                        body = decode_foundation_record(packet.payload, header)
                        if body is not None:
                            _finite(body)
                    else:
                        body_id, body = self._context(packet.payload, header)
                except (ValueError, struct.error) as exc:
                    error = f"{type(exc).__name__}: {exc}"
                    errors += 1
                    body = None
                self.connection.execute(
                    "INSERT INTO packets(raw_offset,received_at_ns,monotonic_ns,header,session_uid,player_car_index,packet_id,"
                    "frame_identifier,overall_frame_identifier,session_time,body_id,error) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (packet.offset, packet.received_at_ns, packet.monotonic_ns, packet.payload[:HEADER_SIZE],
                     str(header.session_uid) if header else None, header.player_car_index if header else None,
                     header.packet_id if header else None, header.frame_identifier if header else None,
                     header.overall_frame_identifier if header else None,
                     header.session_time if header and math.isfinite(header.session_time) else None, body_id, error),
                )
                if header and body is not None:
                    if header.packet_id in PLAYER_SCHEMAS:
                        self.connection.execute(self._insert_sql[header.packet_id], (packet.offset,) + _flatten(body, PLAYER_SCHEMAS[header.packet_id]))
                    if header.packet_id == 2:
                        self._lap_segment(packet.offset, header, body)
                    elif header.packet_id == 3 and body["event_code"] == "FLBK":
                        self._flashback(header, body["event_details"]["flashback_frame_identifier"], packet.offset)
                    elif header.packet_id == 3 and body["event_code"] == "SEND":
                        self.connection.execute(
                            "UPDATE segments SET end_offset=?,end_time=?,end_reason='session_end' "
                            "WHERE session_uid=? AND superseded=0 AND end_reason='open'",
                            (packet.offset, header.session_time, str(header.session_uid)),
                        )
                        for key, state in self._segments.items():
                            if key.startswith(str(header.session_uid) + ":"):
                                state["active"] = None
            progress = {
                "packets": self.progress["packets"] + len(packets),
                "errors": self.progress["errors"] + errors, "cursor": cursor,
                "segment_state": self._segments, "source_mtime_ns": source_mtime_ns,
            }
            self.connection.execute("INSERT OR REPLACE INTO metadata VALUES ('progress',?)", (_json(progress),))
            self.connection.commit()
            self.progress = progress
            return progress
        except BaseException:
            self.connection.rollback()
            self._segments = previous
            self._body_cache.clear()
            raise

    def close(self) -> None:
        try:
            self.connection.close()
        finally:
            self._lease.close()

    def __enter__(self) -> FoundationStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


@dataclass(frozen=True, slots=True)
class FoundationPacket:
    offset: int
    received_at_ns: int
    header: PacketHeader | None
    body: dict | None
    error: str | None


def iter_foundation(connection: sqlite3.Connection, after_offset: int = -1,
                    until_offset: int = 9223372036854775807) -> Iterator[FoundationPacket]:
    """Sorted merge: bounded memory and no SQL query for every game packet."""
    channels = {packet_id: iter(connection.execute(
        f"SELECT * FROM {CHANNEL_NAMES[packet_id]} WHERE raw_offset>? AND raw_offset<=? ORDER BY raw_offset",
        (after_offset, until_offset))) for packet_id in PLAYER_SCHEMAS}
    pending = {packet_id: next(rows, None) for packet_id, rows in channels.items()}
    bodies: OrderedDict[int, dict] = OrderedDict()
    for offset, received, raw_header, packet_id, body_id, error in connection.execute(
        "SELECT raw_offset,received_at_ns,header,packet_id,body_id,error FROM packets "
        "WHERE raw_offset>? AND raw_offset<=? ORDER BY raw_offset", (after_offset, until_offset)
    ):
        body = None
        if packet_id in channels:
            values = pending[packet_id]
            if values is not None and values[0] == offset:
                body = _unflatten(values[1:], PLAYER_SCHEMAS[packet_id], drop_missing=True)
                pending[packet_id] = next(channels[packet_id], None)
        elif body_id is not None:
            if body_id not in bodies:
                encoded = connection.execute("SELECT body_zlib FROM bodies WHERE id=?", (body_id,)).fetchone()[0]
                bodies[body_id] = json.loads(zlib.decompress(encoded))
                if len(bodies) > 128:
                    bodies.popitem(last=False)
            bodies.move_to_end(body_id)
            body = bodies[body_id]
        yield FoundationPacket(offset, received, decode_header(raw_header) if len(raw_header) == HEADER_SIZE else None, body, error)


def ensure_foundation(source: Path, database: Path | None = None, *, recover_tail: bool = False, verify: bool = False, control=None) -> dict:
    """Convert old Raw once; unchanged closed sources use their compatible cache.

    Fast reuse checks path-associated cache, size and high-resolution mtime.
    verify=True additionally rehashes its complete consumed prefix. Appended
    sources always verify the earlier prefix before resuming.
    """
    source = source.resolve()
    if control:
        control.report('基础数据校验')
    target = (database or foundation_path(source)).resolve()
    progress = read_progress(target)
    stat = source.stat()
    if progress and not verify and progress["cursor"]["physical"] == stat.st_size and progress["source_mtime_ns"] == stat.st_mtime_ns:
        with closing(sqlite3.connect(target.as_uri() + "?mode=ro", uri=True)) as connection:
            version = json.loads(connection.execute("SELECT value FROM metadata WHERE key='version'").fetchone()[0])
            if version != [FOUNDATION_VERSION, DECODER_VERSION]:
                raise FoundationError("incompatible foundation version")
        return {**progress, "database": str(target), "reused": True, "tail_error": None}
    with FoundationStore(source, target) as store, ArchiveCursor(source, store.progress["cursor"]) as cursor:
        while batch := cursor.next_batch(stat.st_size, recover_tail=recover_tail):
            if control:
                control.report('整理基础数据', cursor.tell(), stat.st_size)
            store.ingest(batch, cursor.checkpoint(), source_mtime_ns=stat.st_mtime_ns)
        if cursor.tail_error is None and cursor.tell() != stat.st_size:
            raise FoundationError("Raw source was not completely consumed")
        if source.stat().st_mtime_ns != stat.st_mtime_ns or source.stat().st_size != stat.st_size:
            raise FoundationError("Raw changed during offline conversion; retry after capture stops")
        if store.progress["cursor"] is None or store.progress.get("source_mtime_ns") != stat.st_mtime_ns:
            store.ingest([], cursor.checkpoint(), source_mtime_ns=stat.st_mtime_ns)
        return {**store.progress, "database": str(target), "reused": False, "tail_error": cursor.tail_error}
