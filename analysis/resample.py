"""Distance-domain lap normalization without external numeric libraries."""

from __future__ import annotations

from bisect import bisect_right
import math
import sqlite3
from typing import Any


SOURCE_COLUMNS = (
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
    return sorted(deduplicated.values(), key=lambda row: row["lap_distance_m"])


def resample_laps(
    connection: sqlite3.Connection,
    distance_step_m: float = 5.0,
    include_invalid: bool = False,
    minimum_coverage: float = 0.90,
    lap_keys: set[tuple[str, int]] | None = None,
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
        rows = _source_rows(connection, session_uid, lap_number, track_length)
        minimum = rows[0]["lap_distance_m"] if rows else None
        maximum = rows[-1]["lap_distance_m"] if rows else None
        reference_length = float(track_length or 0)
        if reference_length <= 0 and maximum is not None:
            reference_length = float(maximum)
        span = 0.0 if minimum is None or maximum is None else max(0.0, maximum - minimum)
        coverage = span / reference_length if reference_length > 0 else 0.0
        status = "ready"
        if not include_invalid and not lap_valid:
            status = "invalid_lap"
        elif len(rows) < 2:
            status = "insufficient_samples"
        elif coverage < minimum_coverage:
            status = "partial_capture"

        resampled_count = 0
        if status == "ready":
            distances = [float(row["lap_distance_m"]) for row in rows]
            first_grid = math.ceil(distances[0] / distance_step_m) * distance_step_m
            last_grid = math.floor(distances[-1] / distance_step_m) * distance_step_m
            batch = []
            point = first_grid
            while point <= last_grid + 1e-9:
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
