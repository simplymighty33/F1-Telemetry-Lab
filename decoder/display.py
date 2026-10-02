"""Display-only names: never rewrite protocol identifiers or archive values."""
from math import isfinite

TRACK_LABELS = {
    "Melbourne": "澳大利亚", "Paul Ricard": "法国", "Shanghai": "中国",
    "Sakhir (Bahrain)": "巴林", "Catalunya": "西班牙", "Monaco": "摩纳哥",
    "Montreal": "加拿大", "Silverstone": "英国", "Hockenheim": "德国",
    "Hungaroring": "匈牙利", "Spa": "比利时", "Monza": "意大利（蒙扎）",
    "Singapore": "新加坡", "Suzuka": "日本", "Abu Dhabi": "阿布扎比",
    "Texas": "美国（奥斯汀）", "Brazil": "巴西", "Austria": "奥地利",
    "Sochi": "俄罗斯", "Mexico": "墨西哥", "Baku (Azerbaijan)": "阿塞拜疆",
    "Sakhir Short": "巴林（短道）", "Silverstone Short": "英国（短道）",
    "Texas Short": "奥斯汀（短道）", "Suzuka Short": "日本（短道）",
    "Hanoi": "越南", "Zandvoort": "荷兰", "Imola": "意大利（伊莫拉）",
    "Portimao": "葡萄牙", "Jeddah": "沙特阿拉伯", "Miami": "美国（迈阿密）",
    "Las Vegas": "美国（拉斯维加斯）", "Losail": "卡塔尔",
    "Silverstone Reverse": "英国（反向）", "Austria Reverse": "奥地利（反向）",
    "Zandvoort Reverse": "荷兰（反向）", "Madrid": "西班牙（马德里）",
}
STAGE_LABELS = {
    "Practice 1": "练习赛1", "Practice 2": "练习赛2", "Practice 3": "练习赛3",
    "Short Practice": "短练习赛", "Qualifying 1": "排位赛Q1",
    "Qualifying 2": "排位赛Q2", "Qualifying 3": "排位赛Q3",
    "Short Qualifying": "短排位赛", "One-Shot Qualifying": "单圈排位赛",
    "Sprint Shootout 1": "冲刺排位赛SQ1", "Sprint Shootout 2": "冲刺排位赛SQ2",
    "Sprint Shootout 3": "冲刺排位赛SQ3", "Short Sprint Shootout": "短冲刺排位赛",
    "One-Shot Sprint Shootout": "单圈冲刺排位赛", "Time Trial": "计时赛",
    # The documented enum does not identify Sprint Race. Do not guess from lap count.
    "Race": "比赛（正赛/冲刺待确认）", "Race 2": "比赛2（正赛/冲刺待确认）",
    "Race 3": "比赛3（环节待确认）", "Unknown": "环节未知",
}


def track_label(value):
    return TRACK_LABELS.get(value, value if value and value != "—" else "赛道未知")


def stage_label(value):
    return STAGE_LABELS.get(value, value if value and value != "—" else "环节未知")


def session_label(track, stage):
    return f"{track_label(track)} · {stage_label(stage)}"


def tyre_label(tyre):
    wear = tyre.wear_percent
    known = isinstance(wear, (int, float)) and isfinite(wear) and 0 <= wear <= 100
    return f"{tyre.name}（磨损{wear:.1f}%）" if known else f"{tyre.name}（磨损未知）"


def lap_display(track, stage, number, tyre, time_text):
    return f"{session_label(track, stage)} · 总第{number}圈｜{tyre_label(tyre)}｜{time_text}"
