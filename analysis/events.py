"""Derive deterministic driving events and lap metrics from normalized laps."""

from __future__ import annotations

from collections import defaultdict
import sqlite3
from typing import Any, Iterable
from analysis.quality import inspect_source
from analysis.resample import _source_rows


SAMPLE_COLUMNS = (
    "distance_m", "lap_time_ms", "speed_kph", "throttle", "brake", "steer",
    "gear", "engine_rpm", "g_longitudinal", "front_wheels_angle",
)

EVENT_DETECTION_VERSION = 2
EVENT_THRESHOLDS = {
    "brake_start": 0.05,
    "brake_end": 0.02,
    "throttle_pickup_start": 0.20,
    "full_throttle": 0.98,
    "coast_throttle_max": 0.10,
    "timeline_rollback_m": 20.0,
}


def _number(value: Any, default: float = 0.0) -> float:
    return default if value is None else float(value)


def _elapsed(start: dict[str, Any], end: dict[str, Any]) -> float | None:
    if start["lap_time_ms"] is None or end["lap_time_ms"] is None:
        return None
    return max(0.0, float(end["lap_time_ms"]) - float(start["lap_time_ms"]))


def _total_variation(values: Iterable[Any]) -> float:
    valid = [float(value) for value in values if value is not None]
    return sum(abs(right - left) for left, right in zip(valid, valid[1:]))


def _braking_events(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    start_index: int | None = None
    for index, row in enumerate(rows):
        brake = _number(row["brake"])
        if start_index is None and brake >= EVENT_THRESHOLDS["brake_start"]:
            start_index = index
        if start_index is None:
            continue
        is_end = brake <= EVENT_THRESHOLDS["brake_end"] or index == len(rows) - 1
        if not is_end:
            continue
        end_index = index
        active_end = (
            max(start_index, end_index - 1)
            if brake <= EVENT_THRESHOLDS["brake_end"] else end_index
        )
        active = rows[start_index:active_end + 1]
        start = rows[start_index]
        end = rows[end_index]
        span = float(end["distance_m"]) - float(start["distance_m"])
        duration = _elapsed(start, end)
        if active and (span >= 5.0 or (duration is not None and duration >= 100.0)):
            peak_offset = max(range(len(active)), key=lambda item: _number(active[item]["brake"]))
            peak_index = start_index + peak_offset
            peak = rows[peak_index]
            release_rows = rows[peak_index:end_index + 1]
            release_variation = _total_variation(row["brake"] for row in release_rows)
            net_release = max(0.0, _number(peak["brake"]) - _number(end["brake"]))
            smoothness = 1.0 if release_variation <= 1e-9 else min(1.0, net_release / release_variation)
            speeds = [_number(item["speed_kph"]) for item in active if item["speed_kph"] is not None]
            longitudinal = [
                float(item["g_longitudinal"])
                for item in active if item["g_longitudinal"] is not None
            ]
            events.append(
                {
                    "start": start, "peak": peak, "end": end,
                    "duration_ms": duration,
                    "minimum_speed_kph": min(speeds) if speeds else None,
                    "peak_brake": _number(peak["brake"]),
                    "minimum_longitudinal_g": min(longitudinal) if longitudinal else None,
                    "release_distance_m": max(
                        0.0, float(end["distance_m"]) - float(peak["distance_m"])
                    ),
                    "release_smoothness": smoothness,
                }
            )
        start_index = None
    return events


def _throttle_events(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    start_index: int | None = None
    for index in range(1, len(rows)):
        previous = rows[index - 1]
        row = rows[index]
        throttle = _number(row["throttle"])
        previous_throttle = _number(previous["throttle"])
        if (
            start_index is None
            and previous_throttle <= EVENT_THRESHOLDS["throttle_pickup_start"] < throttle
            and _number(row["brake"]) < EVENT_THRESHOLDS["brake_start"]
        ):
            start_index = index - 1
        if start_index is None:
            continue
        falling = throttle < previous_throttle - 0.15
        interrupted = _number(row["brake"]) >= EVENT_THRESHOLDS["brake_start"]
        is_end = throttle >= 0.95 or falling or interrupted or index == len(rows) - 1
        if not is_end:
            continue
        end_index = index - 1 if (falling or interrupted) else index
        start = rows[start_index]
        end = rows[max(start_index, end_index)]
        rise = _number(end["throttle"]) - _number(start["throttle"])
        span = float(end["distance_m"]) - float(start["distance_m"])
        duration = _elapsed(start, end)
        if rise >= 0.25 and span >= 5.0:
            rate = None if not duration else rise / (duration / 1000.0)
            events.append({"start": start, "end": end, "duration_ms": duration, "rate": rate})
        start_index = None
    return events


def _gear_shift_events(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for previous, row in zip(rows, rows[1:]):
        before = previous["gear"]
        after = row["gear"]
        if before is None or after is None:
            continue
        before = int(before)
        after = int(after)
        if before < 1 or after < 1 or before == after:
            continue
        events.append(
            {
                "row": row, "previous": previous,
                "direction": "up" if after > before else "down",
                "from_gear": before, "to_gear": after,
            }
        )
    return events


def _steering_corrections(rows: list[dict[str, Any]]) -> int:
    corrections = 0
    last_direction = 0
    last_distance = -1_000.0
    previous: float | None = None
    for row in rows:
        if row["steer"] is None:
            continue
        current = float(row["steer"])
        if previous is None:
            previous = current
            continue
        delta = current - previous
        previous = current
        if abs(delta) < 0.02 or abs(current) < 0.05:
            continue
        direction = 1 if delta > 0 else -1
        distance = float(row["distance_m"])
        if last_direction and direction != last_direction and distance - last_distance >= 10.0:
            corrections += 1
            last_distance = distance
        last_direction = direction
    return corrections


def _insert_events(
    connection: sqlite3.Connection,
    session_uid: str,
    lap_number: int,
    braking: list[dict[str, Any]],
    throttle: list[dict[str, Any]],
    shifts: list[dict[str, Any]],
) -> None:
    connection.executemany(
        """
        INSERT INTO braking_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            (
                session_uid, lap_number, index,
                event["start"]["distance_m"], event["peak"]["distance_m"],
                event["end"]["distance_m"], event["start"]["lap_time_ms"],
                event["end"]["lap_time_ms"], event["duration_ms"],
                event["start"]["speed_kph"], event["minimum_speed_kph"],
                event["end"]["speed_kph"], event["peak_brake"],
                event["minimum_longitudinal_g"], event["release_distance_m"],
                event["release_smoothness"], event["peak"]["steer"],
            )
            for index, event in enumerate(braking, 1)
        ),
    )
    connection.executemany(
        """
        INSERT INTO throttle_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            (
                session_uid, lap_number, index, event["start"]["distance_m"],
                event["end"]["distance_m"], event["start"]["lap_time_ms"],
                event["end"]["lap_time_ms"], event["duration_ms"],
                event["start"]["speed_kph"], event["end"]["speed_kph"],
                event["start"]["throttle"], event["end"]["throttle"], event["rate"],
            )
            for index, event in enumerate(throttle, 1)
        ),
    )
    connection.executemany(
        """
        INSERT INTO gear_shift_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            (
                session_uid, lap_number, index, event["row"]["distance_m"],
                event["row"]["lap_time_ms"], event["direction"],
                event["from_gear"], event["to_gear"],
                event["previous"]["engine_rpm"], event["row"]["engine_rpm"],
                event["row"]["throttle"], event["row"]["brake"],
            )
            for index, event in enumerate(shifts, 1)
        ),
    )


def _session_metrics(connection: sqlite3.Connection) -> None:
    sessions = connection.execute("SELECT session_uid FROM sessions ORDER BY session_uid").fetchall()
    for (session_uid,) in sessions:
        laps = connection.execute(
            """
            SELECT lap_number, lap_time_ms, sector1_ms, sector2_ms, sector3_ms,
                   sector1_valid, sector2_valid, sector3_valid
            FROM laps WHERE session_uid = ? AND lap_valid = 1
            ORDER BY lap_time_ms, lap_number
            """,
            (session_uid,),
        ).fetchall()
        if not laps:
            connection.execute(
                "INSERT INTO session_metrics VALUES (?,?,?,?,?,?,?,?,?)",
                (session_uid, 0, None, None, None, None, None, None, None),
            )
            continue
        best = laps[0]
        sector_bests = []
        for value_index, valid_index in ((2, 5), (3, 6), (4, 7)):
            candidates = [row[value_index] for row in laps if row[valid_index] and row[value_index] > 0]
            sector_bests.append(min(candidates) if candidates else None)
        theoretical = sum(sector_bests) if all(value is not None for value in sector_bests) else None
        connection.execute(
            "INSERT INTO session_metrics VALUES (?,?,?,?,?,?,?,?,?)",
            (
                session_uid, len(laps), best[0], best[1], *sector_bests,
                theoretical, None if theoretical is None else best[1] - theoretical,
            ),
        )


def analyze_driving_events(connection: sqlite3.Connection, only_laps: set[tuple[str, int]] | None = None) -> dict[str, int]:
    """Populate event and metric tables for every successfully resampled lap."""
    lap_keys = connection.execute(
        """
        SELECT r.session_uid, r.lap_number, l.lap_time_ms, l.lap_valid
        FROM resampled_lap_samples AS r
        JOIN laps AS l ON l.session_uid = r.session_uid AND l.lap_number = r.lap_number
        GROUP BY r.session_uid, r.lap_number
        ORDER BY r.session_uid, r.lap_number
        """
    ).fetchall()
    best_times: dict[str, int] = {}
    for session_uid, _, lap_time_ms, lap_valid in lap_keys:
        if lap_valid and (session_uid not in best_times or lap_time_ms < best_times[session_uid]):
            best_times[session_uid] = lap_time_ms
    fallback_times: dict[str, int] = {}
    for session_uid, _, lap_time_ms, _ in lap_keys:
        fallback_times[session_uid] = min(
            fallback_times.get(session_uid, lap_time_ms), lap_time_ms
        )
    for session_uid, lap_time_ms in fallback_times.items():
        best_times.setdefault(session_uid, lap_time_ms)

    counts: defaultdict[str, int] = defaultdict(int)
    select = f"SELECT {', '.join(SAMPLE_COLUMNS)} FROM resampled_lap_samples "
    select += "WHERE session_uid = ? AND lap_number = ? ORDER BY distance_m"
    for session_uid, lap_number, lap_time_ms, _ in lap_keys:
        if only_laps is not None and (session_uid, lap_number) not in only_laps:
            continue
        rows = [
            dict(zip(SAMPLE_COLUMNS, values))
            for values in connection.execute(select, (session_uid, lap_number))
        ]
        if not rows:
            continue
        length = connection.execute("SELECT track_length_m FROM sessions WHERE session_uid=?", (session_uid,)).fetchone()
        quality = inspect_source(_source_rows(connection, session_uid, lap_number, length[0] if length else None))
        chunks = []
        current = []
        for row in rows:
            if not quality.supports(row["distance_m"]):
                if current:
                    chunks.append(current)
                current = []
                continue
            if current and not quality.connects(current[-1]["distance_m"], row["distance_m"]):
                chunks.append(current)
                current = []
            current.append(row)
        if current:
            chunks.append(current)
        rows = [row for chunk in chunks for row in chunk]
        if not rows:
            continue
        braking = [event for chunk in chunks for event in _braking_events(chunk)]
        throttle = [event for chunk in chunks for event in _throttle_events(chunk)]
        shifts = [event for chunk in chunks for event in _gear_shift_events(chunk)]
        _insert_events(connection, session_uid, lap_number, braking, throttle, shifts)

        sample_count = len(rows)
        percent = lambda count: 100.0 * count / sample_count
        speeds = [float(row["speed_kph"]) for row in rows if row["speed_kph"] is not None]
        distance_span = max(1.0, sum(chunk[-1]["distance_m"] - chunk[0]["distance_m"] for chunk in chunks))
        distance_km = distance_span / 1000.0
        peak_brakes = [event["peak_brake"] for event in braking]
        smoothness = [event["release_smoothness"] for event in braking]
        upshifts = sum(event["direction"] == "up" for event in shifts)
        downshifts = len(shifts) - upshifts
        connection.execute(
            """
            INSERT INTO lap_metrics VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                session_uid, lap_number, lap_time_ms,
                lap_time_ms - best_times.get(session_uid, lap_time_ms),
                percent(sum(
                    _number(row["throttle"]) >= EVENT_THRESHOLDS["full_throttle"]
                    for row in rows
                )),
                percent(sum(
                    _number(row["brake"]) >= EVENT_THRESHOLDS["brake_start"]
                    for row in rows
                )),
                percent(sum(
                    _number(row["throttle"]) <= EVENT_THRESHOLDS["coast_throttle_max"]
                    and _number(row["brake"]) <= EVENT_THRESHOLDS["brake_end"]
                    and _number(row["speed_kph"]) > 20.0
                    for row in rows
                )),
                percent(sum(
                    _number(row["throttle"]) > EVENT_THRESHOLDS["coast_throttle_max"]
                    and _number(row["brake"]) >= EVENT_THRESHOLDS["brake_start"]
                    for row in rows
                )),
                max(speeds) if speeds else None, min(speeds) if speeds else None,
                sum(speeds) / len(speeds) if speeds else None,
                len(braking), len(throttle), upshifts, downshifts,
                sum(_steering_corrections(chunk) for chunk in chunks),
                sum(_total_variation(row["steer"] for row in chunk) for chunk in chunks) / distance_km,
                sum(_total_variation(row["throttle"] for row in chunk) for chunk in chunks) / distance_km,
                sum(peak_brakes) / len(peak_brakes) if peak_brakes else None,
                sum(smoothness) / len(smoothness) if smoothness else None,
            ),
        )
        counts["braking_event_count"] += len(braking)
        counts["throttle_event_count"] += len(throttle)
        counts["gear_shift_event_count"] += len(shifts)
        counts["lap_metric_count"] += 1

    connection.execute("DELETE FROM session_metrics")
    _session_metrics(connection)
    # A newly completed best lap also changes older laps' deltas, not their events.
    for uid, best_time in best_times.items():
        connection.execute("UPDATE lap_metrics SET delta_to_best_ms=lap_time_ms-? WHERE session_uid=?", (best_time, uid))
    counts["session_metric_count"] = len(
        connection.execute("SELECT session_uid FROM session_metrics").fetchall()
    )
    return dict(counts)
