"""Build a flashback-safe, distance-domain analysis database from Raw Replay."""

from __future__ import annotations

from collections import Counter, OrderedDict
from contextlib import closing, contextmanager, ExitStack
from dataclasses import asdict
import csv
from datetime import datetime, timezone
import gzip
import json
import os
from pathlib import Path
import sqlite3
import struct
from typing import Any
from uuid import uuid4

from analysis.events import EVENT_DETECTION_VERSION, EVENT_THRESHOLDS, analyze_driving_events
from analysis.player_decoder import decode_player_record
from analysis.resample import resample_laps
from analysis.schema import SCHEMA_VERSION, create_schema
from decoder.full_parser import decode_packet
from decoder.header import PacketHeader, decode_header
from decoder.protocol import game_label
from decoder.tyres import PlayerTyreTracker
from decoder.stream import FOUNDATION_SCHEMAS as PLAYER_SCHEMAS, decode_context, decode_player_record as shared_player_decode
from storage.foundation import FOUNDATION_VERSION, FoundationPacket, ensure_foundation, foundation_path, iter_foundation
from storage.lease import lease_active
from decoder.stream import DECODER_VERSION
from storage.raw_writer import iter_archive


FRAME_PACKET_IDS = {0, 2, 6, 7, 10, 13}
REQUIRED_FRAME_PARTS = {0, 2, 6}
ANALYSIS_ENGINE_VERSION = 9


def algorithm_contract(distance_step_m: float, include_invalid: bool = False) -> dict:
    """One compatibility vocabulary for full and incremental derived results."""
    return {"engine": ANALYSIS_ENGINE_VERSION, "decoder": DECODER_VERSION,
            "schema": SCHEMA_VERSION, "events": EVENT_DETECTION_VERSION,
            "thresholds": EVENT_THRESHOLDS, "distance_step_m": distance_step_m,
            "include_invalid": include_invalid}

SAMPLE_COLUMNS = (
    "session_uid", "overall_frame_identifier", "frame_identifier",
    "received_at_ns", "session_time", "player_car_index", "lap_number",
    "lap_distance_m", "total_distance_m", "current_lap_time_ms", "sector",
    "current_lap_invalid", "pit_status", "driver_status", "superseded",
    "world_x", "world_y", "world_z", "velocity_x", "velocity_y", "velocity_z",
    "g_lateral", "g_longitudinal", "g_vertical", "yaw", "pitch", "roll",
    "speed_kph", "throttle", "brake", "steer", "clutch", "gear",
    "engine_rpm", "drs", "brake_temp_rl", "brake_temp_rr", "brake_temp_fl",
    "brake_temp_fr", "tyre_surface_temp_rl", "tyre_surface_temp_rr",
    "tyre_surface_temp_fl", "tyre_surface_temp_fr", "tyre_inner_temp_rl",
    "tyre_inner_temp_rr", "tyre_inner_temp_fl", "tyre_inner_temp_fr",
    "tyre_pressure_rl", "tyre_pressure_rr", "tyre_pressure_fl",
    "tyre_pressure_fr", "surface_type_rl", "surface_type_rr",
    "surface_type_fl", "surface_type_fr", "fuel_in_tank",
    "fuel_remaining_laps", "actual_tyre_compound", "visual_tyre_compound",
    "tyres_age_laps", "ers_store_energy", "ers_deploy_mode",
    "ers_deployed_this_lap", "tyre_wear_rl", "tyre_wear_rr", "tyre_wear_fl",
    "tyre_wear_fr", "tyre_damage_rl", "tyre_damage_rr", "tyre_damage_fl",
    "tyre_damage_fr", "front_left_wing_damage", "front_right_wing_damage",
    "rear_wing_damage", "floor_damage", "wheel_speed_rl", "wheel_speed_rr",
    "wheel_speed_fl", "wheel_speed_fr", "wheel_slip_ratio_rl",
    "wheel_slip_ratio_rr", "wheel_slip_ratio_fl", "wheel_slip_ratio_fr",
    "wheel_slip_angle_rl", "wheel_slip_angle_rr", "wheel_slip_angle_fl",
    "wheel_slip_angle_fr", "front_wheels_angle",
)


def _archive_path(path: Path) -> tuple[Path, Path]:
    resolved = path.expanduser().resolve()
    if resolved.is_dir():
        return resolved / "raw_packets.bin", resolved
    return resolved, resolved.parent


def _atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    os.replace(temporary, path)


def _array(record: dict[str, Any] | None, name: str, index: int) -> Any:
    if not record:
        return None
    values = record.get(name)
    return values[index] if isinstance(values, list) and len(values) > index else None


def _value(record: dict[str, Any] | None, name: str) -> Any:
    return None if not record else record.get(name)


def _lap_rows(
    session_uid: str,
    received_at_ns: int,
    decoded: dict[str, Any],
) -> list[tuple[Any, ...]]:
    result = []
    for lap_number, lap in enumerate(decoded["lap_history"][: decoded["num_laps"]], 1):
        if lap["lap_time_ms"] <= 0:
            continue
        flags = lap["lap_valid_bit_flags"]
        result.append(
            (
                session_uid, lap_number, lap["lap_time_ms"],
                lap["sector1_time_minutes"] * 60_000 + lap["sector1_time_ms"],
                lap["sector2_time_minutes"] * 60_000 + lap["sector2_time_ms"],
                lap["sector3_time_minutes"] * 60_000 + lap["sector3_time_ms"],
                int(bool(flags & 0x01)), int(bool(flags & 0x02)),
                int(bool(flags & 0x04)), int(bool(flags & 0x08)),
                received_at_ns,
            )
        )
    return result


def _frame_row(
    frame: dict[str, Any],
    last_status: dict[str, Any] | None,
    last_damage: dict[str, Any] | None,
) -> dict[str, Any]:
    header: PacketHeader = frame["header"]
    parts: dict[int, dict[str, Any]] = frame["parts"]
    motion = parts[0]
    lap = parts[2]
    telemetry = parts[6]
    status = parts.get(7) or last_status
    damage = parts.get(10) or last_damage
    motion_ex = parts.get(13)
    row = {
        "session_uid": str(header.session_uid),
        "overall_frame_identifier": header.overall_frame_identifier,
        "frame_identifier": header.frame_identifier,
        "received_at_ns": frame["received_at_ns"],
        "session_time": header.session_time,
        "player_car_index": header.player_car_index,
        "lap_number": lap["current_lap_num"],
        "lap_distance_m": lap["lap_distance"],
        "total_distance_m": lap["total_distance"],
        "current_lap_time_ms": lap["current_lap_time_ms"],
        "sector": lap["sector"],
        "current_lap_invalid": lap["current_lap_invalid"],
        "pit_status": lap["pit_status"],
        "driver_status": lap["driver_status"],
        "superseded": 0,
        "world_x": motion["world_position_x"],
        "world_y": motion["world_position_y"],
        "world_z": motion["world_position_z"],
        "velocity_x": motion["world_velocity_x"],
        "velocity_y": motion["world_velocity_y"],
        "velocity_z": motion["world_velocity_z"],
        "g_lateral": motion["g_force_lateral"],
        "g_longitudinal": motion["g_force_longitudinal"],
        "g_vertical": motion["g_force_vertical"],
        "yaw": motion["yaw"], "pitch": motion["pitch"], "roll": motion["roll"],
        "speed_kph": telemetry["speed"], "throttle": telemetry["throttle"],
        "brake": telemetry["brake"], "steer": telemetry["steer"],
        "clutch": telemetry["clutch"], "gear": telemetry["gear"],
        "engine_rpm": telemetry["engine_rpm"], "drs": telemetry["drs"],
        "fuel_in_tank": _value(status, "fuel_in_tank"),
        "fuel_remaining_laps": _value(status, "fuel_remaining_laps"),
        "actual_tyre_compound": _value(status, "actual_tyre_compound"),
        "visual_tyre_compound": _value(status, "visual_tyre_compound"),
        "tyres_age_laps": _value(status, "tyres_age_laps"),
        "ers_store_energy": _value(status, "ers_store_energy"),
        "ers_deploy_mode": _value(status, "ers_deploy_mode"),
        "ers_deployed_this_lap": _value(status, "ers_deployed_this_lap"),
        "front_left_wing_damage": _value(damage, "front_left_wing_damage"),
        "front_right_wing_damage": _value(damage, "front_right_wing_damage"),
        "rear_wing_damage": _value(damage, "rear_wing_damage"),
        "floor_damage": _value(damage, "floor_damage"),
        "front_wheels_angle": _value(motion_ex, "front_wheels_angle"),
    }
    wheel_fields = {
        "brakes_temperature": "brake_temp", "tyres_surface_temperature": "tyre_surface_temp",
        "tyres_inner_temperature": "tyre_inner_temp", "tyres_pressure": "tyre_pressure",
        "surface_type": "surface_type", "tyres_wear": "tyre_wear",
        "tyres_damage": "tyre_damage", "wheel_speed": "wheel_speed",
        "wheel_slip_ratio": "wheel_slip_ratio", "wheel_slip_angle": "wheel_slip_angle",
    }
    sources = {
        "brakes_temperature": telemetry, "tyres_surface_temperature": telemetry,
        "tyres_inner_temperature": telemetry, "tyres_pressure": telemetry,
        "surface_type": telemetry, "tyres_wear": damage, "tyres_damage": damage,
        "wheel_speed": motion_ex, "wheel_slip_ratio": motion_ex,
        "wheel_slip_angle": motion_ex,
    }
    for source_name, target_prefix in wheel_fields.items():
        for index, corner in enumerate(("rl", "rr", "fl", "fr")):
            row[f"{target_prefix}_{corner}"] = _array(sources[source_name], source_name, index)
    return row


class _FrameWriter:
    def __init__(self, connection: sqlite3.Connection, on_sample=None, on_issue=None) -> None:
        self.connection = connection
        self.on_sample = on_sample
        self.on_issue = on_issue
        self.context = {}
        for uid, details in connection.execute("SELECT session_uid,details_json FROM quality_context_events WHERE kind='session_context' ORDER BY raw_offset"):
            self.context[uid] = json.loads(details)
        self.pending: OrderedDict[tuple[str, int], dict[str, Any]] = OrderedDict()
        self.last_status: dict[str, dict[str, Any]] = {}
        self.last_damage: dict[str, dict[str, Any]] = {}
        self.inserted = 0
        self.incomplete = 0
        self.identity_mismatches = 0
        self.incomplete_missing_parts: Counter[tuple[int, ...]] = Counter()
        placeholders = ",".join("?" for _ in SAMPLE_COLUMNS)
        self.insert_sql = (
            f"INSERT OR REPLACE INTO telemetry_samples ({','.join(SAMPLE_COLUMNS)}) "
            f"VALUES ({placeholders})"
        )

    def observe_context(self, header, body, offset):
        if body is None:
            return
        uid = str(header.session_uid)
        if header.packet_id == 3 and body.get("event_code") in ("FLBK", "SSTA", "SEND"):
            self.connection.execute("INSERT OR REPLACE INTO quality_context_events VALUES (?,?,?,?,?)",
                (uid, offset, header.overall_frame_identifier, body["event_code"], json.dumps(body.get("event_details", {}))))
            return
        if header.packet_id != 1:
            return
        current = {"paused": bool(body.get("game_paused")), "player_car_index": header.player_car_index,
                   "game_mode": body.get("game_mode"), "session_type": body.get("session_type")}
        if self.context.get(uid) != current:
            self.connection.execute("INSERT OR REPLACE INTO quality_context_events VALUES (?,?,?,?,?)",
                                    (uid, offset, header.overall_frame_identifier, "session_context", json.dumps(current)))
            self.context[uid] = current

    def supersede_quality(self, uid, target):
        self.connection.execute("UPDATE frame_quality_issues SET superseded=1 WHERE session_uid=? AND frame_identifier>?", (uid, target))

    def add(
        self,
        header: PacketHeader,
        received_at_ns: int,
        part: dict[str, Any],
        raw_offset: int | None = None,
    ) -> None:
        key = (str(header.session_uid), header.overall_frame_identifier)
        frame = self.pending.get(key)
        if frame is None:
            frame = {"header": header, "received_at_ns": received_at_ns, "parts": {}, "raw_offset": raw_offset,
                     "raw_end_offset": raw_offset}
            self.pending[key] = frame
        else:
            original = frame["header"]
            if (original.packet_format, original.player_car_index, original.frame_identifier) != (
                    header.packet_format, header.player_car_index, header.frame_identifier):
                frame["identity_mismatch"] = True
            frame["received_at_ns"] = min(frame["received_at_ns"], received_at_ns)
            if raw_offset is not None:
                frame["raw_offset"] = min(frame["raw_offset"], raw_offset) if frame.get("raw_offset") is not None else raw_offset
                frame["raw_end_offset"] = max(frame.get("raw_end_offset") or raw_offset, raw_offset)
        frame["parts"][header.packet_id] = part
        frame.setdefault("condition_players", {})[str(header.packet_id)] = header.player_car_index
        while len(self.pending) > 256:
            self.flush_oldest()

    def flush_oldest(self) -> None:
        if not self.pending:
            return
        _, frame = self.pending.popitem(last=False)
        header: PacketHeader = frame["header"]
        uid = str(header.session_uid)
        parts = frame["parts"]
        identity = f"{uid}:{header.packet_format}:{header.player_car_index}"
        invalid_identity = frame.get("identity_mismatch", False)
        if not invalid_identity:
            if 7 in parts:
                self.last_status[identity] = {**parts[7], '_observed_frame': header.frame_identifier,
                                             '_observed_time': header.session_time}
            if 10 in parts:
                self.last_damage[identity] = {**parts[10], '_observed_frame': header.frame_identifier,
                                             '_observed_time': header.session_time}
        if invalid_identity or not REQUIRED_FRAME_PARTS.issubset(parts):
            self.incomplete += 1
            missing = tuple(sorted(REQUIRED_FRAME_PARTS - parts.keys()))
            if missing:
                self.incomplete_missing_parts[missing] += 1
            if invalid_identity:
                self.identity_mismatches += 1
            if REQUIRED_FRAME_PARTS.intersection(parts):
                lap = parts.get(2, {})
                self.connection.execute("INSERT OR REPLACE INTO frame_quality_issues VALUES (?,?,?,?,?,?,?,?,?,0)",
                    (uid, header.overall_frame_identifier, header.frame_identifier, lap.get("current_lap_num"),
                     lap.get("lap_distance"), header.session_time, frame.get("raw_offset"), frame.get("raw_end_offset"),
                     json.dumps(["identity_mismatch"] if invalid_identity else sorted(REQUIRED_FRAME_PARTS - parts.keys()))))
                if self.on_issue is not None:
                    self.on_issue(uid, lap.get("current_lap_num"))
            return
        def prior(cache):
            record = cache.get(identity)
            if record is not None and (record.get('_observed_frame', -1) > header.frame_identifier
                    or record.get('_observed_time', -1) > header.session_time):
                return None  # Late/out-of-order frames and Flashback cannot use future state.
            return record
        row = _frame_row(frame, prior(self.last_status), prior(self.last_damage))
        self.connection.execute(self.insert_sql, tuple(row.get(name) for name in SAMPLE_COLUMNS))
        players = frame.get("condition_players", {})
        lap_player = players.get("2")
        self.connection.execute("INSERT OR REPLACE INTO condition_frames VALUES (?,?,?,?)",
            (uid, header.overall_frame_identifier,
             int(7 in parts and lap_player == header.player_car_index and players.get("7") == lap_player),
             int(lap_player == header.player_car_index and players.get("6") == lap_player)))
        self.connection.execute("INSERT OR REPLACE INTO sample_origins VALUES (?,?,?,?)",
                                (uid, header.overall_frame_identifier, frame.get("raw_offset"), frame.get("raw_end_offset")))
        self.connection.execute("DELETE FROM frame_quality_issues WHERE session_uid=? AND overall_frame_identifier=?",
                                (uid, header.overall_frame_identifier))
        if self.on_sample is not None:
            self.on_sample(row, frame.get("raw_offset"))
        self.inserted += 1

    def flush_all(self) -> None:
        while self.pending:
            self.flush_oldest()


def _write_exports(connection: sqlite3.Connection, output: Path) -> None:
    lap_query = """
        SELECT l.*, a.source_sample_count, a.resampled_point_count,
               a.min_distance_m, a.max_distance_m, a.coverage_ratio,
               a.distance_step_m, a.quality_status
        FROM laps AS l
        LEFT JOIN lap_analysis AS a
          ON a.session_uid = l.session_uid AND a.lap_number = l.lap_number
        ORDER BY l.session_uid, l.lap_number
    """
    cursor = connection.execute(lap_query)
    with (output / "laps.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(column[0] for column in cursor.description)
        writer.writerows(cursor)

    for table, order_by in (
        ("braking_events", "session_uid, lap_number, event_index"),
        ("throttle_events", "session_uid, lap_number, event_index"),
        ("gear_shift_events", "session_uid, lap_number, event_index"),
        ("lap_metrics", "session_uid, lap_number"),
        ("session_metrics", "session_uid"),
        ("lap_quality_details", "session_uid, lap_number"),
        ("frame_quality_issues", "session_uid, overall_frame_identifier"),
        ("quality_context_events", "session_uid, raw_offset"),
    ):
        cursor = connection.execute(f"SELECT * FROM {table} ORDER BY {order_by}")
        with (output / f"{table}.csv").open(
            "w", encoding="utf-8-sig", newline=""
        ) as stream:
            writer = csv.writer(stream)
            writer.writerow(column[0] for column in cursor.description)
            writer.writerows(cursor)

    cursor = connection.execute(
        "SELECT * FROM resampled_lap_samples ORDER BY session_uid, lap_number, distance_m"
    )
    with gzip.open(
        output / "resampled_player_laps.csv.gz", "wt", encoding="utf-8-sig", newline=""
    ) as stream:
        writer = csv.writer(stream)
        writer.writerow(column[0] for column in cursor.description)
        writer.writerows(cursor)


def _decoded_raw(archive: Path):
    """Reference path for acceptance tests; same decoder, no persistent cache."""
    for packet in iter_archive(archive):
        header = body = error = None
        try:
            header = decode_header(packet.payload)
            body = (shared_player_decode(packet.payload, header)
                    if header.packet_id in PLAYER_SCHEMAS else decode_context(packet.payload, header))
        except (ValueError, struct.error) as exc:
            error = f"{type(exc).__name__}: {exc}"
        yield FoundationPacket(packet.offset, packet.received_at_ns, header, body, error)


@contextmanager
def _analysis_input(archive: Path, use_foundation: bool, foundation_database: Path | None, recover_tail: bool = False, control=None):
    if not use_foundation:
        yield _decoded_raw(archive), None
        return
    database = foundation_database or foundation_path(archive)
    metadata_path = archive.parent / "metadata.json"
    recording = False
    if archive.name == "raw_packets.bin" and metadata_path.is_file():
        recording = json.loads(metadata_path.read_text(encoding="utf-8")).get("summary", {}).get("status") == "recording"
    capture_lock = archive.parent / "capture.lock"
    if capture_lock.is_file():
        recording = lease_active(capture_lock)
    elif lease_active(database.with_suffix(".lock")):
        recording = True
    prepared = None
    if recording:
        if not database.is_file():
            raise ValueError("基础数据尚未准备好，请稍后重试；采集仍继续运行。")
    else:
        prepared = ensure_foundation(archive, database, recover_tail=recover_tail, control=control)
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        connection.execute("BEGIN")  # A consistent point-in-time view while capture continues.
        version = connection.execute("SELECT value FROM metadata WHERE key='version'").fetchone()
        if not version or json.loads(version[0]) != [FOUNDATION_VERSION, DECODER_VERSION]:
            raise ValueError("基础数据版本不兼容，请保留原文件并在新目录重新建立。")
        row = connection.execute("SELECT value FROM metadata WHERE key='progress'").fetchone()
        if row is None:
            raise ValueError("基础数据尚未提交，请稍后重试。")
        progress = json.loads(row[0])
        progress["recording"] = recording
        progress["tail_error"] = prepared["tail_error"] if prepared else None
        yield iter_foundation(connection), progress


def build_analysis(
    source: Path,
    output: Path | None = None,
    distance_step_m: float = 5.0,
    include_invalid: bool = False,
    progress_every: int = 50_000,
    use_foundation: bool = True,
    foundation_database: Path | None = None,
    export_files: bool = False,
    force_rebuild: bool = False,
    recover_tail: bool = False,
    control=None,
) -> dict[str, Any]:
    archive, session_directory = _archive_path(source)
    if not archive.is_file():
        raise FileNotFoundError(f"raw archive not found: {archive}")
    # Never replace the user's pre-0.8 analysis database or exports.
    output_directory = (output or session_directory / "analysis_v0.10.0_reference").expanduser().resolve()
    output_directory.mkdir(parents=True, exist_ok=True)
    database = output_directory / "telemetry_analysis.db"
    if database.is_file():
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as existing:
            if existing.execute("SELECT value FROM metadata WHERE key='incremental_version'").fetchone():
                raise ValueError("该目录已有增量分析数据库，请为全量重建或导出选择独立输出目录。")
    temporary_database = output_directory / f"telemetry_analysis.{uuid4().hex}.db.tmp"

    packet_counts: Counter[int] = Counter()
    errors: list[dict[str, Any]] = []
    error_count = 0
    session_info: dict[str, dict[str, Any]] = {}
    session_bounds: dict[str, list[int]] = {}
    histories: dict[str, tuple[int, dict[str, Any]]] = {}
    tyre_trackers: dict[str, tuple[int, PlayerTyreTracker]] = {}
    flashback_count = 0
    total = 0
    input_stack = ExitStack()
    connection = None
    try:
        inputs, foundation_progress = input_stack.enter_context(_analysis_input(archive, use_foundation, foundation_database, recover_tail, control))
        cache_key = {
            "source_archive": str(archive),
            "cursor": foundation_progress["cursor"] if foundation_progress else None,
            **algorithm_contract(distance_step_m, include_invalid),
            "export_files": export_files,
            "tail_error": foundation_progress.get("tail_error") if foundation_progress else None,
        }
        summary_path = output_directory / "analysis_summary.json"
        if use_foundation and not force_rebuild and database.is_file() and summary_path.is_file():
            try:
                old_summary = json.loads(summary_path.read_text(encoding="utf-8"))
                if old_summary.get("cache_key") == cache_key:
                    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as cached:
                        row = cached.execute("SELECT value FROM metadata WHERE key='cache_key'").fetchone()
                        if row and json.loads(row[0]) == cache_key:
                            input_stack.close()
                            return {**old_summary, "reused": True,
                                    "live_snapshot": foundation_progress.get("recording", False)}
            except (json.JSONDecodeError, sqlite3.DatabaseError):
                pass  # Only a derived cache is rebuilt; never conceal a Raw CRC failure.
        connection = sqlite3.connect(temporary_database)
        create_schema(connection)
        connection.execute("PRAGMA synchronous = NORMAL")
        writer = _FrameWriter(connection)
        for archived in inputs:
            total += 1
            if control:
                control.report('解析玩家数据', total)
            try:
                if archived.error:
                    raise ValueError(archived.error)
                header = archived.header
                if header is None:
                    raise ValueError("packet has no complete F1 header")
                uid = str(header.session_uid)
                bounds = session_bounds.setdefault(
                    uid, [archived.received_at_ns, archived.received_at_ns]
                )
                bounds[0] = min(bounds[0], archived.received_at_ns)
                bounds[1] = max(bounds[1], archived.received_at_ns)
                packet_counts[header.packet_id] += 1
                writer.observe_context(header, archived.body, archived.offset)

                if header.packet_id in PlayerTyreTracker.PACKET_IDS and archived.body is not None:
                    if uid not in tyre_trackers:
                        tyre_trackers[uid] = (header.player_car_index, PlayerTyreTracker())
                    player, tyre_tracker = tyre_trackers[uid]
                    if player != header.player_car_index:
                        tyre_tracker = PlayerTyreTracker()
                        tyre_trackers[uid] = (header.player_car_index, tyre_tracker)
                    tyre_tracker.observe(header, archived.body)
                    if tyre_tracker.invalidated_laps and uid in histories:
                        received, history = histories[uid]
                        histories[uid] = (received, {**history, "num_laps": min(
                            history["num_laps"], min(tyre_tracker.invalidated_laps) - 1)})

                if header.packet_id in FRAME_PACKET_IDS:
                    part = archived.body
                    if part is not None:
                        writer.add(header, archived.received_at_ns, part, archived.offset)
                elif header.packet_id == 1:
                    decoded = archived.body
                    if decoded is None:
                        continue
                    session_info[uid] = {
                        "session_uid": uid, "game": game_label(header),
                        "game_year": header.game_year, "packet_format": header.packet_format,
                        "game_mode": decoded["game_mode_name"],
                        "game_mode_id": decoded["game_mode"],
                        "session_type": decoded["session_type_name"],
                        "session_type_id": decoded["session_type"],
                        "track": decoded["track_name"], "track_id": decoded["track_id"],
                        "track_length_m": decoded["track_length"],
                        "total_laps": decoded["total_laps"],
                    }
                elif header.packet_id == 3:
                    decoded = archived.body
                    if decoded is None:
                        continue
                    if decoded["event_code"] == "FLBK":
                        writer.flush_all()
                        target = decoded["event_details"]["flashback_frame_identifier"]
                        writer.supersede_quality(uid, target)
                        connection.execute(
                            """
                            UPDATE telemetry_samples SET superseded = 1
                            WHERE session_uid = ? AND superseded = 0
                              AND frame_identifier > ?
                            """,
                            (uid, target),
                        )
                        flashback_count += 1
                    connection.execute(
                        """
                        INSERT INTO events (
                            session_uid, received_at_ns, session_time,
                            frame_identifier, overall_frame_identifier,
                            event_code, event_name, event_details_json
                        ) VALUES (?,?,?,?,?,?,?,?)
                        """,
                        (
                            uid, archived.received_at_ns, header.session_time,
                            header.frame_identifier, header.overall_frame_identifier,
                            decoded["event_code"], decoded["event_name"],
                            json.dumps(decoded["event_details"], ensure_ascii=False),
                        ),
                    )
                elif header.packet_id == 11:
                    decoded = archived.body
                    if decoded is None:
                        continue
                    if decoded["car_idx"] == header.player_car_index:
                        histories[uid] = (archived.received_at_ns, decoded)
            except Exception as exc:
                error_count += 1
                errors.append(
                    {
                        "record_number": total, "offset": archived.offset,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                if len(errors) > 200:
                    errors.pop()
            if progress_every and total % progress_every == 0:
                print(f"Analysed {total:,} raw packets...", flush=True)

        writer.flush_all()
        input_stack.close()
        for uid, bounds in session_bounds.items():
            info = session_info.get(uid, {"session_uid": uid})
            connection.execute(
                """
                INSERT INTO sessions (
                    session_uid, game_mode, game_mode_id, session_type,
                    session_type_id, track, track_id, track_length_m,
                    total_laps, first_received_at_ns, last_received_at_ns
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    uid, info.get("game_mode"), info.get("game_mode_id"),
                    info.get("session_type"), info.get("session_type_id"),
                    info.get("track"), info.get("track_id"),
                    info.get("track_length_m"), info.get("total_laps"),
                    bounds[0], bounds[1],
                ),
            )
        for uid, (received_at_ns, decoded) in histories.items():
            connection.executemany(
                """
                INSERT OR REPLACE INTO laps (
                    session_uid, lap_number, lap_time_ms, sector1_ms,
                    sector2_ms, sector3_ms, lap_valid, sector1_valid,
                    sector2_valid, sector3_valid, snapshot_received_at_ns
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                _lap_rows(uid, received_at_ns, decoded),
            )
            if uid in tyre_trackers:
                for number, tyre in tyre_trackers[uid][1].laps(decoded).items():
                    values = {"session_uid": uid, "lap_number": number, **asdict(tyre)}
                    connection.execute(
                        f"INSERT INTO lap_tyres ({','.join(values)}) VALUES ({','.join('?' for _ in values)})",
                        tuple(values.values()),
                    )
        resampling = resample_laps(
            connection, distance_step_m=distance_step_m,
            include_invalid=include_invalid,
            control=control,
        )
        event_analysis = analyze_driving_events(connection, control=control)
        metadata = {
            "schema_version": SCHEMA_VERSION,
            "source_archive": str(archive),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "distance_step_m": distance_step_m,
            "include_invalid_laps": include_invalid,
            "event_detection_version": EVENT_DETECTION_VERSION,
            "event_thresholds": EVENT_THRESHOLDS,
            "analysis_engine_version": ANALYSIS_ENGINE_VERSION,
            "foundation_cursor": foundation_progress["cursor"] if foundation_progress else None,
            "cache_key": cache_key,
        }
        connection.executemany(
            "INSERT INTO metadata(key, value) VALUES (?, ?)",
            ((key, json.dumps(value, ensure_ascii=False)) for key, value in metadata.items()),
        )
        connection.commit()
        active_samples = connection.execute(
            "SELECT COUNT(*) FROM telemetry_samples WHERE superseded = 0"
        ).fetchone()[0]
        superseded_samples = connection.execute(
            "SELECT COUNT(*) FROM telemetry_samples WHERE superseded = 1"
        ).fetchone()[0]
        if export_files:
            _write_exports(connection, output_directory)
        summary = {
            "status": ("partial_verified_prefix" if foundation_progress and foundation_progress.get("tail_error")
                       else "complete" if not errors else "completed_with_errors"),
            "source_archive": str(archive),
            "analysis_database": str(database),
            "schema_version": SCHEMA_VERSION,
            "total_raw_packets": total,
            "packet_counts": {str(key): packet_counts[key] for key in sorted(packet_counts)},
            "decode_error_count": error_count,
            "error_details_truncated": error_count > len(errors),
            "session_count": len(session_bounds),
            "final_lap_count": sum(
                len(_lap_rows(uid, received, decoded))
                for uid, (received, decoded) in histories.items()
            ),
            "telemetry_samples": writer.inserted,
            "active_telemetry_samples": active_samples,
            "superseded_telemetry_samples": superseded_samples,
            "incomplete_frame_count": writer.incomplete,
            "identity_mismatch_frame_count": writer.identity_mismatches,
            "incomplete_frames_by_missing_packet": {
                ",".join(str(packet_id) for packet_id in missing): count
                for missing, count in sorted(writer.incomplete_missing_parts.items())
            },
            "flashback_count": flashback_count,
            "foundation_database": str(foundation_database or foundation_path(archive)) if use_foundation else None,
            "foundation_cursor": foundation_progress["cursor"] if foundation_progress else None,
            "analysis_engine_version": ANALYSIS_ENGINE_VERSION,
            "distance_step_m": distance_step_m,
            "include_invalid_laps": include_invalid,
            "export_files": export_files,
            "cache_key": cache_key,
            "reused": False,
            "live_snapshot": foundation_progress.get("recording", False) if foundation_progress else False,
            "tail_error": foundation_progress.get("tail_error") if foundation_progress else None,
            **resampling,
            **event_analysis,
        }
    except Exception:
        input_stack.close()
        if connection is not None:
            connection.close()
        if temporary_database.exists():
            temporary_database.unlink()
        raise
    else:
        connection.close()
        os.replace(temporary_database, database)
        _atomic_json(output_directory / "analysis_errors.json", errors)
        _atomic_json(output_directory / "analysis_summary.json", summary)
        return summary
