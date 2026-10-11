"""Display-only adapters. Never interpolate, smooth or reconnect missing samples."""
from pathlib import Path
import math
from bisect import bisect_left

import numpy as np


def channel_data(trace, field):
    factor = .001 if field == 'delta_ms' else 100 if field.endswith(('throttle', 'brake')) else 1
    x = np.asarray([p.distance_m / 1000 for p in trace], dtype=float)
    y = np.asarray([float(getattr(p, field)) * factor if getattr(p, field) is not None else np.nan
                    for p in trace], dtype=float)
    valid = np.asarray([p.valid for p in trace], dtype=bool) & np.isfinite(x) & np.isfinite(y)
    y[~valid] = np.nan
    connected = np.zeros(len(trace), dtype=bool)
    if len(trace) > 1:
        connected[:-1] = valid[:-1] & valid[1:] & np.asarray([p.connected for p in trace[1:]])
    return x, y, connected


def nearest_sample(trace, distance):
    finite = [p for p in trace if math.isfinite(p.distance_m)]
    if not finite or not math.isfinite(distance):
        return None
    point = min(finite, key=lambda p: abs(p.distance_m - distance))
    return point if point.valid and point.connected else None


class SampleIndex:
    """Bounded original-order index; unsorted/nonfinite distances keep the oracle.

    This accelerates readout only: no sorting, point dropping or chart resampling.
    Duplicate-distance ties return the first original point, like min().
    """
    def __init__(self, trace):
        self.trace = trace
        self.distances = tuple(p.distance_m for p in trace)
        self.monotonic = all(math.isfinite(d) for d in self.distances) and all(
            a <= b for a, b in zip(self.distances, self.distances[1:]))

    def nearest(self, distance):
        if not self.monotonic:
            return nearest_sample(self.trace, distance)
        if not self.distances or not math.isfinite(distance):
            return None
        right = bisect_left(self.distances, distance)
        candidates = [i for i in (right - 1, right) if 0 <= i < len(self.trace)]
        chosen = min(candidates, key=lambda i: (abs(self.distances[i] - distance), i))
        chosen = bisect_left(self.distances, self.distances[chosen])
        point = self.trace[chosen]
        return point if point.valid and point.connected else None


def source_revision(database, summary):
    revision = summary.get('analysis_revision')
    if revision is None:
        revision = tuple(summary.get(k) for k in ('final_lap_count', 'laps_resampled', 'flashback_count'))
    # A serialized revision is hashable even when older metadata used nested objects.
    return str(Path(database).resolve()), repr(summary.get('source_identity')), repr(revision)


def lap_time(value):
    if value is None or not math.isfinite(value) or value <= 0:
        return '—'
    ms = round(value)
    minutes, remainder = divmod(ms, 60_000)
    return f'{minutes}:{remainder / 1000:06.3f}'


def number(value, suffix='', precision=1):
    return '—' if value is None or not math.isfinite(value) else f'{value:.{precision}f}{suffix}'


QUALITY_LABELS = dict(ready='可分析', invalid_lap='游戏判定无效', partial_capture='覆盖不足',
                      insufficient_samples='样本不足', unverified_attempt='圈段计时待核验', pending='待完成', sample_gap='存在缺口',
                      missing_channels='缺少通道', nonmonotonic_time='时序异常')
