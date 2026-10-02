"""Bounded, read-only practice summaries and descriptive multi-lap evidence.

No Raw replay, coaching, fuel correction, statistical confidence score or map.
All fields in a report come from one committed SQLite snapshot.
"""
from __future__ import annotations

from collections import Counter
from contextlib import closing
from statistics import median, pstdev
import math

from analysis.quality import finite, inspect_source
from analysis.resample import _source_rows
from decoder.display import session_label

MAX_REPORT_LAPS = 120
MAX_CANDIDATES = 12
BIN_M = 100.0
LOSS_MS = 20.0
CONDITION_FIELDS = ("fuel_in_tank", "ers_store_energy", "ers_deploy_mode",
                    "tyre_inner_temp_rl", "tyre_inner_temp_rr", "tyre_inner_temp_fl", "tyre_inner_temp_fr")
REASONS = {"invalid_lap": "游戏判定无效", "partial_capture": "覆盖不足",
           "insufficient_samples": "样本不足", "pending": "尚未完成分析",
           "sample_gap": "大间隙", "missing_channels": "关键字段异常",
           "nonmonotonic_time": "圈内时序异常"}


def _statistics(values):
    values = [v for v in values if finite(v) and v > 0]
    return {"count": len(values), "best_ms": min(values) if values else None,
            "median_ms": median(values) if values else None,
            "spread_ms": max(values) - min(values) if values else None,
            "stddev_ms": pstdev(values) if len(values) >= 2 else None}


def _conditions(con, uid, number, source_rows, length, has_evidence):
    columns = {r[1] for r in con.execute("PRAGMA table_info(telemetry_samples)")}
    available = [name for name in CONDITION_FIELDS if name in columns]
    result = {"fuel_start_kg": None, "fuel_end_kg": None, "fuel_start_m": None,
              "fuel_end_m": None, "ers_median_mj": None, "ers_modes": [],
              "tyre_inner_median_c": None, "status_verified_samples": 0,
              "temperature_verified_samples": 0, "source_samples": len(source_rows),
              "note": "只使用同帧同玩家来源；缓存来源/缺失数值不作已核验条件"}
    if not source_rows or not available:
        return result
    evidence = "LEFT JOIN condition_frames c USING(session_uid,overall_frame_identifier)" if has_evidence else ""
    flags = "c.status_present,c.telemetry_present" if has_evidence else "NULL,NULL"
    data = {r[0]: dict(zip((*available, "status_present", "telemetry_present"), r[1:])) for r in con.execute(
        f"SELECT t.overall_frame_identifier,{','.join('t.'+f for f in available)},{flags} "
        f"FROM telemetry_samples t {evidence} WHERE t.session_uid=? AND t.lap_number=? AND t.superseded=0",
        (uid, number))}
    fuels, energies, modes, temperatures = [], [], set(), []
    quality = inspect_source(source_rows)
    for row in source_rows:
        d = row["lap_distance_m"]
        if not quality.supports(d):
            continue
        fields = data.get(row["overall_frame_identifier"], {})
        if fields.get("status_present") == 1:
            result["status_verified_samples"] += 1
            fuel, energy, mode = (fields.get(f) for f in CONDITION_FIELDS[:3])
            if finite(fuel) and fuel >= 0:
                fuels.append((d, fuel))
            if finite(energy) and energy >= 0:
                energies.append(energy / 1_000_000)
            if isinstance(mode, int) and 0 <= mode <= 3:
                modes.add(mode)
        if fields.get("telemetry_present") == 1:
            wheel = [fields.get(f) for f in CONDITION_FIELDS[3:]]
            if all(finite(v) and 0 < v <= 300 for v in wheel):
                result["temperature_verified_samples"] += 1
                temperatures.append(sum(wheel) / 4)
    if fuels and fuels[0][0] <= 200:
        result["fuel_start_m"], result["fuel_start_kg"] = fuels[0]
    if fuels and finite(length) and length - 200 <= fuels[-1][0] <= length:
        result["fuel_end_m"], result["fuel_end_kg"] = fuels[-1]
    result["ers_median_mj"] = median(energies) if energies else None
    result["ers_modes"] = sorted(modes)
    result["tyre_inner_median_c"] = median(temperatures) if temperatures else None
    return result


def condition_warnings(reference, comparison):
    """Informational thresholds, not eligibility gates or causal diagnoses."""
    warnings = []
    if reference["compound"] != comparison["compound"]:
        warnings.append("轮胎配方不同")
    a, b = reference["wear_percent"], comparison["wear_percent"]
    if finite(a) and finite(b) and abs(a - b) >= 5:
        warnings.append("磨损相差至少5个百分点")
    ca, cb = reference["conditions"], comparison["conditions"]
    for field, threshold, name in (("fuel_start_kg", 5, "起始采样燃油相差至少5 kg"),
                                   ("tyre_inner_median_c", 10, "四轮平均内温的中位值相差至少10°C"),
                                   ("ers_median_mj", .5, "已验证ERS储能中位值相差至少0.5 MJ")):
        a, b = ca[field], cb[field]
        if not finite(a) or not finite(b):
            warnings.append({"fuel_start_kg": "燃油条件未知/来源未核验",
                             "tyre_inner_median_c": "胎温条件未知/来源未核验",
                             "ers_median_mj": "ERS条件未知/来源未核验"}[field])
        elif abs(a - b) >= threshold:
            warnings.append(name)
    if ca["ers_modes"] and cb["ers_modes"] and ca["ers_modes"] != cb["ers_modes"]:
        warnings.append("观察到的ERS模式集合不同")
    warnings.append("天气、交通、辅助与设置尚未完整核验；不自动校正圈速或解释因果")
    return warnings


def repeatability(sources, reference, candidates, length, cancelled=lambda: False):
    """Fixed 100 m bins; use only fully supported endpoints AND interior.

    Repeated = >=2 losses and >=2/3 of supported candidates, with >=2/3
    candidate coverage. 20 ms is an explicit display threshold, not significance.
    """
    result = []
    if reference not in sources or not finite(length) or not 0 < length <= 20000:
        return result
    base = sources[reference]
    for index in range(math.ceil(length / BIN_M)):
        if cancelled():
            raise InterruptedError("复盘已取消")
        start, end = index * BIN_M, min(length, (index + 1) * BIN_M)
        if not (base.supports(start) and base.supports(end) and base.connects(start, end)):
            continue
        baseline = base.time_at(end) - base.time_at(start)
        values = []
        for number in candidates:
            source = sources[number]
            if source.supports(start) and source.supports(end) and source.connects(start, end):
                values.append((number, source.time_at(end) - source.time_at(start) - baseline))
        losses = [number for number, value in values if value > LOSS_MS]
        gains = [number for number, value in values if value < -LOSS_MS]
        n = len(values)
        result.append({"start_m": start, "end_m": end, "supported": n,
                       "candidate_count": len(candidates), "loss_count": len(losses), "gain_count": len(gains),
                       "loss_laps": losses, "median_delta_ms": median([v for _, v in values]) if values else None,
                       "repeated_loss": len(losses) >= 2 and len(losses) * 3 >= n * 2
                           and n * 3 >= len(candidates) * 2})
    return result


def practice_report(repository, uid, segment_id=None, reference_lap=None, cancelled=lambda: False):
    with closing(repository._connect()) as con:
        con.execute("BEGIN")
        session = con.execute("SELECT * FROM sessions WHERE session_uid=?", (uid,)).fetchone()
        if not session:
            raise ValueError("找不到所选会话")
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        args = [uid]
        query = "SELECT l.*,COALESCE(a.quality_status,'pending') AS quality_status FROM laps l LEFT JOIN lap_analysis a USING(session_uid,lap_number) WHERE l.session_uid=?"
        if segment_id is not None:
            if "lap_segments" not in tables:
                raise ValueError("旧库没有驾驶段记录，请选择全部驾驶段")
            query += " AND EXISTS(SELECT 1 FROM lap_segments g WHERE g.session_uid=l.session_uid AND g.lap_number=l.lap_number AND g.segment_id=?)"
            args.append(segment_id)
        records = [dict(row) for row in con.execute(query + " ORDER BY l.lap_number DESC", args)]
        omitted = max(0, len(records) - MAX_REPORT_LAPS)
        records = records[:MAX_REPORT_LAPS]
        tyres = {r["lap_number"]: dict(r) for r in con.execute("SELECT * FROM lap_tyres WHERE session_uid=?", (uid,))} if "lap_tyres" in tables else {}
        segments = dict(con.execute("SELECT lap_number,segment_id FROM lap_segments WHERE session_uid=?", (uid,))) if "lap_segments" in tables else {}
        sources, rows = {}, []
        length = session["track_length_m"]
        for record in records:
            if cancelled():
                raise InterruptedError("复盘已取消")
            number = record["lap_number"]
            source_rows = _source_rows(con, uid, number, length) if repository.has_source_samples else []
            source = inspect_source(source_rows)
            reasons = []
            if not record["lap_valid"] or record["lap_time_ms"] <= 0:
                reasons.append("游戏判定无效/未完成")
            if record["quality_status"] != "ready":
                reasons.append(REASONS.get(record["quality_status"], record["quality_status"]))
            if source.status != "ready":
                reasons.append("源采样" + REASONS.get(source.status, source.status))
            if repository.has_source_samples:
                flags = con.execute("SELECT MAX(pit_status!=0),MAX(driver_status IN (0,2,3)),MAX(current_lap_invalid),COUNT(DISTINCT player_car_index) FROM telemetry_samples WHERE session_uid=? AND lap_number=? AND superseded=0", (uid, number)).fetchone()
                if flags[0] or flags[1]:
                    reasons.append("出入站/车库圈")
                if flags[2]:
                    reasons.append("源采样标记无效")
                if flags[3] > 1:
                    reasons.append("圈内玩家索引变化")
            tyre = tyres.get(number, {})
            item = {"lap": number, "time_ms": record["lap_time_ms"],
                    "sector_ms": [record[f"sector{i}_ms"] if record[f"sector{i}_valid"] and record[f"sector{i}_ms"] > 0 else None for i in (1,2,3)],
                    "eligible": not reasons, "reasons": list(dict.fromkeys(reasons)),
                    "compound": tyre.get("actual_compound"), "visual_compound": tyre.get("visual_compound"),
                    "tyre_set": tyre.get("stint_number"), "tyre_lap": tyre.get("tyre_lap_number"),
                    "wear_percent": tyre.get("wear_percent"), "segment_id": segments.get(number),
                    "conditions": _conditions(con, uid, number, source_rows, length, "condition_frames" in tables),
                    "quality_warning_count": len(source.issues)}
            if not reasons:
                sources[number] = source
            rows.append(item)
        eligible = [row for row in rows if row["eligible"]]
        groups = {}
        for row in eligible:
            # Independent labels: a garage segment is never assumed to be a tyre set.
            key = (row["segment_id"], row["tyre_set"], row["compound"])
            groups.setdefault(key, []).append(row)
        group_summaries = [{"segment_id": key[0], "tyre_set": key[1], "compound": key[2],
                            "statistics": _statistics([r["time_ms"] for r in values]),
                            "laps": sorted(r["lap"] for r in values)} for key, values in groups.items()]
        reference = next((r for r in eligible if r["lap"] == reference_lap), None)
        if reference_lap is None and eligible:
            reference = min(eligible, key=lambda r: r["time_ms"])
        candidates, excluded_conditions = [], []
        if reference:
            for row in eligible:
                if row["lap"] == reference["lap"]:
                    continue
                different_segment = "lap_segments" in tables and row["segment_id"] != reference["segment_id"]
                different_compound = row["compound"] is not None and reference["compound"] is not None and row["compound"] != reference["compound"]
                if different_segment or different_compound:
                    excluded_conditions.append(row["lap"])
                else:
                    candidates.append(row)
            candidates = candidates[:MAX_CANDIDATES]
        conditions = [{"lap": row["lap"], "warnings": condition_warnings(reference, row)} for row in candidates] if reference else []
        repeats = repeatability(sources, reference["lap"], [r["lap"] for r in candidates], length, cancelled) if reference else []
        return {"scope": "所选驾驶段" if segment_id is not None else "会话汇总（不同驾驶段/胎组另列）",
                "session": f"{session_label(session['track'], session['session_type'])} · {session['game_mode'] or '模式未知'}",
                "rows": rows, "omitted_laps": omitted, "statistics": _statistics([r["time_ms"] for r in eligible]),
                "sectors": [_statistics([r["sector_ms"][i] for r in eligible]) for i in range(3)],
                "groups": group_summaries, "excluded_reasons": dict(Counter(reason for row in rows for reason in row["reasons"])),
                "reference_lap": reference["lap"] if reference else None,
                "candidate_laps": [r["lap"] for r in candidates], "condition_excluded_laps": excluded_conditions,
                "condition_warnings": conditions, "repeatability": repeats,
                "repeatability_note": "固定100米区间，不等于弯道；最多最近12个候选圈。不同驾驶段/已知不同配方不混比。损失超过20 ms，至少2圈且占已覆盖候选的2/3、覆盖总候选至少2/3时标记重复观察。非统计显著性或驾驶原因。"}
