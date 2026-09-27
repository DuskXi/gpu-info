import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from main import TelemetryStore


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


if __name__ == "__main__":
    unittest.main()
