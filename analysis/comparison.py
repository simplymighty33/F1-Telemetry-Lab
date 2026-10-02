"""Read-only lap comparison model used by the desktop analysis viewer."""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass, replace
from pathlib import Path
import sqlite3
import json
from typing import Any
from decoder.tyres import LapTyreInfo
from analysis.quality import finite, inspect_source
from analysis.resample import _source_rows, _interpolate
from analysis.regions import TimeRegion, time_regions


REQUIRED_TABLES = {
    "sessions", "laps", "lap_metrics", "resampled_lap_samples",
    "braking_events", "session_metrics",
}


@dataclass(frozen=True)
class SessionChoice:
    session_uid: str
    game_mode: str
    session_type: str
    track: str
    lap_count: int

    @property
    def label(self) -> str:
        details = " · ".join(value for value in (self.game_mode, self.session_type, self.track) if value)
        return f"{details or '未知会话'} · {self.lap_count} 圈"


@dataclass(frozen=True)
class LapSummary:
    session_uid: str
    lap_number: int
    lap_time_ms: int
    sector1_ms: int
    sector2_ms: int
    sector3_ms: int
    delta_to_best_ms: int
    full_throttle_percent: float
    braking_percent: float
    coasting_percent: float
    overlap_percent: float
    maximum_speed_kph: float | None
    braking_event_count: int
    throttle_event_count: int
    upshift_count: int
    downshift_count: int
    steering_correction_count: int
    steering_variation_per_km: float
    tyre: LapTyreInfo = LapTyreInfo()


@dataclass(frozen=True)
class TracePoint:
    distance_m: float
    reference_time_ms: float
    comparison_time_ms: float
    delta_ms: float
    reference_speed_kph: float
    comparison_speed_kph: float
    reference_throttle: float
    comparison_throttle: float
    reference_brake: float
    comparison_brake: float
    reference_gear: int | None = None
    comparison_gear: int | None = None
    valid: bool = True
    connected: bool = True


@dataclass(frozen=True)
class BrakingMarker:
    lap_number: int
    start_distance_m: float
    end_distance_m: float
    entry_speed_kph: float | None
    minimum_speed_kph: float | None
    peak_brake: float


@dataclass(frozen=True)
class LapComparison:
    reference: LapSummary
    comparison: LapSummary
    trace: tuple[TracePoint, ...]
    reference_braking: tuple[BrakingMarker, ...]
    comparison_braking: tuple[BrakingMarker, ...]
    regions: tuple[TimeRegion, ...] = ()
    observed_delta_ms: float = 0.0
    unattributed_delta_ms: float = 0.0
    warnings: tuple[str, ...] = ()
    residual_components: tuple[tuple[str, float], ...] = ()


class AnalysisDatabaseError(ValueError):
    """Raised when a file is not a compatible Phase 2 analysis database."""


class AnalysisRepository:
    def __init__(self, database: Path) -> None:
        self.database = database.expanduser().resolve()
        if not self.database.is_file():
            raise AnalysisDatabaseError(f"分析数据库不存在：{self.database}")
        with closing(self._connect()) as connection:
            tables = {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            self.has_tyre_metadata = "lap_tyres" in tables
            self.has_segments = "driving_segments" in tables
            self.has_source_samples = "telemetry_samples" in tables
            self.has_quality_details = "lap_quality_details" in tables
        missing = REQUIRED_TABLES - tables
        if missing:
            raise AnalysisDatabaseError(
                "不是兼容的分析数据库，缺少：" + ", ".join(sorted(missing))
            )

    def _connect(self) -> sqlite3.Connection:
        uri = self.database.as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        return connection

    def sessions(self) -> tuple[SessionChoice, ...]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT s.session_uid, COALESCE(s.game_mode, '') AS game_mode,
                       COALESCE(s.session_type, '') AS session_type,
                       COALESCE(s.track, '') AS track, COUNT(m.lap_number) AS lap_count
                FROM sessions AS s
                LEFT JOIN lap_metrics AS m ON m.session_uid = s.session_uid
                GROUP BY s.session_uid
                ORDER BY s.first_received_at_ns, s.session_uid
                """
            ).fetchall()
        return tuple(SessionChoice(**dict(row)) for row in rows)

    def segments(self, session_uid: str) -> tuple[dict, ...]:
        if not self.has_segments:
            return ()
        with closing(self._connect()) as con:
            return tuple(dict(row) for row in con.execute(
                "SELECT g.*,COUNT(l.lap_number) AS lap_count FROM driving_segments g "
                "LEFT JOIN lap_segments l ON l.segment_id=g.id "
                "WHERE g.session_uid=? AND g.superseded=0 GROUP BY g.id ORDER BY g.start_offset DESC", (session_uid,)))

    def quality(self, session_uid: str, segment_id: int | None = None) -> tuple[dict, ...]:
        with closing(self._connect()) as con:
            query = "SELECT l.lap_number,l.lap_time_ms,a.coverage_ratio,COALESCE(a.quality_status,'pending') AS quality_status "
            query += "FROM laps l LEFT JOIN lap_analysis a USING(session_uid,lap_number) WHERE l.session_uid=?"
            args = [session_uid]
            if segment_id is not None and self.has_segments:
                query += " AND EXISTS(SELECT 1 FROM lap_segments g WHERE g.session_uid=l.session_uid AND g.lap_number=l.lap_number AND g.segment_id=?)"
                args.append(segment_id)
            rows = [dict(row) for row in con.execute(query + " ORDER BY l.lap_number DESC", args)]
            details = {number: json.loads(value) for number, value in con.execute(
                "SELECT lap_number,details_json FROM lap_quality_details WHERE session_uid=?", (session_uid,))} if self.has_quality_details else {}
            for row in rows:
                row["details"] = details.get(row["lap_number"])
            return tuple(rows)

    def laps(self, session_uid: str) -> tuple[LapSummary, ...]:
        with closing(self._connect()) as connection:
            return self._laps(connection, session_uid)

    def _laps(self, connection: sqlite3.Connection, session_uid: str) -> tuple[LapSummary, ...]:
        tyres = {}
        if self.has_tyre_metadata:
            for row in connection.execute("SELECT * FROM lap_tyres WHERE session_uid=?", (session_uid,)):
                values = dict(row)
                number = values.pop("lap_number")
                values.pop("session_uid")
                tyres[number] = LapTyreInfo(**values)
        rows = connection.execute(
                """
                SELECT l.session_uid, l.lap_number, l.lap_time_ms,
                       l.sector1_ms, l.sector2_ms, l.sector3_ms,
                       m.delta_to_best_ms, m.full_throttle_percent,
                       m.braking_percent, m.coasting_percent,
                       m.brake_throttle_overlap_percent AS overlap_percent,
                       m.maximum_speed_kph, m.braking_event_count,
                       m.throttle_event_count, m.upshift_count,
                       m.downshift_count, m.steering_correction_count,
                       m.steering_total_variation_per_km AS steering_variation_per_km
                FROM laps AS l
                JOIN lap_metrics AS m
                  ON m.session_uid = l.session_uid AND m.lap_number = l.lap_number
                WHERE l.session_uid = ?
                ORDER BY l.lap_number
                """,
                (session_uid,),
            ).fetchall()
        return tuple(LapSummary(**dict(row), tyre=tyres.get(row["lap_number"], LapTyreInfo())) for row in rows)

    def comparison(
        self,
        session_uid: str,
        reference_lap: int,
        comparison_lap: int,
    ) -> LapComparison:
        with closing(self._connect()) as connection:
            # Lap times, final branch and traces must come from one WAL snapshot.
            connection.execute("BEGIN")
            laps = {lap.lap_number: lap for lap in self._laps(connection, session_uid)}
            try:
                reference = laps[reference_lap]
                comparison = laps[comparison_lap]
            except KeyError as exc:
                raise AnalysisDatabaseError(f"找不到第 {exc.args[0]} 圈的分析数据") from exc
            rows = connection.execute(
                """
                SELECT a.distance_m,
                       a.lap_time_ms AS reference_time_ms,
                       b.lap_time_ms AS comparison_time_ms,
                       b.lap_time_ms - a.lap_time_ms AS delta_ms,
                       a.speed_kph AS reference_speed_kph,
                       b.speed_kph AS comparison_speed_kph,
                       a.throttle AS reference_throttle,
                       b.throttle AS comparison_throttle,
                       a.brake AS reference_brake,
                       b.brake AS comparison_brake,
                       a.gear AS reference_gear, b.gear AS comparison_gear
                FROM resampled_lap_samples AS a
                JOIN resampled_lap_samples AS b
                  ON b.session_uid = a.session_uid AND b.distance_m = a.distance_m
                WHERE a.session_uid = ? AND a.lap_number = ? AND b.lap_number = ?
                ORDER BY a.distance_m
                """,
                (session_uid, reference_lap, comparison_lap),
            ).fetchall()
            markers = connection.execute(
                """
                SELECT lap_number, start_distance_m, end_distance_m,
                       entry_speed_kph, minimum_speed_kph, peak_brake
                FROM braking_events
                WHERE session_uid = ? AND lap_number IN (?, ?)
                ORDER BY lap_number, event_index
                """,
                (session_uid, reference_lap, comparison_lap),
            ).fetchall()
            session = connection.execute("SELECT track_length_m FROM sessions WHERE session_uid=?", (session_uid,)).fetchone()
            length = session[0] if session else None
            source_rows = [_source_rows(connection, session_uid, number, length) if self.has_source_samples else []
                           for number in (reference_lap, comparison_lap)]
            sources = [inspect_source(items) for items in source_rows]
            steps = connection.execute("SELECT distance_step_m FROM lap_analysis WHERE session_uid=? AND lap_number IN (?,?)",
                                       (session_uid, reference_lap, comparison_lap)).fetchall()
        rows = [dict(row) for row in rows if not length or 0 <= row["distance_m"] <= length]
        if all(source.distances for source in sources):
            start = max(source.distances[0] for source in sources)
            end = min(source.distances[-1] for source in sources)
            if length:
                end = min(end, length)
            # Add non-grid common endpoints and reliable edges of holes, without extrapolation.
            targets = {start, end}
            targets.update(edge for source in sources for interval in source.bad_intervals for edge in interval)
            existing = {row["distance_m"] for row in rows}
            for distance in sorted(targets - existing):
                if not start <= distance <= end or not all(source.supports(distance) for source in sources):
                    continue
                values = [_interpolate(items, list(source.distances), distance)
                          for items, source in zip(source_rows, sources)]
                a, b = values
                rows.append(dict(distance_m=distance, reference_time_ms=a["current_lap_time_ms"],
                    comparison_time_ms=b["current_lap_time_ms"], delta_ms=b["current_lap_time_ms"] - a["current_lap_time_ms"],
                    reference_speed_kph=a["speed_kph"], comparison_speed_kph=b["speed_kph"],
                    reference_throttle=a["throttle"], comparison_throttle=b["throttle"],
                    reference_brake=a["brake"], comparison_brake=b["brake"],
                    reference_gear=a["gear"], comparison_gear=b["gear"]))
            rows.sort(key=lambda row: row["distance_m"])
        if not rows:
            raise AnalysisDatabaseError("两圈没有共同的距离采样点，无法比较")
        step = min((r[0] for r in steps if finite(r[0]) and r[0] > 0), default=5.0)
        trace = []
        previous = None
        for row in rows:
            point = TracePoint(**dict(row))
            valid = all(finite(row[key]) for key in ("delta_ms", "reference_speed_kph", "comparison_speed_kph",
                                                    "reference_throttle", "comparison_throttle", "reference_brake", "comparison_brake"))
            valid = valid and all(source.supports(point.distance_m) for source in sources)
            if valid:
                reference_time, comparison_time = (source.time_at(point.distance_m) for source in sources)
                if finite(reference_time) and finite(comparison_time):
                    point = replace(point, reference_time_ms=reference_time, comparison_time_ms=comparison_time,
                                    delta_ms=comparison_time - reference_time)
                else:
                    valid = False
            connected = previous is None or (point.distance_m - previous <= step * 1.5
                        and all(source.connects(previous, point.distance_m) for source in sources))
            trace.append(replace(point, valid=valid, connected=connected))
            previous = point.distance_m
        regions = time_regions(trace)
        observed = sum(region.delta_ms for region in regions)
        official = comparison.lap_time_ms - reference.lap_time_ms
        warnings = []
        for name, source in zip(("基准圈", "对比圈"), sources):
            if source.status != "ready":
                warnings.append(f"{name}源采样质量：" + {
                    "insufficient_samples": "无法核验（源采样不足）", "sample_gap": "存在大间隙",
                    "missing_channels": "关键字段缺失或异常", "nonmonotonic_time": "圈内时间不连续",
                }.get(source.status, source.status))
            if any(issue["kind"] == "cadence_gap" for issue in source.issues):
                warnings.append(f"{name}存在相对采样节奏异常的间隔；不是已确认的UDP丢包")
        if not all(p.valid and p.connected for p in trace):
            warnings.append("不可靠区间已断开，未参与时间损失及操作分析")
        if trace[0].distance_m > 0 or (length and trace[-1].distance_m < length):
            warnings.append("起终点未完整采样；官方圈速与已观察区间的差额单独列出")
        if reference.tyre.actual_compound != comparison.tyre.actual_compound:
            warnings.append("两圈轮胎配方不同，不能将全部时间差归因于驾驶操作")
        if reference.tyre.wear_percent is not None and comparison.tyre.wear_percent is not None:
            if abs(reference.tyre.wear_percent - comparison.tyre.wear_percent) >= 5:
                warnings.append("两圈轮胎磨损相差至少5个百分点，比较条件不同")
        braking = tuple(BrakingMarker(**dict(row)) for row in markers)
        components = []
        valid_points = [p for p in trace if p.valid]
        if valid_points:
            first, last = valid_points[0], valid_points[-1]
            components.append(("圈首未观察部分 / 起始计时偏差", first.delta_ms))
            components.append(("内部不可靠区间净差（不作驾驶归因）",
                               last.delta_ms - first.delta_ms - observed))
            components.append(("圈尾未观察部分 / 终点计时偏差", official - last.delta_ms))
        else:
            components.append(("缺少可靠采样，待核验", official - observed))
        return LapComparison(
            reference=reference,
            comparison=comparison,
            trace=tuple(trace),
            reference_braking=tuple(item for item in braking if item.lap_number == reference_lap),
            comparison_braking=tuple(item for item in braking if item.lap_number == comparison_lap),
            regions=regions, observed_delta_ms=observed, unattributed_delta_ms=official - observed,
            warnings=tuple(warnings),
            residual_components=tuple(components),
        )


def find_latest_analysis_database(data_directory: Path) -> Path | None:
    candidates = list(data_directory.glob("session_*/analysis/telemetry_analysis.db"))
    return max(candidates, key=lambda path: path.stat().st_mtime_ns) if candidates else None
