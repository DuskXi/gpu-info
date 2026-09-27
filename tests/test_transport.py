import unittest
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


if __name__ == "__main__":
    unittest.main()
