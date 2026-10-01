from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest

from analysis.comparison import AnalysisDatabaseError, AnalysisRepository
from analysis.schema import create_schema


class ComparisonRepositoryTests(unittest.TestCase):
    def test_lists_laps_and_aligns_distance_traces(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "telemetry_analysis.db"
            connection = sqlite3.connect(database)
            create_schema(connection)
            connection.execute(
                "INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                ("7", "Career '23", 0, "Practice 2", 1, "Melbourne", 13, 100, 10, 1, 2),
            )
            for lap_number, lap_time in ((1, 10_000), (2, 10_500)):
                connection.execute(
                    "INSERT INTO laps VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    ("7", lap_number, lap_time, 3000, 3000, lap_time - 6000, 1, 1, 1, 1, 2),
                )
                connection.execute(
                    "INSERT INTO lap_metrics VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        "7", lap_number, lap_time, lap_time - 10_000,
                        70.0, 10.0, 5.0, 1.0, 300.0, 80.0, 200.0,
                        1, 1, 2, 2, 3, 2.0, 1.0, 0.8, 0.9,
                    ),
                )
                for distance in (0.0, 50.0, 100.0):
                    connection.execute(
                        """
                        INSERT INTO resampled_lap_samples (
                            session_uid, lap_number, distance_m, lap_time_ms,
                            speed_kph, throttle, brake, steer, gear
                        ) VALUES (?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            "7", lap_number, distance,
                            distance * (100 if lap_number == 1 else 105),
                            100 + distance, distance / 100, 0.0, 0.1, 4,
                        ),
                    )
                connection.execute(
                    "INSERT INTO braking_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("7", lap_number, 1, 20.0, 25.0, 40.0, 2000, 4000, 2000, 200, 100, 120, 1.0, -4.0, 15.0, 1.0, 0.1),
                )
            connection.execute(
                "INSERT INTO session_metrics VALUES (?,?,?,?,?,?,?,?,?)",
                ("7", 2, 1, 10_000, 3000, 3000, 4000, 10_000, 0),
            )
            connection.commit()
            connection.close()

            repository = AnalysisRepository(database)
            sessions = repository.sessions()
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0].track, "Melbourne")
            self.assertEqual(len(repository.laps("7")), 2)

            comparison = repository.comparison("7", 1, 2)
            self.assertEqual(len(comparison.trace), 3)
            self.assertEqual(comparison.trace[-1].delta_ms, 500)
            self.assertEqual(len(comparison.reference_braking), 1)
            self.assertEqual(len(comparison.comparison_braking), 1)

    def test_rejects_unrelated_sqlite_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "other.db"
            sqlite3.connect(database).close()
            with self.assertRaises(AnalysisDatabaseError):
                AnalysisRepository(database)


if __name__ == "__main__":
    unittest.main()
