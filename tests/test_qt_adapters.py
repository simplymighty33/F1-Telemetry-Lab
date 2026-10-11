import math
import importlib.util
import random
import unittest
from types import SimpleNamespace

NUMPY_AVAILABLE = importlib.util.find_spec('numpy') is not None
if NUMPY_AVAILABLE:
    from collector.qt_ui.adapters import channel_data, nearest_sample, source_revision, SampleIndex


def point(distance=1000, **values):
    fields = dict(distance_m=distance, delta_ms=-428, reference_speed_kph=250,
                  reference_throttle=.75, reference_brake=.2, valid=True, connected=True)
    return SimpleNamespace(**(fields | values))


@unittest.skipUnless(NUMPY_AVAILABLE, 'Optional NumPy GUI dependency not installed')
class QtAdapterTests(unittest.TestCase):
    def test_units_and_connection_direction(self):
        trace = [point(0), point(1000, connected=False), point(2000)]
        x, y, connect = channel_data(trace, 'delta_ms')
        self.assertEqual(x.tolist(), [0, 1, 2])
        self.assertEqual(y.tolist(), [-.428] * 3)
        self.assertEqual(connect.tolist(), [False, True, False])
        self.assertEqual(channel_data(trace, 'reference_throttle')[1].tolist(), [75] * 3)
        self.assertEqual(channel_data(trace, 'reference_speed_kph')[1].tolist(), [250] * 3)

    def test_invalid_middle_is_not_bridged(self):
        trace = [point(0), point(5, valid=False), point(10)]
        _, y, connect = channel_data(trace, 'delta_ms')
        self.assertTrue(math.isnan(y[1]))
        self.assertFalse(connect.any())

    def test_channel_validity_is_independent(self):
        trace = [point(0), point(5, reference_throttle=float('nan')), point(10)]
        self.assertFalse(channel_data(trace, 'reference_throttle')[2].any())
        self.assertEqual(channel_data(trace, 'delta_ms')[2].tolist(), [True, True, False])

    def test_empty_single_and_nonfinite_distances(self):
        self.assertEqual(len(channel_data([], 'delta_ms')[0]), 0)
        self.assertEqual(channel_data([point()], 'delta_ms')[2].tolist(), [False])
        trace = [point(0), point(float('inf')), point(10)]
        self.assertFalse(channel_data(trace, 'delta_ms')[2].any())

    def test_cursor_uses_original_sample_and_rejects_gap(self):
        trace = [point(0), point(5, connected=False), point(10)]
        self.assertIs(nearest_sample(trace, 9), trace[2])
        self.assertIsNone(nearest_sample(trace, 5))
        self.assertIsNone(nearest_sample([], 1))

    def test_revision_is_not_only_lap_number(self):
        self.assertNotEqual(source_revision('a.db', {'analysis_revision': 1}),
                            source_revision('b.db', {'analysis_revision': 1}))
        self.assertNotEqual(source_revision('a.db', {'analysis_revision': 1}),
                            source_revision('a.db', {'analysis_revision': 2}))
        self.assertNotEqual(source_revision('a.db', {'analysis_revision': 1, 'source_identity': (1, 2)}),
                            source_revision('a.db', {'analysis_revision': 1, 'source_identity': (1, 3)}))

    def test_indexed_lookup_matches_original_sample_oracle(self):
        rng = random.Random(20261006)
        for size in (0, 1, 2, 64, 2048):
            distances = sorted(rng.randrange(0, 6000) for _ in range(size))
            trace = tuple(point(d, valid=rng.choice([True, False]), connected=rng.choice([True, False])) for d in distances)
            index = SampleIndex(trace)
            for d in [0, 6000, float('nan'), *[rng.random() * 6000 for _ in range(40)]]:
                self.assertIs(index.nearest(d), nearest_sample(trace, d))
        for trace in ([point(10), point(float('nan')), point(0)], [point(7), point(7), point(9)]):
            for d in (7, 8, 10):
                self.assertIs(SampleIndex(trace).nearest(d), nearest_sample(trace, d))
