"""Complete versioned decoder for F1 23, F1 24 and F1 25 UDP data."""

from __future__ import annotations

from dataclasses import asdict
from functools import lru_cache
import struct
from typing import Any

from decoder.header import HEADER_SIZE, PacketHeader, decode_header
from decoder.packet_parser import packet_name
from decoder.protocol import ProtocolProfile, protocol_for_format


# Kept as the F1 23 mapping for source compatibility with earlier releases.
PACKET_SIZES = dict(protocol_for_format(2023).packet_sizes)
PACKET_SIZES_BY_FORMAT = {
    packet_format: dict(protocol_for_format(packet_format).packet_sizes)
    for packet_format in (2023, 2024, 2025, 2026)
}

SESSION_TYPE_NAMES_2023 = {
    0: "Unknown", 1: "Practice 1", 2: "Practice 2", 3: "Practice 3",
    4: "Short Practice", 5: "Qualifying 1", 6: "Qualifying 2",
    7: "Qualifying 3", 8: "Short Qualifying", 9: "One-Shot Qualifying",
    10: "Race", 11: "Race 2", 12: "Race 3", 13: "Time Trial",
}
SESSION_TYPE_NAMES_MODERN = {
    0: "Unknown", 1: "Practice 1", 2: "Practice 2", 3: "Practice 3",
    4: "Short Practice", 5: "Qualifying 1", 6: "Qualifying 2",
    7: "Qualifying 3", 8: "Short Qualifying", 9: "One-Shot Qualifying",
    10: "Sprint Shootout 1", 11: "Sprint Shootout 2",
    12: "Sprint Shootout 3", 13: "Short Sprint Shootout",
    14: "One-Shot Sprint Shootout", 15: "Race", 16: "Race 2",
    17: "Race 3", 18: "Time Trial",
}

GAME_MODE_NAMES_2023 = {
    0: "Event Mode", 3: "Grand Prix", 4: "Grand Prix '23", 5: "Time Trial",
    6: "Splitscreen", 7: "Online Custom", 8: "Online League",
    11: "Career Invitational", 12: "Championship Invitational",
    13: "Championship", 14: "Online Championship", 15: "Online Weekly Event",
    17: "Story Mode", 19: "Career '22", 20: "Career '22 Online",
    21: "Career '23", 22: "Career '23 Online", 127: "Benchmark",
}
GAME_MODE_NAMES_2024 = {
    **GAME_MODE_NAMES_2023,
    23: "Driver Career '24", 24: "Career '24 Online",
    25: "My Team Career '24", 26: "Curated Career '24",
}
GAME_MODE_NAMES_2025 = {
    4: "Grand Prix", 5: "Time Trial", 6: "Splitscreen", 7: "Online Custom",
    15: "Online Weekly Event", 17: "Breaking Point", 27: "My Team Career '25",
    28: "Driver Career '25", 29: "Career '25 Online",
    30: "Challenge Career '25", 75: "APXGP Story", 127: "Benchmark",
}

RULE_SET_NAMES = {
    0: "Practice & Qualifying", 1: "Race", 2: "Time Trial", 4: "Time Attack",
    6: "Checkpoint Challenge", 8: "Autocross", 9: "Drift",
    10: "Average Speed Zone", 11: "Rival Duel", 12: "Elimination",
}

TRACK_NAMES = {
    0: "Melbourne", 1: "Paul Ricard", 2: "Shanghai", 3: "Sakhir (Bahrain)",
    4: "Catalunya", 5: "Monaco", 6: "Montreal", 7: "Silverstone",
    8: "Hockenheim", 9: "Hungaroring", 10: "Spa", 11: "Monza",
    12: "Singapore", 13: "Suzuka", 14: "Abu Dhabi", 15: "Texas",
    16: "Brazil", 17: "Austria", 18: "Sochi", 19: "Mexico",
    20: "Baku (Azerbaijan)", 21: "Sakhir Short", 22: "Silverstone Short",
    23: "Texas Short", 24: "Suzuka Short", 25: "Hanoi", 26: "Zandvoort",
    27: "Imola", 28: "Portimao", 29: "Jeddah", 30: "Miami",
    31: "Las Vegas", 32: "Losail", 39: "Silverstone Reverse",
    40: "Austria Reverse", 41: "Zandvoort Reverse", 42: "Madrid",
}

EVENT_NAMES = {
    "SSTA": "Session Started", "SEND": "Session Ended", "FTLP": "Fastest Lap",
    "RTMT": "Retirement", "DRSE": "DRS Enabled", "DRSD": "DRS Disabled",
    "TMPT": "Team Mate In Pits", "CHQF": "Chequered Flag", "RCWN": "Race Winner",
    "PENA": "Penalty Issued", "SPTP": "Speed Trap", "STLG": "Start Lights",
    "LGOT": "Lights Out", "DTSV": "Drive Through Served", "SGSV": "Stop Go Served",
    "FLBK": "Flashback", "BUTN": "Button Status", "RDFL": "Red Flag",
    "OVTK": "Overtake", "SCAR": "Safety Car", "COLL": "Collision",
}


class PacketDecodeError(ValueError):
    pass


Schema = tuple[tuple[str, str], ...]

MOTION_SCHEMA: Schema = (
    ("world_position_x", "f"), ("world_position_y", "f"), ("world_position_z", "f"),
    ("world_velocity_x", "f"), ("world_velocity_y", "f"), ("world_velocity_z", "f"),
    ("world_forward_dir_x", "h"), ("world_forward_dir_y", "h"),
    ("world_forward_dir_z", "h"), ("world_right_dir_x", "h"),
    ("world_right_dir_y", "h"), ("world_right_dir_z", "h"),
    ("g_force_lateral", "f"), ("g_force_longitudinal", "f"),
    ("g_force_vertical", "f"), ("yaw", "f"), ("pitch", "f"), ("roll", "f"),
)

LAP_SCHEMA: Schema = (
    ("last_lap_time_ms", "I"), ("current_lap_time_ms", "I"),
    ("sector1_time_ms", "H"), ("sector1_time_minutes", "B"),
    ("sector2_time_ms", "H"), ("sector2_time_minutes", "B"),
    ("delta_to_car_in_front_ms", "H"), ("delta_to_race_leader_ms", "H"),
    ("lap_distance", "f"), ("total_distance", "f"), ("safety_car_delta", "f"),
    ("car_position", "B"), ("current_lap_num", "B"), ("pit_status", "B"),
    ("num_pit_stops", "B"), ("sector", "B"), ("current_lap_invalid", "B"),
    ("penalties", "B"), ("total_warnings", "B"), ("corner_cutting_warnings", "B"),
    ("num_unserved_drive_through_pens", "B"), ("num_unserved_stop_go_pens", "B"),
    ("grid_position", "B"), ("driver_status", "B"), ("result_status", "B"),
    ("pit_lane_timer_active", "B"), ("pit_lane_time_in_lane_ms", "H"),
    ("pit_stop_timer_ms", "H"), ("pit_stop_should_serve_pen", "B"),
)

LAP_SCHEMA_MODERN: Schema = (
    ("last_lap_time_ms", "I"), ("current_lap_time_ms", "I"),
    ("sector1_time_ms", "H"), ("sector1_time_minutes", "B"),
    ("sector2_time_ms", "H"), ("sector2_time_minutes", "B"),
    ("delta_to_car_in_front_ms", "H"), ("delta_to_car_in_front_minutes", "B"),
    ("delta_to_race_leader_ms", "H"), ("delta_to_race_leader_minutes", "B"),
    ("lap_distance", "f"), ("total_distance", "f"), ("safety_car_delta", "f"),
    ("car_position", "B"), ("current_lap_num", "B"), ("pit_status", "B"),
    ("num_pit_stops", "B"), ("sector", "B"), ("current_lap_invalid", "B"),
    ("penalties", "B"), ("total_warnings", "B"), ("corner_cutting_warnings", "B"),
    ("num_unserved_drive_through_pens", "B"), ("num_unserved_stop_go_pens", "B"),
    ("grid_position", "B"), ("driver_status", "B"), ("result_status", "B"),
    ("pit_lane_timer_active", "B"), ("pit_lane_time_in_lane_ms", "H"),
    ("pit_stop_timer_ms", "H"), ("pit_stop_should_serve_pen", "B"),
    ("speed_trap_fastest_speed", "f"), ("speed_trap_fastest_lap", "B"),
)

CAR_SETUP_SCHEMA: Schema = (
    ("front_wing", "B"), ("rear_wing", "B"), ("on_throttle", "B"),
    ("off_throttle", "B"), ("front_camber", "f"), ("rear_camber", "f"),
    ("front_toe", "f"), ("rear_toe", "f"), ("front_suspension", "B"),
    ("rear_suspension", "B"), ("front_anti_roll_bar", "B"),
    ("rear_anti_roll_bar", "B"), ("front_suspension_height", "B"),
    ("rear_suspension_height", "B"), ("brake_pressure", "B"), ("brake_bias", "B"),
    ("rear_left_tyre_pressure", "f"), ("rear_right_tyre_pressure", "f"),
    ("front_left_tyre_pressure", "f"), ("front_right_tyre_pressure", "f"),
    ("ballast", "B"), ("fuel_load", "f"),
)
CAR_SETUP_SCHEMA_MODERN: Schema = CAR_SETUP_SCHEMA[:16] + (("engine_braking", "B"),) + CAR_SETUP_SCHEMA[16:]

CAR_TELEMETRY_SCHEMA: Schema = (
    ("speed", "H"), ("throttle", "f"), ("steer", "f"), ("brake", "f"),
    ("clutch", "B"), ("gear", "b"), ("engine_rpm", "H"), ("drs", "B"),
    ("rev_lights_percent", "B"), ("rev_lights_bit_value", "H"),
    ("brakes_temperature", "4H"), ("tyres_surface_temperature", "4B"),
    ("tyres_inner_temperature", "4B"), ("engine_temperature", "H"),
    ("tyres_pressure", "4f"), ("surface_type", "4B"),
)

CAR_STATUS_SCHEMA: Schema = (
    ("traction_control", "B"), ("anti_lock_brakes", "B"), ("fuel_mix", "B"),
    ("front_brake_bias", "B"), ("pit_limiter_status", "B"), ("fuel_in_tank", "f"),
    ("fuel_capacity", "f"), ("fuel_remaining_laps", "f"), ("max_rpm", "H"),
    ("idle_rpm", "H"), ("max_gears", "B"), ("drs_allowed", "B"),
    ("drs_activation_distance", "H"), ("actual_tyre_compound", "B"),
    ("visual_tyre_compound", "B"), ("tyres_age_laps", "B"),
    ("vehicle_fia_flags", "b"), ("engine_power_ice", "f"),
    ("engine_power_mguk", "f"), ("ers_store_energy", "f"), ("ers_deploy_mode", "B"),
    ("ers_harvested_this_lap_mguk", "f"), ("ers_harvested_this_lap_mguh", "f"),
    ("ers_deployed_this_lap", "f"), ("network_paused", "B"),
)
CAR_STATUS_SCHEMA_2026: Schema = CAR_STATUS_SCHEMA[:23] + (("ers_harvested_limit_per_lap", "f"),) + CAR_STATUS_SCHEMA[23:]

FINAL_CLASSIFICATION_SCHEMA: Schema = (
    ("position", "B"), ("num_laps", "B"), ("grid_position", "B"),
    ("points", "B"), ("num_pit_stops", "B"), ("result_status", "B"),
    ("best_lap_time_ms", "I"), ("total_race_time", "d"),
    ("penalties_time", "B"), ("num_penalties", "B"), ("num_tyre_stints", "B"),
    ("tyre_stints_actual", "8B"), ("tyre_stints_visual", "8B"),
    ("tyre_stints_end_laps", "8B"),
)
FINAL_CLASSIFICATION_SCHEMA_2025: Schema = FINAL_CLASSIFICATION_SCHEMA[:6] + (("result_reason", "B"),) + FINAL_CLASSIFICATION_SCHEMA[6:]

CAR_DAMAGE_SCHEMA: Schema = (
    ("tyres_wear", "4f"), ("tyres_damage", "4B"), ("brakes_damage", "4B"),
    ("front_left_wing_damage", "B"), ("front_right_wing_damage", "B"),
    ("rear_wing_damage", "B"), ("floor_damage", "B"), ("diffuser_damage", "B"),
    ("sidepod_damage", "B"), ("drs_fault", "B"), ("ers_fault", "B"),
    ("gear_box_damage", "B"), ("engine_damage", "B"), ("engine_mguh_wear", "B"),
    ("engine_es_wear", "B"), ("engine_ce_wear", "B"), ("engine_ice_wear", "B"),
    ("engine_mguk_wear", "B"), ("engine_tc_wear", "B"), ("engine_blown", "B"),
    ("engine_seized", "B"),
)
CAR_DAMAGE_SCHEMA_2025: Schema = CAR_DAMAGE_SCHEMA[:3] + (("tyre_blisters", "4B"),) + CAR_DAMAGE_SCHEMA[3:]

LAP_HISTORY_SCHEMA: Schema = (
    ("lap_time_ms", "I"), ("sector1_time_ms", "H"), ("sector1_time_minutes", "B"),
    ("sector2_time_ms", "H"), ("sector2_time_minutes", "B"),
    ("sector3_time_ms", "H"), ("sector3_time_minutes", "B"),
    ("lap_valid_bit_flags", "B"),
)

TYRE_SET_SCHEMA: Schema = (
    ("actual_tyre_compound", "B"), ("visual_tyre_compound", "B"),
    ("wear", "B"), ("available", "B"), ("recommended_session", "B"),
    ("life_span", "B"), ("usable_life", "B"), ("lap_delta_time", "h"),
    ("fitted", "B"),
)

MOTION_EX_SCHEMA: Schema = (
    ("suspension_position", "4f"), ("suspension_velocity", "4f"),
    ("suspension_acceleration", "4f"), ("wheel_speed", "4f"),
    ("wheel_slip_ratio", "4f"), ("wheel_slip_angle", "4f"),
    ("wheel_lat_force", "4f"), ("wheel_long_force", "4f"),
    ("height_of_cog_above_ground", "f"), ("local_velocity_x", "f"),
    ("local_velocity_y", "f"), ("local_velocity_z", "f"),
    ("angular_velocity_x", "f"), ("angular_velocity_y", "f"),
    ("angular_velocity_z", "f"), ("angular_acceleration_x", "f"),
    ("angular_acceleration_y", "f"), ("angular_acceleration_z", "f"),
    ("front_wheels_angle", "f"), ("wheel_vert_force", "4f"),
)
MOTION_EX_SCHEMA_2024: Schema = MOTION_EX_SCHEMA + (
    ("front_aero_height", "f"), ("rear_aero_height", "f"),
    ("front_roll_angle", "f"), ("rear_roll_angle", "f"), ("chassis_yaw", "f"),
)
MOTION_EX_SCHEMA_2025: Schema = MOTION_EX_SCHEMA_2024 + (
    ("chassis_pitch", "f"), ("wheel_camber", "4f"), ("wheel_camber_gain", "4f"),
)

TIME_TRIAL_SCHEMA: Schema = (
    ("car_idx", "B"), ("team_id", "B"), ("lap_time_ms", "I"),
    ("sector1_time_ms", "I"), ("sector2_time_ms", "I"), ("sector3_time_ms", "I"),
    ("traction_control", "B"), ("gearbox_assist", "B"), ("anti_lock_brakes", "B"),
    ("equal_car_performance", "B"), ("custom_setup", "B"), ("valid", "B"),
)
TIME_TRIAL_SCHEMA_2026: Schema = TIME_TRIAL_SCHEMA[:1] + (("team_id", "H"),) + TIME_TRIAL_SCHEMA[2:]

CAR_TELEMETRY_2_SCHEMA: Schema = (
    ("active_aero_mode", "B"), ("active_aero_available", "B"),
    ("active_aero_activation_distance", "H"), ("overtake_available", "B"),
    ("overtake_active", "B"), ("overtake_activation_distance", "H"),
    ("regulations_2026", "B"), ("driving_wrong_way", "B"),
)


@lru_cache(maxsize=256)
def compiled_schema(schema: Schema) -> tuple[struct.Struct, tuple[tuple[str, int, int], ...]]:
    fields = []
    index = 0
    for name, fmt in schema:
        parser = struct.Struct("<" + fmt)
        count = len(parser.unpack(bytes(parser.size)))
        fields.append((name, index, index + count))
        index += count
    return struct.Struct("<" + "".join(fmt for _, fmt in schema)), tuple(fields)


def _read_schema(packet: bytes, offset: int, schema: Schema) -> tuple[dict[str, Any], int]:
    parser, fields = compiled_schema(schema)
    values = parser.unpack_from(packet, offset)
    return {
        name: values[start] if end - start == 1 else list(values[start:end])
        for name, start, end in fields
    }, offset + parser.size


def _read_records(packet: bytes, offset: int, schema: Schema, count: int,
                  index_name: str = "car_index") -> tuple[list[dict[str, Any]], int]:
    records = []
    for index in range(count):
        record, offset = _read_schema(packet, offset, schema)
        record[index_name] = index
        records.append(record)
    return records, offset


def _decode_name(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("utf-8", errors="replace")


def player_wire_schema(packet_format: int, packet_id: int) -> Schema | None:
    if packet_id == 0:
        return MOTION_SCHEMA
    if packet_id == 2:
        return LAP_SCHEMA if packet_format == 2023 else LAP_SCHEMA_MODERN
    if packet_id == 5:
        return CAR_SETUP_SCHEMA if packet_format == 2023 else CAR_SETUP_SCHEMA_MODERN
    if packet_id == 6:
        return CAR_TELEMETRY_SCHEMA
    if packet_id == 7:
        return CAR_STATUS_SCHEMA_2026 if packet_format == 2026 else CAR_STATUS_SCHEMA
    if packet_id == 10:
        return CAR_DAMAGE_SCHEMA_2025 if packet_format >= 2025 else CAR_DAMAGE_SCHEMA
    if packet_id == 13:
        return {2023: MOTION_EX_SCHEMA, 2024: MOTION_EX_SCHEMA_2024}.get(
            packet_format, MOTION_EX_SCHEMA_2025
        )
    if packet_id == 16 and packet_format == 2026:
        return CAR_TELEMETRY_2_SCHEMA
    return None


def canonical_player_schema(packet_id: int) -> Schema | None:
    return {
        0: MOTION_SCHEMA, 2: LAP_SCHEMA_MODERN, 5: CAR_SETUP_SCHEMA_MODERN,
        6: CAR_TELEMETRY_SCHEMA, 7: CAR_STATUS_SCHEMA_2026,
        10: CAR_DAMAGE_SCHEMA_2025, 13: MOTION_EX_SCHEMA_2025,
        16: CAR_TELEMETRY_2_SCHEMA,
    }.get(packet_id)


def normalize_player_record(record: dict[str, Any], packet_id: int) -> dict[str, Any]:
    schema = canonical_player_schema(packet_id)
    if schema is None:
        return record
    normalized: dict[str, Any] = {}
    for name, fmt in schema:
        if name in record:
            normalized[name] = record[name]
            continue
        parser = struct.Struct("<" + fmt)
        count = len(parser.unpack(bytes(parser.size)))
        normalized[name] = None if count == 1 else [None] * count
    return normalized


def _session_labels(result: dict[str, Any], packet_format: int) -> None:
    sessions = SESSION_TYPE_NAMES_2023 if packet_format == 2023 else SESSION_TYPE_NAMES_MODERN
    games = (GAME_MODE_NAMES_2023 if packet_format == 2023 else
             GAME_MODE_NAMES_2024 if packet_format == 2024 else GAME_MODE_NAMES_2025)
    result["session_type_name"] = sessions.get(result["session_type"], f"Unknown ({result['session_type']})")
    result["game_mode_name"] = games.get(result["game_mode"], f"Unknown ({result['game_mode']})")
    result["rule_set_name"] = RULE_SET_NAMES.get(result["rule_set"], f"Unknown ({result['rule_set']})")
    result["track_name"] = TRACK_NAMES.get(result["track_id"], f"Unknown ({result['track_id']})")


def _decode_session(packet: bytes, offset: int, packet_format: int) -> tuple[dict[str, Any], int]:
    prefix: Schema = (
        ("weather", "B"), ("track_temperature", "b"), ("air_temperature", "b"),
        ("total_laps", "B"), ("track_length", "H"), ("session_type", "B"),
        ("track_id", "b"), ("formula", "B"), ("session_time_left", "H"),
        ("session_duration", "H"), ("pit_speed_limit", "B"), ("game_paused", "B"),
        ("is_spectating", "B"), ("spectator_car_index", "B"),
        ("sli_pro_native_support", "B"), ("num_marshal_zones", "B"),
    )
    result, offset = _read_schema(packet, offset, prefix)
    result["marshal_zones"] = []
    for _ in range(21):
        zone, offset = _read_schema(packet, offset, (("zone_start", "f"), ("zone_flag", "b")))
        result["marshal_zones"].append(zone)
    middle, offset = _read_schema(packet, offset, (
        ("safety_car_status", "B"), ("network_game", "B"),
        ("num_weather_forecast_samples", "B"),
    ))
    result.update(middle)
    result["weather_forecast_samples"] = []
    weather_schema: Schema = (
        ("session_type", "B"), ("time_offset", "B"), ("weather", "B"),
        ("track_temperature", "b"), ("track_temperature_change", "b"),
        ("air_temperature", "b"), ("air_temperature_change", "b"),
        ("rain_percentage", "B"),
    )
    for _ in range(56 if packet_format == 2023 else 64):
        sample, offset = _read_schema(packet, offset, weather_schema)
        result["weather_forecast_samples"].append(sample)
    common_tail: Schema = (
        ("forecast_accuracy", "B"), ("ai_difficulty", "B"),
        ("season_link_identifier", "I"), ("weekend_link_identifier", "I"),
        ("session_link_identifier", "I"), ("pit_stop_window_ideal_lap", "B"),
        ("pit_stop_window_latest_lap", "B"), ("pit_stop_rejoin_position", "B"),
        ("steering_assist", "B"), ("braking_assist", "B"), ("gearbox_assist", "B"),
        ("pit_assist", "B"), ("pit_release_assist", "B"), ("ers_assist", "B"),
        ("drs_assist", "B"), ("dynamic_racing_line", "B"),
        ("dynamic_racing_line_type", "B"), ("game_mode", "B"), ("rule_set", "B"),
        ("time_of_day", "I"), ("session_length", "B"),
        ("speed_units_lead_player", "B"), ("temperature_units_lead_player", "B"),
        ("speed_units_secondary_player", "B"), ("temperature_units_secondary_player", "B"),
        ("num_safety_car_periods", "B"), ("num_virtual_safety_car_periods", "B"),
        ("num_red_flag_periods", "B"),
    )
    tail, offset = _read_schema(packet, offset, common_tail)
    result.update(tail)
    if packet_format != 2023:
        settings: Schema = tuple((name, "B") for name in (
            "equal_car_performance", "recovery_mode", "flashback_limit", "surface_type",
            "low_fuel_mode", "race_starts", "tyre_temperature", "pit_lane_tyre_sim",
            "car_damage", "car_damage_rate", "collisions", "collisions_off_for_first_lap_only",
            "mp_unsafe_pit_release", "mp_off_for_griefing", "corner_cutting_stringency",
            "parc_ferme_rules", "pit_stop_experience", "safety_car", "safety_car_experience",
            "formation_lap", "formation_lap_experience", "red_flags",
            "affects_licence_level_solo", "affects_licence_level_mp", "num_sessions_in_weekend",
        ))
        values, offset = _read_schema(packet, offset, settings)
        result.update(values)
        weekend, offset = _read_schema(packet, offset, (
            ("weekend_structure", "12B"), ("sector2_lap_distance_start", "f"),
            ("sector3_lap_distance_start", "f"),
        ))
        result.update(weekend)
    if packet_format == 2026:
        aero, offset = _read_schema(packet, offset, (
            ("active_aero_track_status", "B"), ("num_active_aero_zones_full", "B"),
        ))
        result.update(aero)
        result["active_aero_zones_full"] = []
        for _ in range(8):
            zone, offset = _read_schema(packet, offset, (("zone_start", "f"), ("zone_end", "f")))
            result["active_aero_zones_full"].append(zone)
        value, offset = _read_schema(packet, offset, (("num_active_aero_zones_partial", "B"),))
        result.update(value)
        result["active_aero_zones_partial"] = []
        for _ in range(8):
            zone, offset = _read_schema(packet, offset, (("zone_start", "f"), ("zone_end", "f")))
            result["active_aero_zones_partial"].append(zone)
        value, offset = _read_schema(packet, offset, (("num_drs_zones", "B"),))
        result.update(value)
        result["drs_zones"] = []
        for _ in range(4):
            zone, offset = _read_schema(packet, offset, (("zone_start", "f"), ("zone_end", "f")))
            result["drs_zones"].append(zone)
        value, offset = _read_schema(packet, offset, (
            ("start_reaction_time", "f"), ("anti_lock_brakes_assist", "B"),
            ("traction_control_assist", "B"), ("dynamic_racing_line_hi_vis", "B"),
            ("dynamic_racing_line_colour_blind", "B"), ("recurring_rewind_prompt", "B"),
        ))
        result.update(value)
    _session_labels(result, packet_format)
    return result, offset


def _decode_event(packet: bytes, offset: int, packet_format: int) -> tuple[dict[str, Any], int]:
    code = bytes(packet[offset:offset + 4]).decode("ascii", errors="replace")
    offset += 4
    schemas: dict[str, Schema] = {
        "FTLP": (("vehicle_idx", "B"), ("lap_time", "f")),
        "RTMT": (("vehicle_idx", "B"),), "TMPT": (("vehicle_idx", "B"),),
        "RCWN": (("vehicle_idx", "B"),),
        "PENA": (("penalty_type", "B"), ("infringement_type", "B"),
                 ("vehicle_idx", "B"), ("other_vehicle_idx", "B"),
                 ("time", "B"), ("lap_num", "B"), ("places_gained", "B")),
        "SPTP": (("vehicle_idx", "B"), ("speed", "f"),
                 ("is_overall_fastest_in_session", "B"),
                 ("is_driver_fastest_in_session", "B"),
                 ("fastest_vehicle_idx_in_session", "B"),
                 ("fastest_speed_in_session", "f")),
        "STLG": (("num_lights", "B"),), "DTSV": (("vehicle_idx", "B"),),
        "SGSV": (("vehicle_idx", "B"),),
        "FLBK": (("flashback_frame_identifier", "I"), ("flashback_session_time", "f")),
        "BUTN": (("button_status", "I"),),
        "OVTK": (("overtaking_vehicle_idx", "B"), ("being_overtaken_vehicle_idx", "B")),
    }
    if packet_format >= 2025:
        schemas.update({
            "RTMT": (("vehicle_idx", "B"), ("reason", "B")),
            "DRSD": (("reason", "B"),),
            "SGSV": (("vehicle_idx", "B"), ("stop_time", "f")),
            "SCAR": (("safety_car_type", "B"), ("event_type", "B")),
            "COLL": (("vehicle1_idx", "B"), ("vehicle2_idx", "B")),
        })
    if packet_format == 2026:
        schemas["COLL"] = (("vehicle1_idx", "B"), ("vehicle2_idx", "B"), ("severity", "B"))
    if code in schemas:
        details, _ = _read_schema(packet, offset, schemas[code])
    else:
        details = {"raw_hex": packet[offset:].hex()}
    return {"event_code": code, "event_name": EVENT_NAMES.get(code, "Unknown Event"),
            "event_details": details}, len(packet)


def _decode_participants(packet: bytes, offset: int, profile: ProtocolProfile) -> tuple[dict, int]:
    result = {"num_active_cars": packet[offset]}
    offset += 1
    participants = []
    for car_index in range(profile.car_count):
        if profile.packet_format == 2026:
            prefix_schema = (("ai_controlled", "B"), ("driver_id", "H"),
                             ("network_id", "H"), ("team_id", "H"),
                             ("my_team", "B"), ("race_number", "B"), ("nationality", "B"))
            name_length = 32
        else:
            prefix_schema = (("ai_controlled", "B"), ("driver_id", "B"),
                             ("network_id", "B"), ("team_id", "B"),
                             ("my_team", "B"), ("race_number", "B"), ("nationality", "B"))
            name_length = 32 if profile.packet_format >= 2025 else 48
        participant, offset = _read_schema(packet, offset, prefix_schema)
        participant["name"] = _decode_name(packet[offset:offset + name_length])
        offset += name_length
        if profile.packet_format == 2023:
            tail_schema = (("your_telemetry", "B"), ("show_online_names", "B"), ("platform", "B"))
        elif profile.packet_format == 2024:
            tail_schema = (("your_telemetry", "B"), ("show_online_names", "B"),
                           ("tech_level", "H"), ("platform", "B"))
        else:
            tail_schema = (("your_telemetry", "B"), ("show_online_names", "B"),
                           ("tech_level", "H"), ("platform", "B"), ("num_colours", "B"))
        tail, offset = _read_schema(packet, offset, tail_schema)
        participant.update(tail)
        if profile.packet_format >= 2025:
            participant["livery_colours"] = []
            for _ in range(4):
                colour, offset = _read_schema(packet, offset, (("red", "B"), ("green", "B"), ("blue", "B")))
                participant["livery_colours"].append(colour)
        participant["car_index"] = car_index
        participants.append(participant)
    result["participants"] = participants
    return result, offset


def _decode_lobby(packet: bytes, offset: int, profile: ProtocolProfile) -> tuple[dict, int]:
    result = {"num_players": packet[offset]}
    offset += 1
    players = []
    for car_index in range(profile.car_count):
        team_fmt = "H" if profile.packet_format == 2026 else "B"
        player, offset = _read_schema(packet, offset, (
            ("ai_controlled", "B"), ("team_id", team_fmt),
            ("nationality", "B"), ("platform", "B"),
        ))
        name_length = 32 if profile.packet_format >= 2025 else 48
        player["name"] = _decode_name(packet[offset:offset + name_length])
        offset += name_length
        if profile.packet_format == 2023:
            tail_schema = (("car_number", "B"), ("ready_status", "B"))
        else:
            tail_schema = (("car_number", "B"), ("your_telemetry", "B"),
                           ("show_online_names", "B"), ("tech_level", "H"),
                           ("ready_status", "B"))
        tail, offset = _read_schema(packet, offset, tail_schema)
        player.update(tail)
        player["car_index"] = car_index
        players.append(player)
    result["players"] = players
    return result, offset


def decode_packet(packet: bytes, header: PacketHeader | None = None) -> dict[str, Any]:
    header = header or decode_header(packet)
    try:
        profile = protocol_for_format(header.packet_format)
    except ValueError as exc:
        raise PacketDecodeError(str(exc)) from exc
    expected = profile.packet_sizes.get(header.packet_id)
    if expected is None:
        raise PacketDecodeError(f"unknown packet id {header.packet_id} for format {header.packet_format}")
    if len(packet) != expected:
        raise PacketDecodeError(
            f"format {header.packet_format} packet {header.packet_id} has {len(packet)} bytes; expected {expected}"
        )
    result: dict[str, Any] = {"header": asdict(header), "packet_name": packet_name(header.packet_id)}
    offset = HEADER_SIZE
    packet_id = header.packet_id
    wire_schema = player_wire_schema(profile.packet_format, packet_id)

    if packet_id == 0:
        result["cars"], offset = _read_records(packet, offset, MOTION_SCHEMA, profile.car_count)
    elif packet_id == 1:
        values, offset = _decode_session(packet, offset, profile.packet_format)
        result.update(values)
    elif packet_id == 2:
        result["cars"], offset = _read_records(packet, offset, wire_schema, profile.car_count)
        tail, offset = _read_schema(packet, offset, (("time_trial_pb_car_idx", "B"), ("time_trial_rival_car_idx", "B")))
        result.update(tail)
    elif packet_id == 3:
        values, offset = _decode_event(packet, offset, profile.packet_format)
        result.update(values)
    elif packet_id == 4:
        values, offset = _decode_participants(packet, offset, profile)
        result.update(values)
    elif packet_id == 5:
        result["cars"], offset = _read_records(packet, offset, wire_schema, profile.car_count)
        if profile.packet_format != 2023:
            tail, offset = _read_schema(packet, offset, (("next_front_wing_value", "f"),))
            result.update(tail)
    elif packet_id == 6:
        result["cars"], offset = _read_records(packet, offset, CAR_TELEMETRY_SCHEMA, profile.car_count)
        tail, offset = _read_schema(packet, offset, (
            ("mfd_panel_index", "B"), ("mfd_panel_index_secondary_player", "B"),
            ("suggested_gear", "b"),
        ))
        result.update(tail)
    elif packet_id == 7:
        result["cars"], offset = _read_records(packet, offset, wire_schema, profile.car_count)
    elif packet_id == 8:
        result["num_cars"] = packet[offset]
        offset += 1
        schema = FINAL_CLASSIFICATION_SCHEMA_2025 if profile.packet_format >= 2025 else FINAL_CLASSIFICATION_SCHEMA
        result["classification"], offset = _read_records(packet, offset, schema, profile.car_count)
    elif packet_id == 9:
        values, offset = _decode_lobby(packet, offset, profile)
        result.update(values)
    elif packet_id == 10:
        result["cars"], offset = _read_records(packet, offset, wire_schema, profile.car_count)
    elif packet_id == 11:
        prefix, offset = _read_schema(packet, offset, (
            ("car_idx", "B"), ("num_laps", "B"), ("num_tyre_stints", "B"),
            ("best_lap_time_lap_num", "B"), ("best_sector1_lap_num", "B"),
            ("best_sector2_lap_num", "B"), ("best_sector3_lap_num", "B"),
        ))
        result.update(prefix)
        result["lap_history"], offset = _read_records(packet, offset, LAP_HISTORY_SCHEMA, 100, "lap_index")
        result["tyre_stints"] = []
        for stint_index in range(8):
            stint, offset = _read_schema(packet, offset, (
                ("end_lap", "B"), ("tyre_actual_compound", "B"), ("tyre_visual_compound", "B"),
            ))
            stint["stint_index"] = stint_index
            result["tyre_stints"].append(stint)
    elif packet_id == 12:
        result["car_idx"] = packet[offset]
        offset += 1
        result["tyre_sets"] = []
        for set_index in range(20):
            tyre_set, offset = _read_schema(packet, offset, TYRE_SET_SCHEMA)
            tyre_set["set_index"] = set_index
            result["tyre_sets"].append(tyre_set)
        result["fitted_idx"] = packet[offset]
        offset += 1
    elif packet_id == 13:
        values, offset = _read_schema(packet, offset, wire_schema)
        result.update(values)
    elif packet_id == 14:
        schema = TIME_TRIAL_SCHEMA_2026 if profile.packet_format == 2026 else TIME_TRIAL_SCHEMA
        for key in ("player_session_best", "personal_best", "rival"):
            result[key], offset = _read_schema(packet, offset, schema)
    elif packet_id == 15:
        prefix, offset = _read_schema(packet, offset, (("num_laps", "B"), ("lap_start", "B")))
        result.update(prefix)
        result["positions"] = []
        for lap_index in range(50):
            positions = list(packet[offset:offset + profile.car_count])
            offset += profile.car_count
            result["positions"].append({"lap_index": lap_index, "car_positions": positions})
    elif packet_id == 16:
        result["cars"], offset = _read_records(packet, offset, CAR_TELEMETRY_2_SCHEMA, profile.car_count)

    if offset != len(packet):
        raise PacketDecodeError(
            f"decoder consumed {offset} of {len(packet)} bytes for format {header.packet_format} packet {packet_id}"
        )
    return result
