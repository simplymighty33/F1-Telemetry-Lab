"""Read-only lap comparison model used by the desktop analysis viewer."""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
import sqlite3
from typing import Any
from decoder.tyres import LapTyreInfo


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
            return tuple(dict(row) for row in con.execute(query + " ORDER BY l.lap_number DESC", args))

    def laps(self, session_uid: str) -> tuple[LapSummary, ...]:
        with closing(self._connect()) as connection:
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
        laps = {lap.lap_number: lap for lap in self.laps(session_uid)}
        try:
            reference = laps[reference_lap]
            comparison = laps[comparison_lap]
        except KeyError as exc:
            raise AnalysisDatabaseError(f"找不到第 {exc.args[0]} 圈的分析数据") from exc
        with closing(self._connect()) as connection:
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
                       b.brake AS comparison_brake
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
        if not rows:
            raise AnalysisDatabaseError("两圈没有共同的距离采样点，无法比较")
        braking = tuple(BrakingMarker(**dict(row)) for row in markers)
        return LapComparison(
            reference=reference,
            comparison=comparison,
            trace=tuple(TracePoint(**dict(row)) for row in rows),
            reference_braking=tuple(item for item in braking if item.lap_number == reference_lap),
            comparison_braking=tuple(item for item in braking if item.lap_number == comparison_lap),
        )


def find_latest_analysis_database(data_directory: Path) -> Path | None:
    candidates = list(data_directory.glob("session_*/analysis/telemetry_analysis.db"))
    return max(candidates, key=lambda path: path.stat().st_mtime_ns) if candidates else None
