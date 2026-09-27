import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from main import TelemetryFrame, TelemetryStore, app


class StoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_history_and_subscribers_receive_same_frame(self):
        store = TelemetryStore(capacity=2)
        queue = store.subscribe()
        payload = {
            "system": {}, "gpus": [], "processes": [],
            "process_utilization": [], "source": "NVML", "errors": [],
        }
        try:
            with patch("main.monitor.capture", return_value=payload):
                await store.poll()
                first = queue.get_nowait().as_message()
                await store.poll()
                await store.poll()
            self.assertEqual(first["sequence"], 1)
            self.assertEqual(first["stream_id"], store.stream_id)
            self.assertEqual(store.recent(2)[0]["sequence"], 2)
            self.assertEqual(store.latest.as_message(), queue.get_nowait().as_message())
        finally:
            store.unsubscribe(queue)
            store.close()

    async def test_display_changes_are_persisted_without_duplicate_events(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "display-events.jsonl"
            store = TelemetryStore(event_path=path)
            one = {"count": 1, "displays": [{"id": "A", "width": 3840}]}
            two = {
                "count": 2,
                "displays": [{"id": "A", "width": 3840}, {"id": "B", "width": 3840}],
            }

            def frame(state):
                return {
                    "display_state": state,
                    "gpus": [{"id": 0, "memory": {"used": 100, "total": 200}}],
                    "processes": [],
                }
            try:
                with patch("main.monitor.capture", side_effect=[frame(one), frame(one), frame(two)]):
                    await store.poll()
                    await store.poll()
                    await store.poll()
                self.assertEqual([event["kind"] for event in store.display_events],
                                 ["baseline", "change"])
                self.assertEqual(store.latest.as_message()["display_events"][-1]["current"], two)
                self.assertEqual(store.display_events[-1]["gpu_memory"][0]["used"], 100)
                self.assertEqual(len(path.read_text(encoding="utf-8").splitlines()), 2)
                restored = TelemetryStore(event_path=path)
                self.assertEqual(len(restored.display_events), 2)
                restored.close()
            finally:
                store.close()


class ExportEndpointTests(unittest.TestCase):
    def test_root_exports_use_the_same_frame_without_new_capture(self):
        payload = {
            "system": {"cpu_percent": 17.5, "memory": {"used": 1024, "total": 2048, "percent": 50}},
            "gpus": [{"id": 0, "name": "Test GPU", "memory": {"total": 200, "used": 150, "free": 50},
                      "utilization": {"gpu": 42, "memory": None}, "temperature": 60,
                      "power": {"usage": 120.5}}],
            "processes": [{"gpu_id": 0, "pid": 42, "name": 'render"\\worker|x',
                           "local_memory": 80, "claimed_memory": 900, "cpu_percent": 5,
                           "gpu_percent": None, "disk_bytes_per_sec": 1024,
                           "memory_source": "Windows GPU counters"}],
            "process_utilization": [], "windows_adapter_memory": [],
            "display_state": {"count": 1, "displays": []}, "source": "NVML", "errors": [],
        }
        frame = TelemetryFrame("stream", 7, 1234567890.0, payload)
        with patch("main.store", SimpleNamespace(latest=frame)), \
             patch("main.monitor.capture", side_effect=AssertionError("new capture")):
            client = TestClient(app)
            json_response = client.get("/status.json")
            old_response = client.get("/api/status")
            markdown = client.get("/status.md")
            metrics = client.get("/metrics")

        self.assertEqual(json_response.json(), old_response.json())
        self.assertEqual(json_response.json()["sequence"], 7)
        self.assertEqual(json_response.headers["cache-control"], "no-store")
        self.assertEqual(markdown.status_code, 200)
        self.assertTrue(markdown.headers["content-type"].startswith("text/markdown"))
        self.assertIn("render\"\\worker\\|x", markdown.text)
        self.assertIn("专用计数", markdown.text)
        self.assertEqual(metrics.status_code, 200)
        self.assertIn("version=0.0.4", metrics.headers["content-type"])
        self.assertIn("gpu_info_gpu_memory_used_bytes{gpu=\"0\",name=\"Test GPU\"} 150", metrics.text)
        self.assertIn("gpu_info_process_local_memory_bytes{gpu=\"0\",pid=\"42\"", metrics.text)
        self.assertIn('name="render\\"\\\\worker|x"', metrics.text)
        self.assertNotIn("gpu_info_gpu_memory_controller_utilization_percent{", metrics.text)
        self.assertNotIn("gpu_info_process_gpu_percent{", metrics.text)
        self.assertIn("gpu_info_display_count 1", metrics.text)

    def test_root_exports_report_missing_first_frame(self):
        with patch("main.store", SimpleNamespace(latest=None)):
            client = TestClient(app)
            for path in ("/status.json", "/status.md", "/metrics"):
                self.assertEqual(client.get(path).status_code, 503)
            self.assertTrue(client.get("/status.md").headers["content-type"].startswith("text/markdown"))
            self.assertIn("gpu_info_up 0", client.get("/metrics").text)


if __name__ == "__main__":
    unittest.main()
