import sys
import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch

from monitor import GPUMonitor, _counter_identity, _pci_key


class FakeNvml:
    def nvmlDeviceGetComputeRunningProcesses(self, _handle):
        return [SimpleNamespace(pid=123, usedGpuMemory=400)]

    def nvmlDeviceGetGraphicsRunningProcesses(self, _handle):
        return [
            SimpleNamespace(pid=123, usedGpuMemory=400),
            SimpleNamespace(pid=456, usedGpuMemory=200),
        ]


class ProcessAccountingTests(unittest.TestCase):
    def test_adapter_ids_are_normalized(self):
        self.assertEqual(_pci_key("00000000:01:00.0"), _pci_key(b"0000:01:00.0"))
        self.assertEqual(
            _counter_identity("pid_42_luid_0x00000000_0x000196BD_phys_0"),
            (42, "0X00000000_0X000196BD"),
        )
        self.assertEqual(
            _counter_identity(
                r"\GPU Engine(pid_42_luid_0x00000000_0x000196BD_phys_0_eng_0)\Utilization Percentage"
            ),
            (42, "0X00000000_0X000196BD"),
        )

    def test_compute_and_graphics_do_not_double_count_pid(self):
        collector = GPUMonitor.__new__(GPUMonitor)
        collector.nvml = FakeNvml()
        result = {"processes": []}

        collector._capture_processes(
            result, index=0, handle=object(), name="Test GPU",
            total=1000, names={123: "compute", 456: "graphics"},
        )

        self.assertEqual(
            [(p["pid"], p["used_memory"]) for p in result["processes"]],
            [(123, 400), (456, 200)],
        )
        self.assertTrue(all(p["shared_memory"] is None for p in result["processes"]))

    def test_windows_counters_ignore_unmapped_integrated_gpu(self):
        collector = GPUMonitor.__new__(GPUMonitor)
        collector.nvml = FakeNvml()
        collector._luid_by_gpu = {"0X00000000_0X000196BD": 0}
        collector._wmi_error = None
        collector._names = {42: "NVIDIA process", 43: "integrated process"}
        collector._names_at = float("inf")

        class FakeWmi:
            def Win32_PerfFormattedData_GPUPerformanceCounters_GPUProcessMemory(self):
                return [
                    SimpleNamespace(
                        Name="pid_42_luid_0x00000000_0x000196BD_phys_0",
                        DedicatedUsage="100", LocalUsage="40",
                        NonLocalUsage="10", TotalCommitted="55",
                        SharedUsage="20",
                    ),
                    SimpleNamespace(
                        Name="pid_43_luid_0x00000000_0x0001D7FB_phys_0",
                        DedicatedUsage="900", LocalUsage="800",
                        NonLocalUsage="0", TotalCommitted="900",
                        SharedUsage="0",
                    ),
                ]

            def Win32_PerfFormattedData_GPUPerformanceCounters_GPUAdapterMemory(self):
                return [
                    SimpleNamespace(
                        Name="luid_0x00000000_0x000196BD_phys_0",
                        DedicatedUsage="750", SharedUsage="25",
                    ),
                    SimpleNamespace(
                        Name="luid_0x00000000_0x0001D7FB_phys_0",
                        DedicatedUsage="999", SharedUsage="999",
                    ),
                ]

        collector._wmi = FakeWmi()
        collector._read_engine_load = lambda _map: {(0, 42): 23}
        result = collector._query_windows_processes(
            collector._names, {0: "NVIDIA"}
        )

        self.assertEqual(len(result.processes), 1)
        self.assertEqual(result.processes[0]["pid"], 42)
        self.assertEqual(result.processes[0]["used_memory"], 40)
        self.assertEqual(result.processes[0]["local_memory"], 40)
        self.assertEqual(result.processes[0]["claimed_memory"], 100)
        self.assertEqual(result.processes[0]["committed_memory"], 55)
        self.assertEqual(result.processes[0]["nonlocal_memory"], 10)
        self.assertEqual(result.processes[0]["gpu_percent"], 23)
        self.assertEqual(result.adapters, [{
            "gpu_id": 0, "dedicated_usage": 750, "shared_usage": 25,
        }])

    def test_engine_rates_use_only_matched_nvidia_luid(self):
        collector = GPUMonitor.__new__(GPUMonitor)
        nvidia = "0X00000000_0X000196BD"
        paths = [
            rf"\GPU Engine(pid_{pid}_luid_{luid}_phys_0_eng_{engine})\Utilization Percentage"
            for pid, luid, engine in [
                (42, "0x00000000_0x000196BD", 0),
                (42, "0x00000000_0x000196BD", 1),
                (43, "0x00000000_0x0001D7FB", 0),
            ]
        ]
        fake_pdh = SimpleNamespace(
            PDH_FMT_DOUBLE=1,
            OpenQuery=lambda: object(),
            ExpandCounterPath=lambda _path: paths,
            AddCounter=lambda _query, path: path,
            CollectQueryData=lambda _query: None,
            GetFormattedCounterValue=lambda path, _format: (
                0, {paths[0]: 23.0, paths[1]: 7.0, paths[2]: 99.0}[path]
            ),
            CloseQuery=lambda _query: None,
        )
        with patch.dict(sys.modules, {"win32pdh": fake_pdh}), patch(
            "monitor.time.sleep"
        ):
            rates = collector._read_engine_load({nvidia: 0})
        self.assertEqual(rates, {(0, 42): 23.0})

    def test_process_rates_are_sampled_and_reset_after_pid_reuse(self):
        collector = GPUMonitor.__new__(GPUMonitor)
        collector._process_samples = {}
        collector._logical_cpus = 4

        class FakeProcess:
            created = 1.0
            cpu = 2.0
            io = 100

            def oneshot(self):
                return nullcontext()

            def create_time(self):
                return self.created

            def cpu_times(self):
                return SimpleNamespace(user=self.cpu, system=0.0)

            def io_counters(self):
                return SimpleNamespace(read_bytes=self.io, write_bytes=0)

        process = FakeProcess()

        def frame():
            return {
                "processes": [{"pid": 42, "gpu_id": 0, "used_memory": 100}],
                "process_utilization": [{"pid": 42, "gpu_id": 0, "sm_util": 12}],
            }

        with patch("monitor.psutil.Process", return_value=process), patch(
            "monitor.time.monotonic", side_effect=[10.0, 12.0, 14.0]
        ):
            first = frame()
            collector._sample_process_activity(first)
            self.assertIsNone(first["processes"][0]["cpu_percent"])
            self.assertIsNone(first["processes"][0]["disk_bytes_per_sec"])
            self.assertEqual(first["processes"][0]["gpu_percent"], 12)

            process.cpu = 4.0
            process.io = 2148
            second = frame()
            collector._sample_process_activity(second)
            self.assertEqual(second["processes"][0]["cpu_percent"], 25)
            self.assertEqual(second["processes"][0]["disk_bytes_per_sec"], 1024)

            process.created = 2.0
            third = frame()
            collector._sample_process_activity(third)
            self.assertIsNone(third["processes"][0]["cpu_percent"])
            self.assertIsNone(third["processes"][0]["disk_bytes_per_sec"])


if __name__ == "__main__":
    unittest.main()
