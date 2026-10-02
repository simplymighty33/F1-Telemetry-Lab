"""Distance-based gain/loss attribution and observed operation summaries.

Smoothing selects boundaries only. Every amount uses the unsmoothed delta,
and every supported interval is retained, including small changes. Missing
coverage is an explicit residual, never distributed over observed regions.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from analysis.comparison import TracePoint


@dataclass(frozen=True)
class OperationSummary:
    entry_speed_kph: float
    minimum_speed_kph: float
    exit_speed_kph: float
    brake_start_m: float | None
    full_throttle_m: float | None
    minimum_gear: int | None


@dataclass(frozen=True)
class TimeRegion:
    start_m: float
    end_m: float
    delta_ms: float
    kind: str
    reference: OperationSummary
    comparison: OperationSummary


def _operations(points, prefix: str) -> OperationSummary:
    speeds = [getattr(p, prefix + "_speed_kph") for p in points]
    minimum = min(range(len(points)), key=lambda i: speeds[i])
    brake_start = next((p.distance_m for before, p in zip(points, points[1:])
                        if getattr(before, prefix + "_brake") < 0.05 <= getattr(p, prefix + "_brake")), None)
    # Only a crossing AFTER the minimum, within this same shared region.
    full_throttle = next((p.distance_m for before, p in zip(points[minimum:], points[minimum + 1:])
                          if getattr(before, prefix + "_throttle") < 0.98 <= getattr(p, prefix + "_throttle")), None)
    gears = [getattr(p, prefix + "_gear") for p in points]
    gears = [g for g in gears if g is not None and g > 0]
    return OperationSummary(speeds[0], speeds[minimum], speeds[-1], brake_start,
                            full_throttle, min(gears) if gears else None)


def supported_runs(trace) -> list[list]:
    runs, current = [], []
    for point in trace:
        if not point.valid or (current and not point.connected):
            if len(current) >= 2:
                runs.append(current)
            current = []
        if point.valid:
            current.append(point)
    if len(current) >= 2:
        runs.append(current)
    return runs


def time_regions(trace, smoothing_m: float = 50.0) -> tuple[TimeRegion, ...]:
    regions = []
    for points in supported_runs(trace):
        # Distance-window mean, linear time; independent of grid interval.
        prefix = [0.0]
        for point in points:
            prefix.append(prefix[-1] + point.delta_ms)
        left = right = 0
        smoothed = []
        for point in points:
            while points[left].distance_m < point.distance_m - smoothing_m / 2:
                left += 1
            while right < len(points) and points[right].distance_m <= point.distance_m + smoothing_m / 2:
                right += 1
            smoothed.append((prefix[right] - prefix[left]) / (right - left))
        boundaries = [0]
        previous_sign = 0
        for index in range(1, len(points)):
            change = smoothed[index] - smoothed[index - 1]
            sign = 1 if change > 1e-6 else -1 if change < -1e-6 else previous_sign
            if previous_sign and sign and sign != previous_sign:
                boundary = index - 1
                if boundary > boundaries[-1]:
                    boundaries.append(boundary)
            if sign:
                previous_sign = sign
        if boundaries[-1] != len(points) - 1:
            boundaries.append(len(points) - 1)
        # Merge insignificant fragments rather than discarding their amounts.
        merged = [0]
        for boundary in boundaries[1:-1]:
            if (points[boundary].distance_m - points[merged[-1]].distance_m >= 30
                    and abs(points[boundary].delta_ms - points[merged[-1]].delta_ms) >= 20):
                merged.append(boundary)
        last = len(points) - 1
        if len(merged) > 1 and (points[last].distance_m - points[merged[-1]].distance_m < 30
                               or abs(points[last].delta_ms - points[merged[-1]].delta_ms) < 20):
            merged.pop()
        boundaries = merged + [last]
        for start, end in zip(boundaries, boundaries[1:]):
            selected = points[start:end + 1]
            amount = selected[-1].delta_ms - selected[0].delta_ms
            length = selected[-1].distance_m - selected[0].distance_m
            kind = "小变化" if abs(amount) < 20 else "损失" if amount > 0 else "获益"
            regions.append(TimeRegion(selected[0].distance_m, selected[-1].distance_m, amount, kind,
                                      _operations(selected, "reference"), _operations(selected, "comparison")))
    return tuple(regions)
