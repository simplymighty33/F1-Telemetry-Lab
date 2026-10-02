"""Distance-domain lap normalization without external numeric libraries."""

from __future__ import annotations

from bisect import bisect_right, bisect_left
import math
import sqlite3
from typing import Any
from analysis.quality import inspect_source, finite, SMALL_CLOCK_JITTER_MS
from analysis.quality import QUALITY_VERSION
import json


SOURCE_COLUMNS = (
    "frame_identifier", "session_time", "received_at_ns", "pit_status", "driver_status",
    "overall_frame_identifier", "lap_distance_m", "current_lap_time_ms",
    "speed_kph", "throttle", "brake", "steer", "gear", "engine_rpm", "drs",
    "g_lateral", "g_longitudinal", "world_x", "world_y", "world_z",
    "fuel_in_tank", "ers_store_energy", "tyre_wear_rl", "tyre_wear_rr",
    "tyre_wear_fl", "tyre_wear_fr", "front_wheels_angle",
    "wheel_slip_ratio_rl", "wheel_slip_ratio_rr", "wheel_slip_ratio_fl",
    "wheel_slip_ratio_fr",
)

CONTINUOUS_FIELDS = (
    "current_lap_time_ms", "speed_kph", "throttle", "brake", "steer",
    "engine_rpm", "g_lateral", "g_longitudinal", "world_x", "world_y",
    "world_z", "fuel_in_tank", "ers_store_energy", "tyre_wear_rl",
    "tyre_wear_rr", "tyre_wear_fl", "tyre_wear_fr", "front_wheels_angle",
    "wheel_slip_ratio_rl", "wheel_slip_ratio_rr", "wheel_slip_ratio_fl",
    "wheel_slip_ratio_fr",
)


def _linear(left: Any, right: Any, ratio: float) -> float | None:
    if left is None:
        return None if right is None else float(right)
    if right is None:
        return float(left)
    return float(left) + (float(right) - float(left)) * ratio


def _interpolate(rows: list[dict[str, Any]], distances: list[float], target: float) -> dict[str, Any]:
    right_index = bisect_right(distances, target)
    if right_index <= 0:
        return rows[0]
    if right_index >= len(rows):
        return rows[-1]
    left = rows[right_index - 1]
    right = rows[right_index]
    span = right["lap_distance_m"] - left["lap_distance_m"]
    ratio = 0.0 if span <= 0 else (target - left["lap_distance_m"]) / span
    result = {name: _linear(left[name], right[name], ratio) for name in CONTINUOUS_FIELDS}
    nearest = left if ratio < 0.5 else right
    result["gear"] = nearest["gear"]
    result["drs"] = nearest["drs"]
    return result


def _source_rows(
    connection: sqlite3.Connection,
    session_uid: str,
    lap_number: int,
    track_length_m: float | None,
) -> list[dict[str, Any]]:
    query = f"""
        SELECT {', '.join(SOURCE_COLUMNS)}
        FROM telemetry_samples
        WHERE session_uid = ? AND lap_number = ? AND superseded = 0
          AND lap_distance_m >= 0
        ORDER BY overall_frame_identifier
    """
    timeline: list[dict[str, Any]] = []
    candidates: list[list[dict[str, Any]]] = []
    previous_distance: float | None = None
    for values in connection.execute(query, (session_uid, lap_number)):
        row = dict(zip(SOURCE_COLUMNS, values))
        distance = float(row["lap_distance_m"])
        if not math.isfinite(distance):
            continue
        if previous_distance is not None and previous_distance - distance > 20.0:
            crossed_finish = bool(
                track_length_m
                and previous_distance >= float(track_length_m) * 0.90
                and distance <= float(track_length_m) * 0.10
            )
            if crossed_finish:
                if timeline:
                    candidates.append(timeline)
                timeline = []
            else:
                timeline = [item for item in timeline if item["lap_distance_m"] < distance]
        timeline.append(row)
        previous_distance = distance

    if timeline:
        candidates.append(timeline)
    if not candidates:
        return []

    def coverage(candidate: list[dict[str, Any]]) -> tuple[float, int]:
        distances = [float(item["lap_distance_m"]) for item in candidate]
        return max(distances) - min(distances), len(candidate)

    timeline = max(candidates, key=coverage)

    deduplicated: dict[float, dict[str, Any]] = {}
    for row in timeline:
        distance = float(row["lap_distance_m"])
        deduplicated[round(distance, 3)] = row
    ordered = sorted(deduplicated.values(), key=lambda row: row["lap_distance_m"])
    # Small local clock jitter exists in real Lap Data around corrections.
    # Normalize only the derived in-memory view; Raw and telemetry_samples
    # remain untouched. A genuine reset/large rollback must still be rejected.
    for before, row in zip(ordered, ordered[1:]):
        left_time, right_time = before["current_lap_time_ms"], row["current_lap_time_ms"]
        if (finite(left_time) and finite(right_time)
                and 0 < left_time - right_time <= SMALL_CLOCK_JITTER_MS
                and row["lap_distance_m"] - before["lap_distance_m"] <= 5.0):
            row["current_lap_time_ms"] = left_time
    if connection.execute("SELECT 1 FROM sqlite_master WHERE name='frame_quality_issues'").fetchone():
        missing_frames = [r[0] for r in connection.execute("""SELECT overall_frame_identifier FROM frame_quality_issues
            WHERE session_uid=? AND superseded=0 AND (lap_number=? OR lap_number IS NULL)
            ORDER BY overall_frame_identifier""", (session_uid, lap_number))]
        for before, row in zip(ordered, ordered[1:]):
            first, last = before["overall_frame_identifier"], row["overall_frame_identifier"]
            row["_incomplete_before"] = max(0, bisect_left(missing_frames, last) - bisect_right(missing_frames, first)) if last > first else 0
    return ordered


def resample_laps(
    connection: sqlite3.Connection,
    distance_step_m: float = 5.0,
    include_invalid: bool = False,
    minimum_coverage: float = 0.90,
    lap_keys: set[tuple[str, int]] | None = None,
    control=None,
) -> dict[str, int]:
    if not math.isfinite(distance_step_m) or distance_step_m <= 0:
        raise ValueError("distance step must be a positive finite number")
    laps = connection.execute(
        """
        SELECT l.session_uid, l.lap_number, l.lap_valid, s.track_length_m
        FROM laps AS l
        LEFT JOIN sessions AS s ON s.session_uid = l.session_uid
        ORDER BY l.session_uid, l.lap_number
        """
    ).fetchall()
    if lap_keys is not None:
        laps = [row for row in laps if (row[0], row[1]) in lap_keys]
    accepted = rejected = points_written = 0
    insert_sql = """
        INSERT INTO resampled_lap_samples (
            session_uid, lap_number, distance_m, lap_time_ms, speed_kph,
            throttle, brake, steer, gear, engine_rpm, drs, g_lateral,
            g_longitudinal, world_x, world_y, world_z, fuel_in_tank,
            ers_store_energy, tyre_wear_rl, tyre_wear_rr, tyre_wear_fl,
            tyre_wear_fr, front_wheels_angle, wheel_slip_ratio_rl,
            wheel_slip_ratio_rr, wheel_slip_ratio_fl, wheel_slip_ratio_fr
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """
    for session_uid, lap_number, lap_valid, track_length in laps:
        if control:
            control.report('距离重采样', accepted + rejected, len(laps))
        rows = _source_rows(connection, session_uid, lap_number, track_length)
        minimum = rows[0]["lap_distance_m"] if rows else None
        maximum = rows[-1]["lap_distance_m"] if rows else None
        reference_length = float(track_length or 0)
        if reference_length <= 0 and maximum is not None:
            reference_length = float(maximum)
        span = 0.0 if minimum is None or maximum is None else max(0.0, maximum - minimum)
        coverage = span / reference_length if reference_length > 0 else 0.0
        quality = inspect_source(rows)
        status = "ready"
        if not include_invalid and not lap_valid:
            status = "invalid_lap"
        elif len(rows) < 2:
            status = "insufficient_samples"
        elif coverage < minimum_coverage:
            status = "partial_capture"
        else:
            status = quality.status

        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "lap_quality_details" in tables:
            official = connection.execute("SELECT lap_time_ms FROM laps WHERE session_uid=? AND lap_number=?",
                                          (session_uid, lap_number)).fetchone()[0]
            frames = [row["overall_frame_identifier"] for row in rows]
            origins = connection.execute("""SELECT MIN(o.raw_start_offset),MAX(o.raw_end_offset)
                FROM sample_origins o JOIN telemetry_samples t USING(session_uid,overall_frame_identifier)
                WHERE t.session_uid=? AND t.lap_number=? AND t.superseded=0""", (session_uid, lap_number)).fetchone()
            missing_query = """FROM frame_quality_issues WHERE session_uid=? AND superseded=0
                AND (lap_number=? OR (lap_number IS NULL AND overall_frame_identifier BETWEEN ? AND ?))"""
            missing_args = (session_uid, lap_number, min(frames, default=-1), max(frames, default=-1))
            missing_count = connection.execute("SELECT COUNT(*) " + missing_query, missing_args).fetchone()[0]
            missing = [dict(zip(("frame", "distance_m", "raw_start_offset", "raw_end_offset", "missing_parts"), r))
                       for r in connection.execute("""SELECT overall_frame_identifier,distance_m,raw_start_offset,
                       raw_end_offset,missing_parts_json """ + missing_query + " ORDER BY overall_frame_identifier LIMIT 200", missing_args)]
            for item in missing:
                item["missing_parts"] = json.loads(item["missing_parts"])
            details = dict(status=status, start_m=minimum, end_m=maximum, track_length_m=track_length,
                           start_time_ms=rows[0]["current_lap_time_ms"] if rows else None,
                           end_time_ms=rows[-1]["current_lap_time_ms"] if rows else None,
                           official_end_difference_ms=official - rows[-1]["current_lap_time_ms"] if rows else None,
                           official_lap_time_ms=official, cadence_ms=quality.cadence_ms,
                           source_frame_range=[frames[0], frames[-1]] if frames else None,
                           raw_offset_range=list(origins), issues=quality.issues[:200], issue_count=len(quality.issues),
                           incomplete_frames=missing, incomplete_frame_count=missing_count,
                           unknown_lap_assignment="frame_range_only_when_lap_packet_missing",
                           timing_source="game_session_history", sample_source="observed_player_frames")
            details["contexts"] = [dict(kind=kind, raw_offset=offset, details=json.loads(value))
                for kind, offset, value in connection.execute("""SELECT kind,raw_offset,details_json FROM quality_context_events
                    WHERE session_uid=? AND overall_frame_identifier BETWEEN ? AND ? ORDER BY raw_offset LIMIT 50""",
                    (session_uid, min(frames, default=-1), max(frames, default=-1)))]
            connection.execute("INSERT OR REPLACE INTO lap_quality_details VALUES (?,?,?,?)",
                               (session_uid, lap_number, QUALITY_VERSION, json.dumps(details, ensure_ascii=False)))

        resampled_count = 0
        if status == "ready":
            distances = [float(row["lap_distance_m"]) for row in rows]
            first_grid = math.ceil(distances[0] / distance_step_m) * distance_step_m
            last_grid = math.floor(distances[-1] / distance_step_m) * distance_step_m
            batch = []
            point = first_grid
            while point <= last_grid + 1e-9:
                if not quality.supports(point):
                    point += distance_step_m
                    continue
                value = _interpolate(rows, distances, point)
                batch.append(
                    (
                        session_uid, lap_number, round(point, 6),
                        value["current_lap_time_ms"], value["speed_kph"],
                        value["throttle"], value["brake"], value["steer"],
                        value["gear"], value["engine_rpm"], value["drs"],
                        value["g_lateral"], value["g_longitudinal"],
                        value["world_x"], value["world_y"], value["world_z"],
                        value["fuel_in_tank"], value["ers_store_energy"],
                        value["tyre_wear_rl"], value["tyre_wear_rr"],
                        value["tyre_wear_fl"], value["tyre_wear_fr"],
                        value["front_wheels_angle"], value["wheel_slip_ratio_rl"],
                        value["wheel_slip_ratio_rr"], value["wheel_slip_ratio_fl"],
                        value["wheel_slip_ratio_fr"],
                    )
                )
                point += distance_step_m
            connection.executemany(insert_sql, batch)
            resampled_count = len(batch)
            points_written += resampled_count
            accepted += 1
        else:
            rejected += 1

        connection.execute(
            """
            INSERT INTO lap_analysis (
                session_uid, lap_number, source_sample_count,
                resampled_point_count, min_distance_m, max_distance_m,
                coverage_ratio, distance_step_m, quality_status
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                session_uid, lap_number, len(rows), resampled_count, minimum,
                maximum, coverage, distance_step_m, status,
            ),
        )
    return {
        "laps_considered": len(laps),
        "laps_resampled": accepted,
        "laps_rejected": rejected,
        "resampled_points": points_written,
    }
