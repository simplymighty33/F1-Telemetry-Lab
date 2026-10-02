"""Conservative source-sample checks shared by normalization and comparison.

These thresholds detect large holes, not UDP loss rates or a guarantee that
every packet arrived. No third-party implementation is embedded here.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
import math
from statistics import median

MAX_DISTANCE_GAP_M = 50.0
MAX_TIME_GAP_MS = 1500.0
SMALL_CLOCK_JITTER_MS = 50.0
QUALITY_VERSION = 1
REQUIRED_CHANNELS = ("current_lap_time_ms", "speed_kph", "throttle", "brake")


def finite(value) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value)


def usable(row: dict) -> bool:
    if not all(finite(row.get(key)) for key in REQUIRED_CHANNELS):
        return False
    return (row["current_lap_time_ms"] >= 0 and row["speed_kph"] >= 0
            and -1e-6 <= row["throttle"] <= 1.000001
            and -1e-6 <= row["brake"] <= 1.000001)


@dataclass(frozen=True)
class SourceQuality:
    distances: tuple[float, ...]
    times: tuple[float | None, ...]
    bad_points: frozenset[float]
    bad_intervals: tuple[tuple[float, float], ...]
    max_distance_gap_m: float
    max_time_gap_ms: float
    status: str
    issues: tuple[dict, ...] = ()
    cadence_ms: float | None = None

    def time_at(self, distance: float) -> float | None:
        if not self.supports(distance):
            return None
        index = bisect_left(self.distances, distance)
        if self.distances[index] == distance:
            return self.times[index]
        left, right = index - 1, index
        if not finite(self.times[left]) or not finite(self.times[right]):
            return None
        ratio = (distance - self.distances[left]) / (self.distances[right] - self.distances[left])
        return self.times[left] + ratio * (self.times[right] - self.times[left])

    def supports(self, distance: float) -> bool:
        if not self.distances or not self.distances[0] <= distance <= self.distances[-1]:
            return False
        if distance in self.bad_points:
            return False
        index = bisect_left(self.distances, distance)
        if index < len(self.distances) and self.distances[index] == distance:
            return True
        return self.connects(distance, distance)

    def connects(self, start: float, end: float) -> bool:
        if not self.distances or start < self.distances[0] or end > self.distances[-1]:
            return False
        # Inclusive point query; positive-length queries reject any overlap.
        for left, right in self.bad_intervals:
            if start == end:
                if left < start < right:
                    return False
            elif left < end and right > start:
                return False
        index = bisect_right(self.distances, start)
        return not any(d in self.bad_points for d in self.distances[index:bisect_left(self.distances, end)])


def inspect_source(rows: list[dict]) -> SourceQuality:
    distances = tuple(float(row["lap_distance_m"]) for row in rows)
    bad_points = frozenset(d for d, row in zip(distances, rows) if not usable(row))
    intervals = []
    maximum_distance = maximum_time = 0.0
    rollback = False
    deltas = []
    for left, right in zip(rows, rows[1:]):
        a, b = left.get("session_time"), right.get("session_time")
        dt = (b - a) * 1000 if finite(a) and finite(b) else None
        driving = all(r.get("pit_status", 0) == 0 and r.get("driver_status", 4) in (3, 4)
                      for r in (left, right))
        deltas.append(dt if driving and dt is not None and 0 < dt <= 250 else None)
    cadence = median([dt for dt in deltas if dt is not None]) if any(dt is not None for dt in deltas) else None
    issues = []
    hard_gap = False
    for index, (left, right) in enumerate(zip(rows, rows[1:])):
        start, end = float(left["lap_distance_m"]), float(right["lap_distance_m"])
        span = end - start
        before, after = left.get("current_lap_time_ms"), right.get("current_lap_time_ms")
        elapsed = after - before if finite(before) and finite(after) else 0.0
        maximum_distance = max(maximum_distance, span)
        maximum_time = max(maximum_time, elapsed)
        rollback |= elapsed < 0 or span <= 0
        hard = (span > MAX_DISTANCE_GAP_M or elapsed > MAX_TIME_GAP_MS or elapsed < 0
                or not usable(left) or not usable(right))
        if hard:
            intervals.append((start, end))
            hard_gap = True
        # Local game-clock cadence, never wall-clock silence or a packet-loss percentage.
        nearby = [dt for dt in deltas[max(0, index - 12):index + 13] if dt is not None]
        local = median(nearby) if len(nearby) >= 8 else None
        a, b = left.get("session_time"), right.get("session_time")
        game_dt = (b - a) * 1000 if finite(a) and finite(b) else None
        same_context = (left.get("pit_status", 0) == right.get("pit_status", 0) == 0
                        and left.get("driver_status", 4) == right.get("driver_status", 4)
                        and left.get("driver_status", 4) in (3, 4))
        suspect = (not hard and same_context and local is not None and game_dt is not None
                   and game_dt > max(120.0, local * 3.5) and span > 1.0)
        incomplete = bool(right.get("_incomplete_before"))
        if suspect or (incomplete and not hard):
            intervals.append((start, end))
        if hard or suspect or incomplete:
            kind = ("missing_channels" if not usable(left) or not usable(right) else
                    "clock_rollback" if elapsed < 0 else "large_gap" if hard else
                    "incomplete_frame" if incomplete else "cadence_gap")
            issues.append(dict(kind=kind, start_m=start, end_m=end, elapsed_ms=elapsed,
                               game_elapsed_ms=game_dt, baseline_ms=local,
                               start_frame=left.get("overall_frame_identifier"),
                               end_frame=right.get("overall_frame_identifier")))
    status = ("insufficient_samples" if len(rows) < 2 else
              "missing_channels" if bad_points else
              "nonmonotonic_time" if rollback else
              "sample_gap" if hard_gap else "ready")
    return SourceQuality(distances, tuple(row.get("current_lap_time_ms") for row in rows), bad_points,
                         tuple(intervals), maximum_distance, maximum_time, status, tuple(issues), cadence)
