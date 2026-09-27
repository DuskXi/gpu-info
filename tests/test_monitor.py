import unittest
from types import SimpleNamespace

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
                        DedicatedUsage="100", SharedUsage="20",
                    ),
                    SimpleNamespace(
                        Name="pid_43_luid_0x00000000_0x0001D7FB_phys_0",
                        DedicatedUsage="900", SharedUsage="0",
                    ),
                ]

        collector._wmi = FakeWmi()
        result = collector._query_windows_processes(
            collector._names, {0: "NVIDIA"}
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["pid"], 42)
        self.assertEqual(result[0]["used_memory"], 100)


if __name__ == "__main__":
    unittest.main()
