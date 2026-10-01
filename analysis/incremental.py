"""Single-writer, transactional analysis of committed foundation increments.

Raw capture never calls this reducer. A failed batch changes neither its durable
checkpoint nor published lap results. Historical full Replay remains the oracle.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import asdict
import json
from pathlib import Path
import math
import sqlite3
import zlib

from analysis.build import FRAME_PACKET_IDS, _FrameWriter, _analysis_input, _archive_path, _lap_rows
from analysis.events import analyze_driving_events
from analysis.resample import resample_laps
from analysis.schema import create_schema
from decoder.header import PacketHeader
from decoder.tyres import PlayerTyreTracker
from storage.foundation import FOUNDATION_VERSION, foundation_path, iter_foundation
from decoder.stream import DECODER_VERSION
from storage.lease import FileLease, LeaseBusyError

INCREMENTAL_VERSION = 1
DERIVED = ("lap_analysis", "resampled_lap_samples", "braking_events", "throttle_events", "gear_shift_events", "lap_metrics")


def analysis_path(source: Path) -> Path:
    return _archive_path(source)[1] / "analysis_v0.10.0" / "telemetry_analysis.db"


def read_summary(database: Path) -> dict:
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as con:
        row = con.execute("SELECT value FROM metadata WHERE key='incremental_summary'").fetchone()
        if not row:
            raise ValueError("分析数据尚未提交，请稍后重试；Raw 采集不受影响。")
        return json.loads(row[0])


class IncrementalAnalysisStore:
    def __init__(self, source: Path, database: Path | None = None,
                 foundation_database: Path | None = None, distance_step_m: float = 5.0) -> None:
        self.source = _archive_path(source)[0]
        self.database = (database or analysis_path(source)).resolve()
        self.foundation = (foundation_database or foundation_path(self.source)).resolve()
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.lease = FileLease(self.database.with_suffix(".lock"))
        self.connection = None
        self.distance_step_m = distance_step_m
        try:
            if not math.isfinite(distance_step_m) or distance_step_m <= 0:
                raise ValueError("distance step must be a positive finite number")
            self.connection = sqlite3.connect(self.database, timeout=0.2)
            con = self.connection
            if not con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchone():
                create_schema(con)
                con.executescript("""
                    CREATE TABLE incremental_checkpoint(id INTEGER PRIMARY KEY CHECK(id=1), state BLOB NOT NULL);
                    CREATE TABLE telemetry_origins(session_uid TEXT, overall_frame_identifier INTEGER, raw_offset INTEGER,
                        PRIMARY KEY(session_uid,overall_frame_identifier));
                    CREATE TABLE driving_segments(id INTEGER PRIMARY KEY,session_uid TEXT,player_car_index INTEGER,
                        start_offset INTEGER,end_offset INTEGER,start_time REAL,end_time REAL,first_lap INTEGER,
                        last_lap INTEGER,start_complete INTEGER,end_reason TEXT,superseded INTEGER);
                    CREATE TABLE lap_segments(session_uid TEXT,lap_number INTEGER,segment_id INTEGER,
                        PRIMARY KEY(session_uid,lap_number));
                """)
                con.execute("INSERT INTO metadata VALUES ('incremental_version',?)", (json.dumps(INCREMENTAL_VERSION),))
                con.commit()
            version = con.execute("SELECT value FROM metadata WHERE key='incremental_version'").fetchone()
            if not version or json.loads(version[0]) != INCREMENTAL_VERSION:
                raise ValueError("增量分析版本不兼容；现有文件已保留，请选择新目录。")
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA synchronous=FULL")
            con.execute("PRAGMA cache_size=-4096")
            self._load()
        except BaseException:
            self.close()
            raise

    def _load(self) -> None:
        con = self.connection
        row = con.execute("SELECT state FROM incremental_checkpoint WHERE id=1").fetchone()
        self.state = json.loads(zlib.decompress(row[0])) if row else {
            "source": str(self.source), "foundation": str(self.foundation), "step": self.distance_step_m,
            "offset": -1, "packets": 0, "errors": 0, "flashbacks": 0, "histories": {},
            "info": {}, "bounds": {}, "trackers": {}, "dirty": [], "frames": [],
            "status": {}, "damage": {}, "last_packet": None,
        }
        s = self.state
        if (s["source"], s["foundation"], s["step"]) != (str(self.source), str(self.foundation), self.distance_step_m):
            raise ValueError("分析来源或参数改变，请保留现有数据库并选择新输出目录。")
        self.dirty = {tuple(key) for key in s["dirty"]}
        self.trackers = {uid: (value[0], PlayerTyreTracker.restore(value[1])) for uid, value in s["trackers"].items()}
        self.writer = _FrameWriter(con, self._sample)
        self.writer.last_status = s["status"]
        self.writer.last_damage = s["damage"]
        for encoded in s["frames"]:
            frame = {**encoded, "header": PacketHeader(**encoded["header"]),
                     "parts": {int(k): v for k, v in encoded["parts"].items()}}
            self.writer.pending[(str(frame["header"].session_uid), frame["header"].overall_frame_identifier)] = frame

    def _sample(self, row: dict, offset: int | None) -> None:
        key = (row["session_uid"], row["lap_number"])
        self.dirty.add(key)
        self.connection.execute("INSERT OR REPLACE INTO telemetry_origins VALUES (?,?,?)",
                                (key[0], row["overall_frame_identifier"], offset))

    def _packet(self, packet) -> None:
        s, con = self.state, self.connection
        s["offset"] = packet.offset
        s["packets"] += 1
        h, body = packet.header, packet.body
        if packet.error or h is None:
            s["errors"] += 1
            return
        uid = str(h.session_uid)
        bounds = s["bounds"].setdefault(uid, [packet.received_at_ns, packet.received_at_ns])
        bounds[0] = min(bounds[0], packet.received_at_ns)
        bounds[1] = max(bounds[1], packet.received_at_ns)
        if body is None:
            return
        if h.packet_id in PlayerTyreTracker.PACKET_IDS:
            if uid not in self.trackers or self.trackers[uid][0] != h.player_car_index:
                self.trackers[uid] = (h.player_car_index, PlayerTyreTracker())
            tracker = self.trackers[uid][1]
            tracker.observe(h, body)
            if tracker.invalidated_laps and uid in s["histories"]:
                history = dict(s["histories"][uid][1])
                history["num_laps"] = min(history["num_laps"], min(tracker.invalidated_laps) - 1)
                s["histories"][uid][1] = history
        if h.packet_id in FRAME_PACKET_IDS:
            self.writer.add(h, packet.received_at_ns, body, packet.offset)
        elif h.packet_id == 1:
            previous = s["info"].get(uid)
            s["info"][uid] = body
            if previous is None or previous.get("track_length") != body.get("track_length"):
                self.dirty.update((uid, number) for number, in con.execute("SELECT lap_number FROM laps WHERE session_uid=?", (uid,)))
        elif h.packet_id == 3:
            if body["event_code"] == "FLBK":
                self.writer.flush_all()
                target = body["event_details"]["flashback_frame_identifier"]
                affected = {(uid, number) for number, in con.execute(
                    "SELECT DISTINCT lap_number FROM telemetry_samples WHERE session_uid=? AND superseded=0 AND frame_identifier>?", (uid, target))}
                self.dirty.update(affected)
                for key in affected:
                    for table in DERIVED:
                        con.execute(f"DELETE FROM {table} WHERE session_uid=? AND lap_number=?", key)
                con.execute("UPDATE telemetry_samples SET superseded=1 WHERE session_uid=? AND superseded=0 AND frame_identifier>?", (uid, target))
                s["flashbacks"] += 1
            con.execute("INSERT INTO events(session_uid,received_at_ns,session_time,frame_identifier,overall_frame_identifier,event_code,event_name,event_details_json) VALUES (?,?,?,?,?,?,?,?)",
                        (uid, packet.received_at_ns, h.session_time, h.frame_identifier, h.overall_frame_identifier,
                         body["event_code"], body["event_name"], json.dumps(body["event_details"], ensure_ascii=False)))
        elif h.packet_id == 11 and body["car_idx"] == h.player_car_index:
            s["histories"][uid] = [packet.received_at_ns, body]

    def _publish(self, foundation: sqlite3.Connection) -> int:
        con, s = self.connection, self.state
        for uid, bounds in s["bounds"].items():
            info = s["info"].get(uid, {})
            con.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(session_uid) DO UPDATE SET "
                        "game_mode=excluded.game_mode,game_mode_id=excluded.game_mode_id,session_type=excluded.session_type,session_type_id=excluded.session_type_id,track=excluded.track,track_id=excluded.track_id,track_length_m=excluded.track_length_m,total_laps=excluded.total_laps,last_received_at_ns=excluded.last_received_at_ns",
                        (uid, info.get("game_mode_name"), info.get("game_mode"), info.get("session_type_name"), info.get("session_type"),
                         info.get("track_name"), info.get("track_id"), info.get("track_length"), info.get("total_laps"), *bounds))
        for uid, (received, history) in s["histories"].items():
            rows = _lap_rows(uid, received, history)
            valid = {row[1] for row in rows}
            previous = {row[1]: tuple(row) for row in con.execute("SELECT * FROM laps WHERE session_uid=?", (uid,))}
            for number in previous.keys() - valid:
                for table in (*DERIVED, "lap_tyres", "lap_segments"):
                    con.execute(f"DELETE FROM {table} WHERE session_uid=? AND lap_number=?", (uid, number))
                con.execute("DELETE FROM laps WHERE session_uid=? AND lap_number=?", (uid, number))
                self.dirty.discard((uid, number))
            for row in rows:
                # Receipt timestamp changes at 2 Hz; lap contents, not that stamp, invalidate analysis.
                if row[1] not in previous or previous[row[1]][:-1] != row[:-1]:
                    self.dirty.add((uid, row[1]))
                con.execute("INSERT INTO laps VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(session_uid,lap_number) DO UPDATE SET "
                            "lap_time_ms=excluded.lap_time_ms,sector1_ms=excluded.sector1_ms,sector2_ms=excluded.sector2_ms,sector3_ms=excluded.sector3_ms,lap_valid=excluded.lap_valid,sector1_valid=excluded.sector1_valid,sector2_valid=excluded.sector2_valid,sector3_valid=excluded.sector3_valid,snapshot_received_at_ns=excluded.snapshot_received_at_ns", row)
            if uid in self.trackers:
                for number, tyre in self.trackers[uid][1].laps(history).items():
                    if number not in valid:
                        continue
                    values = {"session_uid": uid, "lap_number": number, **asdict(tyre)}
                    con.execute(f"INSERT OR REPLACE INTO lap_tyres ({','.join(values)}) VALUES ({','.join('?' for _ in values)})", tuple(values.values()))
        pending_laps = {(str(f["header"].session_uid), f["parts"][2]["current_lap_num"])
                        for f in self.writer.pending.values() if 2 in f["parts"]}
        completed = {(uid, num) for uid, num in con.execute("SELECT session_uid,lap_number FROM laps")}
        segment_keys = self.dirty & completed
        selected = (self.dirty & completed) - pending_laps
        for uid, number in selected:
            for table in DERIVED:
                con.execute(f"DELETE FROM {table} WHERE session_uid=? AND lap_number=?", (uid, number))
        resample_laps(con, distance_step_m=self.distance_step_m, lap_keys=selected)
        analyze_driving_events(con, only_laps=selected)
        self.dirty.difference_update(selected)
        # Segment IDs are Raw offsets, stable across refresh; use active timeline only.
        con.execute("DELETE FROM driving_segments")
        segments = foundation.execute("SELECT * FROM segments WHERE superseded=0 AND start_offset<=?", (s["offset"],)).fetchall()
        con.executemany("INSERT INTO driving_segments VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", segments)
        con.execute("DELETE FROM lap_segments WHERE segment_id NOT IN (SELECT id FROM driving_segments)")
        for key in segment_keys:
            con.execute("DELETE FROM lap_segments WHERE session_uid=? AND lap_number=?", key)
            con.execute("""INSERT INTO lap_segments
                SELECT t.session_uid,t.lap_number,MAX(g.id) FROM telemetry_samples t
                JOIN telemetry_origins o ON o.session_uid=t.session_uid AND o.overall_frame_identifier=t.overall_frame_identifier
                JOIN driving_segments g ON g.session_uid=t.session_uid AND g.player_car_index=t.player_car_index
                    AND o.raw_offset BETWEEN g.start_offset AND g.end_offset
                WHERE t.superseded=0 AND t.session_uid=? AND t.lap_number=?
                GROUP BY t.session_uid,t.lap_number""", key)
        return len(selected)

    def update(self, *, final: bool = False, batch_size: int = 50000) -> dict:
        """Consume at most one indexed range; state and results commit together."""
        con, s = self.connection, self.state
        if batch_size <= 0:
            raise ValueError("batch size must be positive")
        with closing(sqlite3.connect(self.foundation.as_uri() + "?mode=ro", uri=True)) as foundation:
            foundation.execute("BEGIN")
            version = foundation.execute("SELECT value FROM metadata WHERE key='version'").fetchone()
            if not version or json.loads(version[0]) != [FOUNDATION_VERSION, DECODER_VERSION]:
                raise ValueError("基础数据版本不兼容，已有分析已保留。")
            progress = json.loads(foundation.execute("SELECT value FROM metadata WHERE key='progress'").fetchone()[0])
            source_complete = bool(progress.get("cursor") and progress["cursor"]["physical"] == self.source.stat().st_size)
            last = foundation.execute("SELECT raw_offset,received_at_ns,hex(header) FROM packets ORDER BY raw_offset DESC LIMIT 1").fetchone()
            if last and last[0] < s["offset"]:
                raise ValueError("基础缓存被截短，请使用新分析目录；现有结果已保留。")
            if s["last_packet"]:
                boundary = foundation.execute("SELECT raw_offset,received_at_ns,hex(header) FROM packets WHERE raw_offset=?", (s["offset"],)).fetchone()
                if list(boundary or []) != s["last_packet"]:
                    raise ValueError("基础缓存来源发生变化，请使用新分析目录。")
            offsets = foundation.execute("SELECT raw_offset FROM packets WHERE raw_offset>? ORDER BY raw_offset LIMIT ?", (s["offset"], batch_size)).fetchall()
            if not offsets and (not final or (not self.writer.pending and s.get("finalized"))):
                try:
                    return {**read_summary(self.database), "processed_this_update": 0, "laps_updated": 0, "reused": True}
                except ValueError:
                    pass
            try:
                con.execute("BEGIN IMMEDIATE")
                before = s["packets"]
                if offsets:
                    for packet in iter_foundation(foundation, s["offset"], offsets[-1][0]):
                        self._packet(packet)
                caught_up = not last or s["offset"] == last[0]
                if final and caught_up and source_complete:
                    self.writer.flush_all()
                s["finalized"] = bool(final and caught_up and source_complete)
                changed = self._publish(foundation)
                s["frames"] = [{**f, "header": asdict(f["header"])} for f in self.writer.pending.values()]
                s["status"], s["damage"] = self.writer.last_status, self.writer.last_damage
                s["trackers"] = {uid: [player, tracker.checkpoint()] for uid, (player, tracker) in self.trackers.items()}
                s["dirty"] = sorted(self.dirty)
                boundary = foundation.execute("SELECT raw_offset,received_at_ns,hex(header) FROM packets WHERE raw_offset=?", (s["offset"],)).fetchone()
                s["last_packet"] = list(boundary) if boundary else None
                summary = {"analysis_database": str(self.database), "total_raw_packets": s["packets"],
                           "decode_error_count": s["errors"], "flashback_count": s["flashbacks"],
                           "processed_this_update": s["packets"] - before, "laps_updated": changed,
                           "laps_resampled": con.execute("SELECT COUNT(*) FROM lap_metrics").fetchone()[0],
                           "final_lap_count": con.execute("SELECT COUNT(*) FROM laps").fetchone()[0],
                           "resampled_points": con.execute("SELECT COUNT(*) FROM resampled_lap_samples").fetchone()[0],
                           "caught_up": caught_up, "live_snapshot": not final, "reused": False,
                           "source_complete": source_complete,
                           "status": "completed_with_errors" if s["errors"] else
                                     "complete" if final and caught_up and source_complete else
                                     "pending" if final else "processing"}
                con.execute("INSERT OR REPLACE INTO incremental_checkpoint VALUES (1,?)", (zlib.compress(json.dumps(s, ensure_ascii=False).encode(), 1),))
                con.execute("INSERT OR REPLACE INTO metadata VALUES ('incremental_summary',?)", (json.dumps(summary),))
                con.commit()
                return summary
            except BaseException:
                con.rollback()
                self._load()
                raise

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()
        self.lease.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def update_analysis(source: Path, output: Path | None = None, progress_every: int = 0,
                    recover_tail: bool = False, foundation_database: Path | None = None,
                    distance_step_m: float = 5.0) -> dict:
    archive, _ = _archive_path(source)
    database = output.resolve() / "telemetry_analysis.db" if output else analysis_path(source)
    # Resolve old Raw once, or use a committed live snapshot without a second foundation writer.
    with _analysis_input(archive, True, foundation_database, recover_tail) as (_, progress):
        recording = progress.get("recording", False)
        tail_error = progress.get("tail_error")
    try:
        with IncrementalAnalysisStore(archive, database, foundation_database, distance_step_m) as store:
            while True:
                result = store.update(final=not recording)
                if result["caught_up"]:
                    return {**result, "tail_error": tail_error,
                            "status": "partial_verified_prefix" if tail_error else result["status"]}
    except LeaseBusyError:
        return {**read_summary(database), "reused": True, "live_snapshot": True}
