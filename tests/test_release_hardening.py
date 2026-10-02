from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
import json
import tkinter as tk
import time
import shutil
import struct
from unittest.mock import patch

from analysis.build import build_analysis
from analysis.incremental import update_analysis, IncrementalAnalysisStore
from decoder.full_parser import compiled_schema, LAP_SCHEMA, CAR_TELEMETRY_SCHEMA
from decoder.header import HEADER_SIZE
from tests.test_analysis_pipeline import session_packet, motion_packet, lap_packet, telemetry_packet, history_packet
from tests.test_foundation import write_packets, table_rows
from storage.foundation import ensure_foundation


def other_player(packet, schema):
    changed = bytearray(packet)
    stride = compiled_schema(schema)[0].size
    changed[HEADER_SIZE + stride:HEADER_SIZE + stride * 2] = packet[HEADER_SIZE:HEADER_SIZE + stride]
    changed[27] = 1
    return bytes(changed)


class FrameIdentityTests(unittest.TestCase):
    def test_protocol_and_frame_mismatch_survive_incremental_restart(self):
        from tests.test_tyres import player_packet
        cases = [player_packet(0, 1, {}, packet_format=2024), motion_packet(2, 1, 20)]
        for motion in cases:
            with self.subTest(case=motion[:3]), tempfile.TemporaryDirectory() as temporary:
                folder = Path(temporary)
                raw = folder/'raw_packets.bin'
                write_packets(raw, [session_packet(), motion, lap_packet(1, 1, 20),
                                    telemetry_packet(1, 1, 20, False), history_packet(3)])
                ensure_foundation(raw)
                database = folder/'incremental.db'
                with IncrementalAnalysisStore(raw, database) as store:
                    store.update(batch_size=3)
                with IncrementalAnalysisStore(raw, database) as store:
                    store.update(final=True)
                self.assertEqual(table_rows(database, 'telemetry_samples'), [])
                self.assertTrue(table_rows(database, 'frame_quality_issues'))

    def test_duplicate_channels_still_produce_one_sample(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            raw = folder/'raw_packets.bin'
            write_packets(raw, [session_packet(), motion_packet(1, 1, 20), motion_packet(1, 1, 20),
                lap_packet(1, 1, 20), telemetry_packet(1, 1, 20, False), history_packet(3)])
            database = Path(update_analysis(raw)['analysis_database'])
            self.assertEqual(len(table_rows(database, 'telemetry_samples')), 1)

    def test_remaining_session_time_does_not_revise_completed_analysis(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            changed = bytearray(session_packet())
            struct.pack_into('<H', changed, 38, 120)
            raw = folder/'raw_packets.bin'
            write_packets(raw, [session_packet(), history_packet(2), bytes(changed)])
            ensure_foundation(raw)
            with IncrementalAnalysisStore(raw) as store:
                first = store.update(batch_size=2)
                second = store.update(batch_size=1)
            self.assertEqual(first['analysis_revision'], second['analysis_revision'])

    def test_mixed_player_frame_is_not_a_valid_sample_in_full_or_resumed_analysis(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = root / 'raw_packets.bin'
            write_packets(raw, [session_packet(), motion_packet(1, 1, 20),
                other_player(lap_packet(1, 1, 20), LAP_SCHEMA),
                other_player(telemetry_packet(1, 1, 20, True), CAR_TELEMETRY_SCHEMA), history_packet(2)])
            full = Path(build_analysis(raw, output=root/'full', use_foundation=False,
                                      progress_every=0)['analysis_database'])
            incremental = Path(update_analysis(raw, output=root/'incremental')['analysis_database'])
            for database in (full, incremental):
                self.assertEqual(table_rows(database, 'telemetry_samples'), [])
                with closing(sqlite3.connect(database)) as con:
                    reasons = con.execute('SELECT missing_parts_json FROM frame_quality_issues').fetchall()
                self.assertTrue(any('identity_mismatch' in row[0] for row in reasons))

    def test_algorithm_mismatch_refuses_writes_and_preserves_previous_results(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = root/'raw_packets.bin'
            write_packets(raw, [session_packet(), history_packet(2)])
            database = Path(update_analysis(raw, output=root/'analysis')['analysis_database'])
            with closing(sqlite3.connect(database)) as con:
                con.execute("INSERT OR REPLACE INTO metadata VALUES ('algorithm_contract',?)", (json.dumps({'engine': -1}),))
                con.commit()
            before = table_rows(database, 'incremental_checkpoint')
            with self.assertRaisesRegex(ValueError, '算法'):
                with IncrementalAnalysisStore(raw, database):
                    pass
            self.assertEqual(table_rows(database, 'incremental_checkpoint'), before)

    def test_identical_history_does_not_rewrite_completed_laps_or_segments(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = root/'raw_packets.bin'
            write_packets(raw, [session_packet(), history_packet(2), history_packet(3)])
            ensure_foundation(raw)
            with IncrementalAnalysisStore(raw) as store:
                store.update(batch_size=2)
                statements = []
                store.connection.set_trace_callback(statements.append)
                result = store.update(batch_size=1)
            writes = [s for s in statements if s.startswith(('INSERT INTO laps ', 'INSERT OR REPLACE INTO lap_tyres',
                'DELETE FROM driving_segments', 'DELETE FROM session_metrics'))]
            self.assertEqual(writes, [])
            self.assertEqual(result['analysis_revision'], 1)

    def test_copied_legacy_cache_can_be_rebuilt_without_overwriting_it(self):
        from analysis.incremental import rebuild_analysis, analysis_path
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root/'original'
            original.mkdir()
            write_packets(original/'raw_packets.bin', [session_packet(), history_packet(2)])
            update_analysis(original)
            moved = root/'moved'
            shutil.copytree(original, moved)
            old = analysis_path(moved)
            before = table_rows(old, 'incremental_checkpoint')
            rebuilt = rebuild_analysis(moved)
            self.assertNotEqual(Path(rebuilt['analysis_database']), old)
            self.assertEqual(table_rows(old, 'incremental_checkpoint'), before)
            self.assertEqual(rebuilt['total_raw_packets'], 2)


class ViewRefreshTests(unittest.TestCase):
    def test_packet_progress_keeps_current_distance_focus(self):
        from collector.analysis_gui import AnalysisWindow
        from analysis.incremental import read_summary
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            packets = [session_packet()]
            for frame, distance in enumerate(range(0, 101, 10), 1):
                packets += [motion_packet(frame, frame, distance), lap_packet(frame, frame, distance),
                            telemetry_packet(frame, frame, distance, False)]
            packets.append(history_packet(12))
            raw = folder/'raw_packets.bin'
            write_packets(raw, packets)
            database = Path(update_analysis(raw)['analysis_database'])
            root = tk.Tk()
            root.withdraw()
            window = AnalysisWindow(root, folder, database)
            try:
                deadline = time.monotonic() + 3
                while window.comparison is None and time.monotonic() < deadline:
                    root.update()
                    time.sleep(.01)
                self.assertIsNotNone(window.comparison)
                window._poll_live_analysis()
                window.chart.focus((20., 40.))
                summary = read_summary(database)
                summary['total_raw_packets'] += 1
                with patch('collector.analysis_gui.read_summary', return_value=summary):
                    window._poll_live_analysis()
                self.assertEqual(window.chart.focus_range, (20., 40.))
            finally:
                window.prepare_close()
                while not window.shutdown_ready():
                    time.sleep(.01)
                for token in root.tk.splitlist(root.tk.call('after', 'info')):
                    root.after_cancel(token)
                root.destroy()


class OfflineCancellationTests(unittest.TestCase):
    def test_cancelled_incremental_batch_rolls_back_and_then_resumes(self):
        from storage.task_control import TaskControl
        class CancelDuringBatch(TaskControl):
            def __init__(self):
                super().__init__()
                self.calls = 0
            def check(self):
                self.calls += 1
                if self.calls == 5:
                    self.cancel()
                super().check()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = root/'raw_packets.bin'
            packets = [session_packet(), motion_packet(1,1,20), lap_packet(1,1,20),
                       telemetry_packet(1,1,20,False), history_packet(2)]
            write_packets(raw, packets)
            ensure_foundation(raw)
            with IncrementalAnalysisStore(raw) as store:
                store.update(batch_size=1)
                before = table_rows(store.database, 'incremental_checkpoint')
                with self.assertRaises(InterruptedError):
                    store.update(final=True, control=CancelDuringBatch())
                self.assertEqual(table_rows(store.database,'incremental_checkpoint'), before)
                result = store.update(final=True)
                self.assertEqual(result['total_raw_packets'],5)
                self.assertEqual(len(table_rows(store.database,'telemetry_samples')),1)

    def test_error_totals_are_exact_while_details_are_bounded(self):
        from collector.decode_session import decode_session
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            raw = folder/'raw_packets.bin'
            write_packets(raw, [b'bad'] * 501)
            result = decode_session(raw, write_packets=False, progress_every=0)
            self.assertEqual(result['decode_error_count'], 501)
            details = json.loads((folder/'decoded'/'decode_errors.json').read_text('utf-8'))
            self.assertLessEqual(len(details), 200)
            self.assertTrue(result['error_details_truncated'])

    def test_cancelled_conversion_keeps_source_and_publishes_no_copy(self):
        from storage.task_control import TaskControl
        from storage.archive_tools import convert_archive
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, target = root/'input.bin', root/'copy.bin'
            write_packets(source, [session_packet(), history_packet(2)])
            before = source.read_bytes()
            control = TaskControl()
            control.cancel()
            with self.assertRaises(InterruptedError):
                convert_archive(source, target, control=control)
            self.assertEqual(source.read_bytes(), before)
            self.assertFalse(target.exists())
            self.assertEqual(list(root.glob('*.tmp-*')), [])


class BoundedTaskTests(unittest.TestCase):
    def test_only_latest_pending_request_can_publish(self):
        import threading
        from collector.latest_task import LatestTask
        task = LatestTask()
        entered, release = threading.Event(), threading.Event()
        def slow():
            entered.set()
            release.wait(3)
            return 'stale'
        try:
            task.request('first', slow)
            self.assertTrue(entered.wait(2))
            task.request('skipped', lambda: 'not wanted')
            task.request('latest', lambda: 'current')
            release.set()
            deadline = time.monotonic()+3
            while not task.ready() and time.monotonic()<deadline:
                time.sleep(.01)
            self.assertEqual(task.take(), ('latest','current',None))
        finally:
            release.set()
            task.close()
