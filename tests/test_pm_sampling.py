import math
import threading
import time
import unittest
from types import SimpleNamespace

from pm_sampling import GROUPS, PMSampling, RollingSamples
from status_exports import render_prometheus
from training_health import TrainingHealth


class RollingTests(unittest.TestCase):
    def test_time_weighted_clipping_dedup_and_epoch_reset(self):
        window = RollingSamples()
        definitions = [{"key": "load", "unit": "percent", "name": "test"}]
        def packet(rows, epoch=1):
            return {"received_at": 42, "epoch": epoch, "samples": [
                {"start_ns": int(a * 1e9), "end_ns": int(b * 1e9), "values": [v]} for a,b,v in rows]}
        result = window.add(packet([(0, 2, 20), (2, 4, 80)]), definitions)
        self.assertEqual(result["observed_seconds"], 3)
        self.assertEqual(result["metrics"]["load"]["value"], 60)
        self.assertIsNone(window.add(packet([(2, 4, 80)]), definitions))
        result = window.add(packet([(10, 11, 50)]), definitions)
        self.assertEqual(result["observed_seconds"], 1)
        self.assertEqual(result["samples"], 1)
        result = window.add(packet([(0, 1, 30)], epoch=2), definitions)
        self.assertEqual(result["metrics"]["load"]["value"], 30)

    def test_invalid_data_and_overflow_do_not_become_zero(self):
        for value in (math.nan, math.inf, -1, True):
            with self.assertRaises(ValueError):
                RollingSamples().add({"received_at": 1, "samples": [
                    {"start_ns": 0, "end_ns": 1, "values": [value]}]}, GROUPS["tensor"])
        with self.assertRaises(ValueError):
            RollingSamples().add({"overflow": True}, GROUPS["tensor"])

    def test_active_lanes_uses_ratio_of_counts_and_zero_denominator_is_unknown(self):
        packet = {"received_at": 1, "samples": [
            {"start_ns": 0, "end_ns": 1_000_000_000, "values": [300, 10]},
            {"start_ns": 1_000_000_000, "end_ns": 2_000_000_000, "values": [10, 1]},
        ]}
        result = RollingSamples().add(packet, GROUPS["scalar"])
        self.assertAlmostEqual(result["metrics"]["fma_light_active_lanes"]["value"], 310 / 11)
        packet["samples"][0]["values"] = packet["samples"][1]["values"] = [0, 0]
        result = RollingSamples().add(packet, GROUPS["scalar"])
        self.assertIsNone(result["metrics"]["fma_light_active_lanes"]["value"])

    def test_timestamp_rounding_is_clipped_but_large_overlap_is_rejected(self):
        window = RollingSamples()
        def packet(start, end):
            return {"received_at": 1, "samples": [{"start_ns": start, "end_ns": end, "values": [1]}]}
        window.add(packet(0, 100_000_000), GROUPS["tensor"])
        result = window.add(packet(99_999_657, 200_000_000), GROUPS["tensor"])
        self.assertEqual(result["timestamp_trimmed_ns"], 343)
        self.assertEqual(result["observed_seconds"], .2)
        with self.assertRaises(ValueError):
            window.add(packet(199_000_000, 300_000_000), GROUPS["tensor"])

    def test_profiler_mutex_is_shared_with_legacy_probe(self):
        from activity_probe import ProbeError
        from unittest.mock import patch
        lock = threading.Lock()
        lock.acquire()
        sampler = PMSampling(lock)
        with patch("pm_sampling.Path.is_file", return_value=True):
            with self.assertRaises(ProbeError):
                sampler.start(0, "0000:01:00.0")
        self.assertTrue(lock.locked())
        lock.release()

    def test_export_omits_old_hardware_values_but_keeps_timestamp(self):
        now = time.time()
        group = {"sampled_at": now - 10, "metrics": {
            "tensor_ips": {"value": 4, "unit": "warp_instructions_per_second"}}}
        frame = {"timestamp": now, "gpus": [{"id": 0}], "hardware_sampling": {
            "enabled": True, "gpu_id": 0, "groups": {"tensor": group}}}
        text = render_prometheus(frame)
        self.assertNotIn('gpu_info_hardware_warp_instructions_per_second{', text)
        self.assertIn('gpu_info_hardware_sample_fresh{gpu="0",group="tensor"} 0', text)
        group["sampled_at"] = now
        self.assertIn('gpu_info_hardware_warp_instructions_per_second{', render_prometheus(frame))


class HealthTests(unittest.TestCase):
    def test_driver_reset_unsupported_fields_and_unknown_bits(self):
        class Nv:
            NVML_FI_DEV_ENERGY = 1
            energy, stamp = 1000, 1_000_000
            def nvmlDeviceGetFieldValues(self, handle, fields):
                return [SimpleNamespace(valueType=3, nvmlReturn=0, timestamp=self.stamp,
                                        value=SimpleNamespace(ullVal=self.energy))]
            def nvmlDeviceGetCurrentClocksEventReasons(self, handle): return 0x400
        nv = Nv()
        health = TrainingHealth()
        first = health.capture(nv, None, 0)
        self.assertIsNone(first["instant_power_w"])
        self.assertEqual(first["unknown_clock_reasons"], 0x400)
        self.assertEqual(first["clock_reasons"], [])
        nv.energy, nv.stamp = 301000, 2_000_000
        self.assertEqual(health.capture(nv, None, 0)["energy_window_w"], 300)
        nv.energy, nv.stamp = 5, 3_000_000
        self.assertIsNone(health.capture(nv, None, 0)["energy_window_w"])


if __name__ == "__main__":
    unittest.main()
