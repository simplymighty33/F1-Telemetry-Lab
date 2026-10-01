"""SQLite schema for portable Phase 2 analysis output."""

from __future__ import annotations

import sqlite3


SCHEMA_VERSION = 3


def create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE sessions (
            session_uid TEXT PRIMARY KEY,
            game_mode TEXT,
            game_mode_id INTEGER,
            session_type TEXT,
            session_type_id INTEGER,
            track TEXT,
            track_id INTEGER,
            track_length_m INTEGER,
            total_laps INTEGER,
            first_received_at_ns INTEGER,
            last_received_at_ns INTEGER
        );
        CREATE TABLE events (
            id INTEGER PRIMARY KEY,
            session_uid TEXT NOT NULL,
            received_at_ns INTEGER NOT NULL,
            session_time REAL NOT NULL,
            frame_identifier INTEGER NOT NULL,
            overall_frame_identifier INTEGER NOT NULL,
            event_code TEXT NOT NULL,
            event_name TEXT NOT NULL,
            event_details_json TEXT NOT NULL
        );
        CREATE TABLE laps (
            session_uid TEXT NOT NULL,
            lap_number INTEGER NOT NULL,
            lap_time_ms INTEGER NOT NULL,
            sector1_ms INTEGER NOT NULL,
            sector2_ms INTEGER NOT NULL,
            sector3_ms INTEGER NOT NULL,
            lap_valid INTEGER NOT NULL,
            sector1_valid INTEGER NOT NULL,
            sector2_valid INTEGER NOT NULL,
            sector3_valid INTEGER NOT NULL,
            snapshot_received_at_ns INTEGER NOT NULL,
            PRIMARY KEY (session_uid, lap_number)
        );
        CREATE TABLE lap_tyres (
            session_uid TEXT NOT NULL,
            lap_number INTEGER NOT NULL,
            actual_compound INTEGER,
            visual_compound INTEGER,
            stint_number INTEGER,
            tyre_lap_number INTEGER,
            fitted_set_index INTEGER,
            wear_percent REAL,
            wear_rl REAL, wear_rr REAL, wear_fl REAL, wear_fr REAL,
            wear_source TEXT,
            wear_frame_identifier INTEGER,
            wear_session_time REAL,
            tyres_age_laps INTEGER,
            compound_source TEXT,
            PRIMARY KEY (session_uid, lap_number),
            FOREIGN KEY (session_uid, lap_number) REFERENCES laps(session_uid, lap_number)
        );
        CREATE TABLE telemetry_samples (
            id INTEGER PRIMARY KEY,
            session_uid TEXT NOT NULL,
            overall_frame_identifier INTEGER NOT NULL,
            frame_identifier INTEGER NOT NULL,
            received_at_ns INTEGER NOT NULL,
            session_time REAL NOT NULL,
            player_car_index INTEGER NOT NULL,
            lap_number INTEGER NOT NULL,
            lap_distance_m REAL NOT NULL,
            total_distance_m REAL NOT NULL,
            current_lap_time_ms INTEGER NOT NULL,
            sector INTEGER NOT NULL,
            current_lap_invalid INTEGER NOT NULL,
            pit_status INTEGER NOT NULL,
            driver_status INTEGER NOT NULL,
            superseded INTEGER NOT NULL DEFAULT 0,
            world_x REAL, world_y REAL, world_z REAL,
            velocity_x REAL, velocity_y REAL, velocity_z REAL,
            g_lateral REAL, g_longitudinal REAL, g_vertical REAL,
            yaw REAL, pitch REAL, roll REAL,
            speed_kph INTEGER, throttle REAL, brake REAL, steer REAL,
            clutch INTEGER, gear INTEGER, engine_rpm INTEGER, drs INTEGER,
            brake_temp_rl INTEGER, brake_temp_rr INTEGER,
            brake_temp_fl INTEGER, brake_temp_fr INTEGER,
            tyre_surface_temp_rl INTEGER, tyre_surface_temp_rr INTEGER,
            tyre_surface_temp_fl INTEGER, tyre_surface_temp_fr INTEGER,
            tyre_inner_temp_rl INTEGER, tyre_inner_temp_rr INTEGER,
            tyre_inner_temp_fl INTEGER, tyre_inner_temp_fr INTEGER,
            tyre_pressure_rl REAL, tyre_pressure_rr REAL,
            tyre_pressure_fl REAL, tyre_pressure_fr REAL,
            surface_type_rl INTEGER, surface_type_rr INTEGER,
            surface_type_fl INTEGER, surface_type_fr INTEGER,
            fuel_in_tank REAL, fuel_remaining_laps REAL,
            actual_tyre_compound INTEGER, visual_tyre_compound INTEGER,
            tyres_age_laps INTEGER, ers_store_energy REAL,
            ers_deploy_mode INTEGER, ers_deployed_this_lap REAL,
            tyre_wear_rl REAL, tyre_wear_rr REAL,
            tyre_wear_fl REAL, tyre_wear_fr REAL,
            tyre_damage_rl INTEGER, tyre_damage_rr INTEGER,
            tyre_damage_fl INTEGER, tyre_damage_fr INTEGER,
            front_left_wing_damage INTEGER, front_right_wing_damage INTEGER,
            rear_wing_damage INTEGER, floor_damage INTEGER,
            wheel_speed_rl REAL, wheel_speed_rr REAL,
            wheel_speed_fl REAL, wheel_speed_fr REAL,
            wheel_slip_ratio_rl REAL, wheel_slip_ratio_rr REAL,
            wheel_slip_ratio_fl REAL, wheel_slip_ratio_fr REAL,
            wheel_slip_angle_rl REAL, wheel_slip_angle_rr REAL,
            wheel_slip_angle_fl REAL, wheel_slip_angle_fr REAL,
            front_wheels_angle REAL,
            UNIQUE (session_uid, overall_frame_identifier)
        );
        CREATE TABLE lap_analysis (
            session_uid TEXT NOT NULL,
            lap_number INTEGER NOT NULL,
            source_sample_count INTEGER NOT NULL,
            resampled_point_count INTEGER NOT NULL,
            min_distance_m REAL,
            max_distance_m REAL,
            coverage_ratio REAL NOT NULL,
            distance_step_m REAL NOT NULL,
            quality_status TEXT NOT NULL,
            PRIMARY KEY (session_uid, lap_number)
        );
        CREATE TABLE resampled_lap_samples (
            session_uid TEXT NOT NULL,
            lap_number INTEGER NOT NULL,
            distance_m REAL NOT NULL,
            lap_time_ms REAL,
            speed_kph REAL,
            throttle REAL,
            brake REAL,
            steer REAL,
            gear INTEGER,
            engine_rpm REAL,
            drs INTEGER,
            g_lateral REAL,
            g_longitudinal REAL,
            world_x REAL, world_y REAL, world_z REAL,
            fuel_in_tank REAL,
            ers_store_energy REAL,
            tyre_wear_rl REAL, tyre_wear_rr REAL,
            tyre_wear_fl REAL, tyre_wear_fr REAL,
            front_wheels_angle REAL,
            wheel_slip_ratio_rl REAL, wheel_slip_ratio_rr REAL,
            wheel_slip_ratio_fl REAL, wheel_slip_ratio_fr REAL,
            PRIMARY KEY (session_uid, lap_number, distance_m)
        );
        CREATE TABLE braking_events (
            session_uid TEXT NOT NULL,
            lap_number INTEGER NOT NULL,
            event_index INTEGER NOT NULL,
            start_distance_m REAL NOT NULL,
            peak_distance_m REAL NOT NULL,
            end_distance_m REAL NOT NULL,
            start_time_ms REAL,
            end_time_ms REAL,
            duration_ms REAL,
            entry_speed_kph REAL,
            minimum_speed_kph REAL,
            exit_speed_kph REAL,
            peak_brake REAL NOT NULL,
            minimum_longitudinal_g REAL,
            release_distance_m REAL,
            release_smoothness REAL,
            steer_at_peak REAL,
            PRIMARY KEY (session_uid, lap_number, event_index)
        );
        CREATE TABLE throttle_events (
            session_uid TEXT NOT NULL,
            lap_number INTEGER NOT NULL,
            event_index INTEGER NOT NULL,
            start_distance_m REAL NOT NULL,
            end_distance_m REAL NOT NULL,
            start_time_ms REAL,
            end_time_ms REAL,
            duration_ms REAL,
            start_speed_kph REAL,
            end_speed_kph REAL,
            start_throttle REAL,
            end_throttle REAL,
            average_rise_per_second REAL,
            PRIMARY KEY (session_uid, lap_number, event_index)
        );
        CREATE TABLE gear_shift_events (
            session_uid TEXT NOT NULL,
            lap_number INTEGER NOT NULL,
            event_index INTEGER NOT NULL,
            distance_m REAL NOT NULL,
            lap_time_ms REAL,
            direction TEXT NOT NULL,
            from_gear INTEGER NOT NULL,
            to_gear INTEGER NOT NULL,
            rpm_before REAL,
            rpm_after REAL,
            throttle REAL,
            brake REAL,
            PRIMARY KEY (session_uid, lap_number, event_index)
        );
        CREATE TABLE lap_metrics (
            session_uid TEXT NOT NULL,
            lap_number INTEGER NOT NULL,
            lap_time_ms INTEGER NOT NULL,
            delta_to_best_ms INTEGER NOT NULL,
            full_throttle_percent REAL NOT NULL,
            braking_percent REAL NOT NULL,
            coasting_percent REAL NOT NULL,
            brake_throttle_overlap_percent REAL NOT NULL,
            maximum_speed_kph REAL,
            minimum_speed_kph REAL,
            average_speed_kph REAL,
            braking_event_count INTEGER NOT NULL,
            throttle_event_count INTEGER NOT NULL,
            upshift_count INTEGER NOT NULL,
            downshift_count INTEGER NOT NULL,
            steering_correction_count INTEGER NOT NULL,
            steering_total_variation_per_km REAL NOT NULL,
            throttle_total_variation_per_km REAL NOT NULL,
            average_peak_brake REAL,
            average_brake_release_smoothness REAL,
            PRIMARY KEY (session_uid, lap_number)
        );
        CREATE TABLE session_metrics (
            session_uid TEXT PRIMARY KEY,
            valid_lap_count INTEGER NOT NULL,
            best_lap_number INTEGER,
            best_lap_time_ms INTEGER,
            best_sector1_ms INTEGER,
            best_sector2_ms INTEGER,
            best_sector3_ms INTEGER,
            theoretical_best_ms INTEGER,
            best_lap_gap_to_theoretical_ms INTEGER
        );
        CREATE INDEX telemetry_lap_distance_idx
            ON telemetry_samples(session_uid, lap_number, superseded, lap_distance_m);
        CREATE INDEX telemetry_frame_idx
            ON telemetry_samples(session_uid, frame_identifier, superseded);
        """
    )
