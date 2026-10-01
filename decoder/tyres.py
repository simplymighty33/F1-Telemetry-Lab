"""Bounded player tyre observations shared by live lap history and Replay."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
import math

from decoder.header import PacketHeader


VISUAL_COMPOUNDS = {
    7: "半雨胎", 8: "全雨胎", 9: "干地胎", 10: "雨胎", 15: "雨胎",
    16: "软胎", 17: "中性胎", 18: "硬胎",
    19: "超软胎", 20: "软胎", 21: "中性胎", 22: "硬胎",
}
ACTUAL_COMPOUNDS = {7: "半雨胎", 8: "全雨胎", 9: "干地胎", 10: "雨胎",
                    11: "超软胎", 12: "软胎", 13: "中性胎", 14: "硬胎", 15: "雨胎"}


def tyre_name(visual: int | None, actual: int | None = None) -> str:
    # C0-C6 is not a universal soft/medium/hard mapping; use the visual compound.
    if visual in VISUAL_COMPOUNDS:
        return VISUAL_COMPOUNDS[visual]
    if actual in ACTUAL_COMPOUNDS:
        return ACTUAL_COMPOUNDS[actual]
    if actual is not None and 16 <= actual <= 22:
        return {16: "C5", 17: "C4", 18: "C3", 19: "C2", 20: "C1", 21: "C0", 22: "C6"}[actual]
    return "未知轮胎"


@dataclass(frozen=True, slots=True)
class LapTyreInfo:
    actual_compound: int | None = None
    visual_compound: int | None = None
    stint_number: int | None = None
    tyre_lap_number: int | None = None
    fitted_set_index: int | None = None
    wear_percent: float | None = None
    wear_rl: float | None = None
    wear_rr: float | None = None
    wear_fl: float | None = None
    wear_fr: float | None = None
    wear_source: str | None = None
    wear_frame_identifier: int | None = None
    wear_session_time: float | None = None
    tyres_age_laps: int | None = None
    compound_source: str | None = None

    @property
    def name(self) -> str:
        return tyre_name(self.visual_compound, self.actual_compound)

    @property
    def label(self) -> str:
        wear = "磨损 —" if self.wear_percent is None else f"磨损 {self.wear_percent:.1f}%"
        return f"{self.name} · {wear}"


@dataclass(frozen=True, slots=True)
class _Observation:
    frame: int
    time: float
    values: tuple


class PlayerTyreTracker:
    """One instance per session/player; retain only bounded recent samples and laps.

    Session History stint boundaries provide independent lap numbering. Wear is
    the last valid four-wheel damage sample before the completed lap's last Lap
    Data frame, at most 4 seconds old. Tyre Sets provides a marked fallback.
    """

    PACKET_IDS = {2, 3, 7, 10, 11, 12}

    def __init__(self) -> None:
        self._recent = {key: deque(maxlen=256) for key in (7, 10, 12)}
        self._lap: tuple[int, int, float] | None = None
        self._finished: dict[int, tuple[int, float, LapTyreInfo]] = {}
        self.invalidated_laps: set[int] = set()

    def checkpoint(self) -> dict:
        """Explicit JSON state, never executable pickle, for incremental resume."""
        from dataclasses import asdict
        return {"recent": {str(k): [asdict(v) for v in values] for k, values in self._recent.items()},
                "lap": self._lap,
                "finished": {str(k): [f, t, asdict(v)] for k, (f, t, v) in self._finished.items()}}

    @classmethod
    def restore(cls, state: dict) -> PlayerTyreTracker:
        tracker = cls()
        for key, values in state.get("recent", {}).items():
            tracker._recent[int(key)].extend(_Observation(v["frame"], v["time"], tuple(v["values"])) for v in values)
        tracker._lap = tuple(state["lap"]) if state.get("lap") else None
        tracker._finished = {int(k): (v[0], v[1], LapTyreInfo(**v[2])) for k, v in state.get("finished", {}).items()}
        return tracker

    def _at(self, packet_id: int, frame: int, time: float) -> _Observation | None:
        eligible = (item for item in self._recent[packet_id]
                    if item.frame <= frame and 0 <= time - item.time <= 4.0)
        return max(eligible, key=lambda item: (item.frame, item.time), default=None)

    def _snapshot(self, frame: int, time: float) -> LapTyreInfo:
        status, damage, sets = (self._at(packet_id, frame, time) for packet_id in (7, 10, 12))
        actual, visual, age = status.values if status else (None, None, None)
        fitted = None
        set_wear = None
        if sets:
            fitted, set_actual, set_visual, set_wear = sets.values
            if status and (set_actual, set_visual) != (actual, visual):
                fitted, set_wear = None, None
            elif status is None:
                actual, visual = set_actual, set_visual
        wheels = damage.values if damage else (None,) * 4
        mean = sum(wheels) / 4 if damage else set_wear
        source = "car_damage_average" if damage else "tyre_sets" if set_wear is not None else None
        sample = damage if damage else sets if set_wear is not None else None
        return LapTyreInfo(actual_compound=actual, visual_compound=visual,
                           fitted_set_index=fitted, wear_percent=mean,
                           wear_rl=wheels[0], wear_rr=wheels[1], wear_fl=wheels[2], wear_fr=wheels[3],
                           wear_source=source, wear_frame_identifier=sample.frame if sample else None,
                           wear_session_time=sample.time if sample else None, tyres_age_laps=age,
                           compound_source="car_status" if status else "tyre_sets" if sets else None)

    def observe(self, header: PacketHeader, body: dict) -> bool:
        packet_id, frame, time = header.packet_id, header.frame_identifier, header.session_time
        self.invalidated_laps = set()
        if packet_id in {11, 12} and body.get("car_idx") != header.player_car_index:
            return False
        if packet_id == 3 and body.get("event_code") == "FLBK":
            target = body["event_details"]["flashback_frame_identifier"]
            target_time = body["event_details"].get("flashback_session_time", time)
            self.invalidated_laps = {number for number, value in self._finished.items() if value[0] > target}
            self._finished = {number: value for number, value in self._finished.items() if value[0] <= target}
            for samples in self._recent.values():
                kept = [item for item in samples if item.frame <= target and item.time <= target_time]
                samples.clear()
                samples.extend(kept)
            self._lap = None
            return bool(self.invalidated_laps)
        if packet_id == 2:
            number = body.get("current_lap_num", 0)
            if number <= 0:
                return False
            changed = False
            if self._lap and number > self._lap[0]:
                previous, end_frame, end_time = self._lap
                self._finished[previous] = (end_frame, end_time, self._snapshot(end_frame, end_time))
                changed = True
            elif self._lap and number == self._lap[0] and frame < self._lap[1]:
                return False  # A late datagram alone is not evidence of Flashback.
            elif self._lap and number < self._lap[0]:
                # Conservative implicit rewind if the FLBK event was not received.
                self.invalidated_laps = {n for n in self._finished if n >= number}
                for n in self.invalidated_laps:
                    self._finished.pop(n)
                for samples in self._recent.values():
                    kept = [item for item in samples if item.frame <= frame and item.time <= time]
                    samples.clear()
                    samples.extend(kept)
                changed = bool(self.invalidated_laps)
            self._lap = (number, frame, time)
            return changed
        if packet_id == 11:
            completed = {number for number, entry in enumerate(body.get("lap_history", [])[:body.get("num_laps", 0)], 1)
                         if entry["lap_time_ms"] > 0}
            # History can lag the finish-line Lap Data by one update. Never erase
            # a newly completed snapshot merely because that older history has
            # not published it yet; FLBK/Lap Data rewind invalidate explicitly.
            # Last history at session end can arrive without a lap rollover packet.
            if self._lap and self._lap[0] in completed and self._lap[0] not in self._finished:
                number, end_frame, end_time = self._lap
                self._finished[number] = (end_frame, end_time, self._snapshot(end_frame, end_time))
            return False
        if packet_id == 7:
            actual, visual = body.get("actual_tyre_compound"), body.get("visual_tyre_compound")
            if not actual and not visual:
                return False
            values = (actual, visual, body.get("tyres_age_laps"))
        elif packet_id == 10:
            wear = body.get("tyres_wear", ())
            if len(wear) != 4 or any(value is None or not math.isfinite(value) or not 0 <= value <= 100 for value in wear):
                return False
            values = tuple(wear)
        elif packet_id == 12:
            fitted = body.get("fitted_idx", 255)
            sets = body.get("tyre_sets", [])
            if not 0 <= fitted < len(sets):
                return False
            tyre = sets[fitted]
            if not tyre.get("actual_tyre_compound") and not tyre.get("visual_tyre_compound"):
                return False
            wear = tyre.get("wear")
            if wear is None or not math.isfinite(wear) or not 0 <= wear <= 100:
                return False
            values = (fitted, tyre["actual_tyre_compound"], tyre["visual_tyre_compound"], float(wear))
        else:
            return False
        self._recent[packet_id].append(_Observation(frame, time, values))
        # Out-of-order low-rate packets can refine only the completed end snapshot.
        changed = False
        for number, (end_frame, end_time, info) in reversed(self._finished.items()):
            if time - end_time > 4 and frame > end_frame:
                break
            if frame <= end_frame and 0 <= end_time - time <= 4:
                updated = self._snapshot(end_frame, end_time)
                if updated != info:
                    self._finished[number] = (end_frame, end_time, updated)
                    changed = True
        return changed

    def laps(self, history: dict) -> dict[int, LapTyreInfo]:
        result = {}
        start = 1
        stints = history.get("tyre_stints", [])[:history.get("num_tyre_stints", 0)]
        boundaries = []
        for index, stint in enumerate(stints, 1):
            end = stint["end_lap"]
            if end != 255 and end < start:
                continue
            boundaries.append((start, 100 if end == 255 else end, index, stint))
            if end == 255:
                break
            start = end + 1
        group, group_start, previous = 0, 1, None
        for number, entry in enumerate(history.get("lap_history", [])[:history.get("num_laps", 0)], 1):
            if entry["lap_time_ms"] <= 0:
                continue
            info = self._finished.get(number, (0, 0, LapTyreInfo()))[2]
            match = next((value for value in boundaries if value[0] <= number <= value[1]), None)
            observed = info.visual_compound is not None or info.actual_compound is not None
            nominal_index, first, conflict = None, number, False
            if match:
                first, _end, nominal_index, stint = match
                actual, visual = stint["tyre_actual_compound"] or None, stint["tyre_visual_compound"] or None
                conflict = observed and (info.actual_compound, info.visual_compound) != (actual, visual)
                if not observed:
                    info = replace(info, actual_compound=actual, visual_compound=visual, compound_source="session_history")
                elif conflict:
                    info = replace(info, compound_source=f"{info.compound_source}_over_history")
            if info.visual_compound is not None or info.actual_compound is not None:
                compounds = (info.actual_compound, info.visual_compound)
                if previous is None:
                    group, group_start = nominal_index or 1, first if not conflict else number
                else:
                    old_compounds, old_set, old_age, old_index = previous
                    changed = compounds != old_compounds or (
                        info.fitted_set_index is not None and old_set is not None and info.fitted_set_index != old_set
                    ) or (
                        info.tyres_age_laps is not None and old_age is not None and info.tyres_age_laps < old_age
                    ) or (
                        nominal_index is not None and old_index is not None and nominal_index != old_index and not conflict
                    )
                    if changed:
                        group += 1
                        group_start = number
                previous = (compounds, info.fitted_set_index, info.tyres_age_laps, nominal_index)
                info = replace(info, stint_number=group, tyre_lap_number=number - group_start + 1)
            result[number] = info
        return result
