from __future__ import annotations

from dataclasses import asdict
from contextlib import closing
from pathlib import Path
import sqlite3
import struct
import tempfile
import unittest

from analysis.build import build_analysis
from analysis.comparison import AnalysisRepository
from collector.analysis_gui import lap_label
from decoder.full_parser import player_wire_schema
from decoder.header import HEADER_STRUCT, PacketHeader, decode_header
from decoder.protocol import PROTOCOLS
from decoder.session_history import PlayerSessionHistoryTracker
from decoder.tyres import PlayerTyreTracker, tyre_name
from storage.raw_writer import RawPacketWriter
from tests.test_analysis_pipeline import session_packet, motion_packet, lap_packet, telemetry_packet


def player_packet(packet_id, frame, values, packet_format=2023, uid=77, player=0):
    packet = bytearray(PROTOCOLS[packet_format].packet_sizes[packet_id])
    HEADER_STRUCT.pack_into(packet, 0, packet_format, packet_format % 100, 1, 0, 1,
                            packet_id, uid, frame / 60, frame, frame, player, 255)
    schema = player_wire_schema(packet_format, packet_id)
    size = sum(struct.calcsize("<" + kind) for _, kind in schema)
    offset = HEADER_STRUCT.size + player * size
    for name, kind in schema:
        if name in values:
            value = values[name]
            struct.pack_into("<" + kind, packet, offset, *(value if isinstance(value, (tuple, list)) else (value,)))
        offset += struct.calcsize("<" + kind)
    return bytes(packet)


def history_packet(times, stints, frame=100, packet_format=2023, uid=77, player=0):
    packet = bytearray(PROTOCOLS[packet_format].packet_sizes[11])
    HEADER_STRUCT.pack_into(packet, 0, packet_format, packet_format % 100, 1, 0, 1,
                            11, uid, frame / 60, frame, frame, player, 255)
    struct.pack_into("<7B", packet, HEADER_STRUCT.size, player, len(times), len(stints), 1, 1, 1, 1)
    lap_struct = struct.Struct("<IHBHBHBB")
    for index, value in enumerate(times):
        lap_struct.pack_into(packet, HEADER_STRUCT.size + 7 + index * lap_struct.size,
                             value, 3000, 0, 3000, 0, max(0, value - 6000), 0, 15)
    for index, (end, actual, visual) in enumerate(stints):
        struct.pack_into("<3B", packet, HEADER_STRUCT.size + 7 + 100 * lap_struct.size + index * 3,
                         end, actual, visual)
    return bytes(packet)


def header(packet_id, frame, time=None):
    return PacketHeader(2023, 23, 1, 0, 1, packet_id, 77,
                        frame / 60 if time is None else time, frame, frame, 0, 255)


class TyreTests(unittest.TestCase):
    def test_visual_names_do_not_confuse_c_compounds_with_soft_medium_hard(self):
        self.assertEqual(tyre_name(16, 18), "软胎")
        self.assertEqual(tyre_name(17, 16), "中性胎")
        self.assertEqual(tyre_name(None, 18), "C3")
        self.assertEqual(tyre_name(7), "半雨胎")
        self.assertEqual(tyre_name(8), "全雨胎")
        self.assertEqual(tyre_name(19, 11), "超软胎")

    def test_same_compound_new_set_resets_independent_lap_counter_all_protocols(self):
        for packet_format in PROTOCOLS:
            with self.subTest(packet_format=packet_format):
                tracker = PlayerSessionHistoryTracker()
                update = tracker.observe(history_packet([10000] * 4,
                    [(2, 18, 16), (255, 18, 16)], packet_format=packet_format), 1)
                self.assertEqual([lap.tyre.tyre_lap_number for lap in update.laps], [1, 2, 1, 2])
                self.assertEqual([lap.tyre.stint_number for lap in update.laps], [1, 1, 2, 2])
                self.assertTrue(all(lap.tyre.wear_percent is None for lap in update.laps))

    def test_completed_wear_is_average_and_is_frozen_before_pit_change(self):
        tracker = PlayerSessionHistoryTracker()
        packets = [player_packet(7, 1, {"actual_tyre_compound": 18, "visual_tyre_compound": 16}),
                   player_packet(10, 10, {"tyres_wear": [6, 8, 10, 12]}),
                   player_packet(2, 10, {"current_lap_num": 1}),
                   player_packet(2, 11, {"current_lap_num": 2}),
                   player_packet(7, 12, {"actual_tyre_compound": 17, "visual_tyre_compound": 17}),
                   player_packet(10, 12, {"tyres_wear": [0, 0, 0, 0]}),
                   history_packet([10000], [(1, 18, 16), (255, 17, 17)], frame=13)]
        update = None
        for index, packet in enumerate(packets):
            result = tracker.observe(packet, index)
            update = result or update
        self.assertEqual(update.laps[0].tyre.wear_percent, 9)
        self.assertEqual(update.laps[0].tyre.name, "软胎")
        self.assertEqual(update.laps[0].tyre.wear_frame_identifier, 10)

    def test_flashback_removes_future_wear_and_replaces_completion(self):
        tracker = PlayerTyreTracker()
        tracker.observe(header(7, 1), {"actual_tyre_compound": 18, "visual_tyre_compound": 16, "tyres_age_laps": 0})
        tracker.observe(header(2, 10), {"current_lap_num": 1})
        tracker.observe(header(10, 10), {"tyres_wear": [88] * 4})
        tracker.observe(header(2, 11), {"current_lap_num": 2})
        tracker.observe(header(3, 12), {"event_code": "FLBK", "event_details": {
            "flashback_frame_identifier": 5, "flashback_session_time": 5 / 60}})
        self.assertEqual(tracker.invalidated_laps, {1})
        tracker.observe(header(2, 6), {"current_lap_num": 1})
        tracker.observe(header(10, 9), {"tyres_wear": [8] * 4})
        tracker.observe(header(2, 10), {"current_lap_num": 1})
        tracker.observe(header(2, 11), {"current_lap_num": 2})
        result = tracker.laps({"num_laps": 1, "lap_history": [{"lap_time_ms": 10000}],
                               "num_tyre_stints": 1, "tyre_stints": [{
                                   "end_lap": 255, "tyre_actual_compound": 18, "tyre_visual_compound": 16}]})
        self.assertEqual(result[1].wear_percent, 8)
        self.assertEqual(result[1].tyre_lap_number, 1)

    def test_missing_stale_invalid_and_ai_data_never_become_fake_wear(self):
        tracker = PlayerTyreTracker()
        tracker.observe(header(10, 1), {"tyres_wear": [float("nan"), 1, 2, 3]})
        tracker.observe(header(10, 2), {"tyres_wear": [3] * 4})
        tracker.observe(header(2, 400), {"current_lap_num": 1})
        tracker.observe(header(2, 401), {"current_lap_num": 2})
        history = {"num_laps": 1, "lap_history": [{"lap_time_ms": 10000}]}
        self.assertIsNone(tracker.laps(history)[1].wear_percent)
        live = PlayerSessionHistoryTracker()
        self.assertIsNone(live.observe(history_packet([10000], [(255, 18, 16)], player=1)[:], 1,
                                      header=PacketHeader(2023, 23, 1, 0, 1, 11, 77, 1, 60, 60, 0, 255)))

    def test_tyre_sets_fallback_and_late_damage_packet_refine_completed_lap(self):
        tracker = PlayerTyreTracker()
        tracker.observe(header(2, 10), {"current_lap_num": 1})
        tracker.observe(header(12, 9), {"car_idx": 0, "fitted_idx": 0, "tyre_sets": [{
            "actual_tyre_compound": 18, "visual_tyre_compound": 16, "wear": 12}]})
        tracker.observe(header(2, 11), {"current_lap_num": 2})
        history = {"num_laps": 1, "lap_history": [{"lap_time_ms": 10000}]}
        self.assertEqual(tracker.laps(history)[1].wear_source, "tyre_sets")
        self.assertTrue(tracker.observe(header(10, 10), {"tyres_wear": [10, 12, 14, 16]}))
        self.assertEqual(tracker.laps(history)[1].wear_percent, 13)
        self.assertEqual(tracker.laps(history)[1].wear_source, "car_damage_average")

    def test_lagging_history_does_not_erase_new_completion(self):
        tracker = PlayerSessionHistoryTracker()
        for packet in (player_packet(7, 1, {"actual_tyre_compound": 18, "visual_tyre_compound": 16}),
                       player_packet(10, 10, {"tyres_wear": [8] * 4}),
                       player_packet(2, 10, {"current_lap_num": 1}),
                       player_packet(2, 11, {"current_lap_num": 2}),
                       history_packet([0], [(255, 18, 16)], frame=12)):
            tracker.observe(packet, 1)
        update = tracker.observe(history_packet([10000], [(255, 18, 16)], frame=13), 2)
        self.assertEqual(update.laps[0].tyre.wear_percent, 8)

    def test_observed_compound_corrects_practice_history_boundary_disagreement(self):
        tracker = PlayerTyreTracker()
        for lap, compound in ((1, (18, 17)), (2, (18, 17)), (3, (17, 16))):
            frame = lap * 10
            tracker.observe(header(7, frame), {"actual_tyre_compound": compound[0], "visual_tyre_compound": compound[1]})
            tracker.observe(header(10, frame), {"tyres_wear": [float(lap)] * 4})
            tracker.observe(header(2, frame), {"current_lap_num": lap})
            tracker.observe(header(2, frame + 1), {"current_lap_num": lap + 1})
        result = tracker.laps({"num_laps": 3, "lap_history": [{"lap_time_ms": 10000}] * 3,
            "num_tyre_stints": 2, "tyre_stints": [
                {"end_lap": 1, "tyre_actual_compound": 18, "tyre_visual_compound": 17},
                {"end_lap": 255, "tyre_actual_compound": 17, "tyre_visual_compound": 16}]})
        self.assertEqual([result[n].name for n in (1, 2, 3)], ["中性胎", "中性胎", "软胎"])
        self.assertEqual([result[n].tyre_lap_number for n in (1, 2, 3)], [1, 2, 1])
        self.assertEqual(result[2].wear_percent, 2)

    def test_same_compound_sets_fallback_and_missing_compound_in_history(self):
        tracker = PlayerTyreTracker()
        for lap, fitted, age in [(1, 0, 1), (2, 1, 0)]:
            frame = lap * 10
            tracker.observe(header(7, frame), {"actual_tyre_compound": 18, "visual_tyre_compound": 16,
                                              "tyres_age_laps": age})
            tracker.observe(header(12, frame), {"car_idx": 0, "fitted_idx": fitted, "tyre_sets": [
                {"actual_tyre_compound": 18, "visual_tyre_compound": 16, "wear": 0}] * 2})
            tracker.observe(header(2, frame), {"current_lap_num": lap})
            tracker.observe(header(2, frame + 1), {"current_lap_num": lap + 1})
        info = tracker.laps({"num_laps": 2, "lap_history": [{"lap_time_ms": 10000}] * 2})
        self.assertEqual([info[n].tyre_lap_number for n in (1, 2)], [1, 1])

    def test_replay_and_foundation_generate_identical_tyre_metadata_and_labels(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = root / "raw_packets.bin"
            packets = [session_packet(), player_packet(7, 0, {
                "actual_tyre_compound": 18, "visual_tyre_compound": 16})]
            for frame, distance in enumerate(range(0, 101, 10), 1):
                packets.extend([motion_packet(frame, frame, float(distance)),
                    lap_packet(frame, frame, float(distance)),
                    telemetry_packet(frame, frame, float(distance), False),
                    player_packet(10, frame, {"tyres_wear": [6, 8, 10, 12]})])
            packets.append(history_packet([10000], [(255, 18, 16)], frame=11))
            live = PlayerSessionHistoryTracker()
            with RawPacketWriter(raw, flush_every=1, compression="zlib") as writer:
                for index, packet in enumerate(packets):
                    writer.write(packet, 1800000000000000000 + index, index, "127.0.0.1", 20777)
                    update = live.observe(packet, index)
            expected = live._tyres.laps(live._history)[1]
            records = []
            for foundation in (False, True):
                summary = build_analysis(root, output=root / str(foundation), use_foundation=foundation, progress_every=0)
                self.assertEqual(summary["decode_error_count"], 0)
                repo = AnalysisRepository(Path(summary["analysis_database"]))
                lap = repo.laps("77")[0]
                records.append(asdict(lap.tyre))
                self.assertEqual(lap.tyre.wear_percent, 9)
                self.assertIn("软胎 第1圈（0:10.000）", lap_label(lap))
                self.assertIn("总第1圈", lap_label(lap))
            self.assertEqual(records[0], records[1])
            self.assertEqual(records[0], asdict(expected))
            # Old derived databases remain read-only and usable without tyre columns.
            database = root / "False" / "telemetry_analysis.db"
            with closing(sqlite3.connect(database)) as connection:
                connection.execute("DROP TABLE lap_tyres")
                connection.commit()
            old = AnalysisRepository(database)
            self.assertFalse(old.has_tyre_metadata)
            self.assertIn("第 1 圈", lap_label(old.laps("77")[0]))


if __name__ == "__main__":
    unittest.main()
