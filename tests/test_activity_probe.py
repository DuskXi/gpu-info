import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from activity_probe import parse_metrics, report_window
from main import TelemetryFrame, TelemetryStore


class ActivityProbeTests(unittest.TestCase):
    def test_report_uses_metric_names_and_retains_real_zero_samples(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.sqlite"
            with closing(sqlite3.connect(path)) as database:
                database.execute(
                    "CREATE TABLE TARGET_INFO_GPU_METRICS "
                    "(typeId INTEGER, metricId INTEGER, metricName TEXT)"
                )
                database.execute(
                    "CREATE TABLE GPU_METRICS "
                    "(timestamp INTEGER, typeId INTEGER, metricId INTEGER, value INTEGER)"
                )
                database.execute(
                    "CREATE TABLE TARGET_INFO_SESSION_START_TIME (utcEpochNs INTEGER)"
                )
                database.execute(
                    "INSERT INTO TARGET_INFO_SESSION_START_TIME VALUES (1000000000000)"
                )
                database.executemany(
                    "INSERT INTO TARGET_INFO_GPU_METRICS VALUES (7, ?, ?)",
                    [(91, "Tensor Active [Throughput %]"),
                     (12, "Compute Warps in Flight [Throughput %]")],
                )
                database.executemany(
                    "INSERT INTO GPU_METRICS VALUES (?, 7, ?, ?)",
                    [(0, 91, 0), (1, 91, 30), (2, 91, 0),
                     (0, 12, 25), (1, 12, 50), (2, 12, 0)],
                )
                database.commit()
            metrics = parse_metrics(path)
            window = report_window(path)
        self.assertEqual(metrics["tensor_active"], {
            "mean_percent": 10.0, "peak_percent": 30.0,
            "active_samples": 1, "samples": 3,
        })
        self.assertEqual(metrics["compute_warps"]["peak_percent"], 50.0)
        self.assertNotIn("sm_active", metrics)
        self.assertEqual(window, (1000.0, 1000.000000002))

    def test_concurrent_process_context_stays_separate_from_device_metrics(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TelemetryStore(event_path=Path(directory) / "events.jsonl")
            try:
                store.history.append(TelemetryFrame("stream", 1, 1002.0, {
                    "process_utilization": [
                        {"gpu_id": 0, "pid": 42, "name": "python.exe", "sm_util": 80},
                        {"gpu_id": 1, "pid": 99, "name": "other", "sm_util": 100},
                    ],
                    "processes": [
                        {"gpu_id": 0, "pid": 42, "name": "python.exe", "used_memory": 300},
                    ],
                }))
                context = store._activity_process_context({
                    "gpu_id": 0, "window_started_at": 1001.0,
                    "window_ended_at": 1005.0,
                })
            finally:
                store.close()
        self.assertEqual(context, [{
            "pid": 42, "name": "python.exe", "used_memory": 300,
            "sm_mean_percent": 80.0, "sm_peak_percent": 80.0, "samples": 1,
        }])


if __name__ == "__main__":
    unittest.main()
