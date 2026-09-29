import io
import json
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from bandwidth_sampling import BandwidthSampling, capture_bandwidth, link_capacity
from main import app, TelemetryFrame, TelemetryStore
from pm_sampling import PMSampling, sampling_plan
from status_exports import render_prometheus, render_status_markdown


class SamplingTests(unittest.TestCase):
    def test_shared_cohort_projects_identical_window_and_bounded_cursor(self):
        plan = sampling_plan("compute")
        self.assertEqual(len(plan["cohorts"]["execution"]), 11)
        self.assertTrue(plan["simultaneous_all_metrics"])
        self.assertFalse(sampling_plan("diagnostic")["simultaneous_all_metrics"])
        lock = threading.Lock()
        lock.acquire()
        sampler = PMSampling(lock, lambda gpu: {"estimated_peak_bytes_per_second": 1000})
        sampler._state.update(session_id="new", gpu_id=0, view_cohorts=plan["views"])
        packets = [{"sequence": i, "group": "execution", "epoch": 1, "received_at": time.time(),
                    "samples": [{"start_ns": i * 50_000_000, "end_ns": (i+1) * 50_000_000,
                                 "values": [20] * 11}]} for i in range(1, 243)]
        process = SimpleNamespace(stdout=io.StringIO("\n".join(map(json.dumps, packets))),
                                  stderr=io.StringIO(), stdin=io.StringIO(), wait=lambda timeout: None, returncode=0)
        sampler._read(process, plan)
        state = sampler.snapshot()
        groups = state["groups"]
        self.assertEqual(len({g["window_id"] for g in groups.values()}), 1)
        self.assertEqual(groups["memory"]["metrics"]["dram_read_estimated_bytes_per_second"]["value"], 200)
        self.assertEqual(len(groups["tensor"]["metrics"]), 1)
        self.assertFalse(lock.locked())
        self.assertFalse(state["ready"])
        batch = sampler.samples(0, 3, "old")
        self.assertTrue(batch["reset"])
        self.assertTrue(batch["truncated"])
        self.assertEqual(batch["next_sequence"], 5)
        self.assertEqual(sampler.samples(5, 1, "new")["packets"][0]["sequence"], 6)

    def test_ready_requires_all_groups_fresh_and_enabled(self):
        sampler = PMSampling(threading.Lock())
        sampler._state.update(state="running", enabled=True, view_cohorts={"a": "x", "b": "y"},
                              groups={"a": {"sampled_at": time.time()}})
        self.assertEqual(sampler.snapshot()["missing_groups"], ["b"])
        sampler._state["groups"]["b"] = {"sampled_at": time.time() - 10}
        self.assertFalse(sampler.snapshot()["ready"])
        sampler._state["groups"]["b"]["sampled_at"] = time.time()
        self.assertTrue(sampler.snapshot()["ready"])
        sampler._state["enabled"] = False
        self.assertFalse(sampler.snapshot()["ready"])

    def test_pcie_encoding_direction_units_and_unsupported_are_explicit(self):
        nv = SimpleNamespace(NVML_PCIE_UTIL_TX_BYTES=0, NVML_PCIE_UTIL_RX_BYTES=1,
             nvmlDeviceGetCurrPcieLinkGeneration=lambda h: 4, nvmlDeviceGetCurrPcieLinkWidth=lambda h: 16,
             nvmlDeviceGetPcieThroughput=lambda h, direction: 1024 if direction == 0 else 2048)
        row = capture_bandwidth(nv, None)
        self.assertEqual(row["pcie_tx_bytes_per_second"], 1024**2)
        self.assertEqual(row["pcie_rx_bytes_per_second"], 2 * 1024**2)
        self.assertAlmostEqual(row["pcie_tx_percent"], 1024**2 / (16e9 * 128/130 * 16/8) * 100)
        self.assertIsNone(row["memory_reference"]["estimated_peak_bytes_per_second"])
        self.assertIsNone(link_capacity(6, 16))  # Do not apply Gen3 encoding to PCIe 6.
        unknown = capture_bandwidth(SimpleNamespace(), None)
        self.assertIsNone(unknown["pcie_tx_percent"])
        self.assertIsNone(unknown["pcie_rx_bytes_per_second"])

    def test_bandwidth_cursor_reset_and_retention(self):
        sampler = BandwidthSampling(None)
        sampler._state["session_id"] = "current"
        sampler._samples.extend({"sequence": i} for i in range(1, 605))
        result = sampler.samples(2, 2, "current")
        self.assertTrue(result["truncated"])
        self.assertEqual(result["next_sequence"], 6)
        self.assertTrue(sampler.samples(600, 1, "old")["reset"])

    def test_exports_keep_estimates_distinct_from_measured_bandwidth(self):
        now = time.time()
        frame = {"timestamp": now, "gpus": [{"id": 0, "bandwidth": {
            "sampled_at": now, "pcie_tx_bytes_per_second": 100, "pcie_tx_percent": 5}}],
            "hardware_sampling": {"enabled": True, "gpu_id": 0, "groups": {"memory": {
                "sampled_at": now, "metrics": {"dram_read_estimated_bytes_per_second": {
                    "value": 200, "unit": "estimated_bytes_per_second"}}}}}}
        text = render_prometheus(frame)
        self.assertIn("gpu_info_hardware_estimated_bytes_per_second{", text)
        self.assertIn('direction="tx"} 100', text)
        self.assertIn('direction="tx"} 0.05', text)
        self.assertIn("估算", render_status_markdown(frame))


class ControlTests(unittest.TestCase):
    def test_no_parameters_are_documentation_and_get_cannot_mutate(self):
        client = TestClient(app)
        for method in (client.get, client.post):
            response = method("/api/sampling-control")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["endpoint"], "/api/sampling-control")
            self.assertIn("compute", response.json()["parameters"]["mode"]["enum"])
        self.assertEqual(client.get("/api/sampling-control?action=start").status_code, 405)
        for query in ("target=hardware", "action=start&mode=typo", "action=state&unknown=1", "action=start&gpu=-1"):
            self.assertEqual(client.post("/api/sampling-control?" + query).status_code, 422)

    def test_control_uses_shared_collectors_and_validates_before_mutation(self):
        store = TelemetryStore()
        store.history.append(TelemetryFrame("test", 1, time.time(), {"gpus": [{"id": 0, "device_info": {"pci_bus_id": "pci"}}]}))
        client = TestClient(app)
        with patch("main.store", store), patch.object(store.pm_sampling, "start", return_value={}) as start, \
             patch.object(store.bandwidth_sampling, "start", return_value={}) as bandwidth:
            response = client.post("/api/sampling-control?action=start&target=all&gpu=7")
            self.assertEqual(response.status_code, 404)
            start.assert_not_called()
            bandwidth.assert_not_called()
            response = client.post("/api/sampling-control?action=start&target=all&mode=compute&replace=true")
            self.assertEqual(response.status_code, 200)
            start.assert_called_once_with(0, "pci", "compute", True)
            bandwidth.assert_called_once_with(0)
            self.assertEqual(client.post("/api/sampling-control?action=stop&target=all").status_code, 200)
            self.assertFalse(store.telemetry_enabled)
            self.assertFalse(client.get("/api/sampling-control?action=state").json()["telemetry"]["enabled"])
        store.executor.shutdown(wait=False)
