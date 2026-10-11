"""Independent Season Pack wire fixtures; do not derive lengths from the decoder."""
import struct
import unittest
import sqlite3
import json
import tempfile
from pathlib import Path
from contextlib import closing

from analysis.schema import create_schema
from analysis.resample import resample_laps
from analysis.incremental import update_analysis, analysis_path
from analysis.quality import inspect_source
from analysis.resample import _source_rows
from analysis.comparison import AnalysisRepository
from analysis.practice import practice_report
from storage.foundation import foundation_path
from tests.test_analysis_pipeline import session_packet
from tests.test_foundation import write_packets

from decoder.full_parser import decode_packet
from decoder.header import HEADER_STRUCT, decode_header
from decoder.stream import decode_player_record


def wire_packet(packet_id, length, player=23):
    data = bytearray(length)
    HEADER_STRUCT.pack_into(data, 0, 2026, 25, 1, 26, 1, packet_id,
                            123, 1.0, 10, 10, player, 255)
    return data


class SeasonPackWireTests(unittest.TestCase):
    def test_motion_54_byte_car_has_scaled_signed_g_and_last_car(self):
        data = wire_packet(0, 1325)
        struct.pack_into('<6f9h3f', data, 29 + 23 * 54,
                         1, 2, 3, 4, 5, 6, 32767, 0, 0, 0, 32767, 0,
                         1234, -2500, 1000, .1, .2, .3)
        full = decode_packet(bytes(data))['cars']
        player = decode_player_record(bytes(data), decode_header(data))
        self.assertEqual(len(full), 24)
        self.assertEqual({**player, 'car_index': 23}, full[23])
        self.assertEqual(player['g_force_lateral'], 1.234)
        self.assertEqual(player['g_force_longitudinal'], -2.5)
        self.assertEqual(player['g_force_vertical'], 1.0)
        self.assertEqual(full[0]['world_position_x'], 0)

    def test_telemetry_59_byte_car_uint8_engine_temperature(self):
        data = wire_packet(6, 1448)
        struct.pack_into('<HfffBbHBBH4H4B4BB4f4B', data, 29 + 23 * 59,
                         321, .75, -.25, .5, 0, 8, 12000, 1, 90, 1023,
                         700, 701, 702, 703, 90, 91, 92, 93,
                         80, 81, 82, 83, 110, 22, 23, 24, 25, 0, 1, 2, 3)
        full = decode_packet(bytes(data))['cars']
        player = decode_player_record(bytes(data), decode_header(data))
        self.assertEqual({**player, 'car_index': 23}, full[23])
        self.assertEqual(player['engine_temperature'], 110)
        self.assertEqual(player['tyres_pressure'], [22, 23, 24, 25])
        self.assertEqual(player['surface_type'], [0, 1, 2, 3])
        self.assertEqual(player['speed'], 321)


class TimeTrialAttemptTests(unittest.TestCase):
    def run_attempts(self, trailing=False, matching=True, session_type=18, old_issue=False):
        con = sqlite3.connect(':memory:')
        self.addCleanup(con.close)
        create_schema(con)
        con.execute("INSERT INTO sessions(session_uid,session_type_id,game_mode_id,track_length_m) VALUES ('7',?,?,1000)",
                    (session_type, 5 if session_type == 18 else 3))
        con.execute("INSERT INTO laps VALUES ('7',2,90000,30000,30000,30000,1,1,1,1,1)")
        # First attempt reaches an artificial finish on garage reset, not a completed lap.
        rows = [(d, d * 90, 1) for d in range(0, 901, 10)] + [(1001, 0, 0)]
        # Final completion has slightly less geometric coverage, but agrees with official timing.
        rows += [(d + 1, d * (90 if matching else 80), 1) for d in range(0, 1001, 10)]
        if trailing:
            rows += [(1001, 0, 0)] + [(d + 2, d * 90, 1) for d in range(0, 201, 10)]
        for frame, (distance, clock, driver) in enumerate(rows, 1):
            con.execute("INSERT INTO sample_origins VALUES ('7',?,?,?)", (frame,frame*1000,frame*1000+999))
            con.execute("""INSERT INTO telemetry_samples(session_uid,overall_frame_identifier,
                frame_identifier,received_at_ns,session_time,player_car_index,lap_number,
                lap_distance_m,total_distance_m,current_lap_time_ms,sector,current_lap_invalid,
                pit_status,driver_status,speed_kph,throttle,brake)
                VALUES ('7',?,?,?, ?,0,2,?,?,?,0,0,0,?,40,1,0)""",
                (frame, frame, frame * 900000000, frame * .9, distance, distance, clock, driver))
        if old_issue:
            con.execute("INSERT INTO frame_quality_issues(session_uid,overall_frame_identifier,lap_number,missing_parts_json) VALUES ('7',30,2,'[0]')")
        resample_laps(con, distance_step_m=10)
        return con

    def test_latest_completed_attempt_not_greatest_coverage(self):
        con = self.run_attempts()
        self.assertEqual(con.execute('SELECT quality_status FROM lap_analysis').fetchone()[0], 'ready')
        details = json.loads(con.execute('SELECT details_json FROM lap_quality_details').fetchone()[0])
        self.assertEqual(details['end_time_ms'], 90000)
        self.assertEqual(details['source_frame_range'], [93, 193])

    def test_selected_quality_origins_do_not_include_abandoned_attempt(self):
        con = self.run_attempts(old_issue=True)
        details = json.loads(con.execute('SELECT details_json FROM lap_quality_details').fetchone()[0])
        self.assertEqual(details['incomplete_frame_count'], 0)
        self.assertEqual(details['raw_offset_range'], [93000, 193999])

    def test_unfinished_retry_does_not_replace_matching_completed_attempt(self):
        con = self.run_attempts(trailing=True)
        self.assertEqual(con.execute('SELECT quality_status FROM lap_analysis').fetchone()[0], 'ready')

    def test_unmatched_official_time_cannot_be_presented_as_ready(self):
        con = self.run_attempts(matching=False)
        self.assertNotEqual(con.execute('SELECT quality_status FROM lap_analysis').fetchone()[0], 'ready')
        quality = inspect_source(_source_rows(con, '7', 2, 1000))
        self.assertEqual(quality.status, 'unverified_attempt')
        self.assertFalse(quality.supports(500))
        self.assertFalse(quality.connects(400, 500))

    def test_practice_uses_selected_attempt_not_earlier_garage_flags(self):
        con = self.run_attempts()
        con.commit()
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / 'analysis.db'
            with closing(sqlite3.connect(database)) as target:
                con.backup(target)
            report = practice_report(AnalysisRepository(database), '7')
            self.assertTrue(report['rows'][0]['eligible'], report['rows'][0]['reasons'])

    def test_modern_short_sprint_shootout_id13_is_not_time_trial(self):
        con = self.run_attempts(session_type=13)
        self.assertEqual(con.execute('SELECT quality_status FROM lap_analysis').fetchone()[0], 'nonmonotonic_time')

    def test_flying_lap_cadence_is_measured(self):
        rows = [dict(lap_distance_m=i, current_lap_time_ms=i*16, session_time=i*.016,
                     speed_kph=225, throttle=1, brake=0, driver_status=1, pit_status=0)
                for i in range(50)]
        self.assertAlmostEqual(inspect_source(rows).cadence_ms, 16)


class ReleaseCacheTests(unittest.TestCase):
    def test_new_default_paths_preserve_old_error_caches(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            raw = root / 'raw_packets.bin'
            old_foundation = root / 'foundation/raw_packets.foundation-v2.db'
            old_analysis = root / 'analysis_v1.0.3/telemetry_analysis.db'
            for path in (old_foundation, old_analysis):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'old incompatible user cache')
            write_packets(raw, [session_packet()])
            result = update_analysis(raw)
            self.assertEqual(Path(result['analysis_database']), analysis_path(raw))
            self.assertEqual(analysis_path(raw).parent.name, 'analysis_v2.0')
            self.assertTrue(foundation_path(raw).name.endswith('foundation-v3.db'))
            for path in (old_foundation, old_analysis):
                self.assertEqual(path.read_bytes(), b'old incompatible user cache')
