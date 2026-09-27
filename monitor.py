"""GPU collector using NVML and Windows per-process counters.

CUDA Driver device identity queries never create a CUDA context.
"""

import logging
import math
import os
import re
import struct
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, NamedTuple

import psutil

logger = logging.getLogger(__name__)


def _pci_key(value: str | bytes) -> tuple[int, int, int, int] | None:
    text = value.decode() if isinstance(value, bytes) else value
    match = re.search(r"([0-9a-f]+):([0-9a-f]{2}):([0-9a-f]{2})\.([0-7])$", text.lower())
    if match is None:
        return None
    return (
        int(match.group(1), 16), int(match.group(2), 16),
        int(match.group(3), 16), int(match.group(4), 16),
    )


def _counter_identity(name: str) -> tuple[int, str] | None:
    match = re.search(
        r"pid_(\d+)_luid_(0x[0-9a-f]+)_(0x[0-9a-f]+)", name, re.IGNORECASE
    )
    if match is None:
        return None
    return int(match.group(1)), f"{match.group(2)}_{match.group(3)}".upper()


class _ProcessSample(NamedTuple):
    created_at: float
    sampled_at: float
    cpu_seconds: float
    io_bytes: int | None


class _WindowsMemorySample(NamedTuple):
    processes: list[dict[str, Any]]
    adapters: list[dict[str, Any]]


class GPUMonitor:
    def __init__(self) -> None:
        self.nvml: Any = None
        self._static: dict[int, dict[str, Any]] = {}
        self._names: dict[int, str] = {}
        self._names_at = 0.0
        self._util_since: dict[int, int] = {}
        self._wmi: Any = None
        self._wmi_initialized = False
        self._luid_by_gpu: dict[str, int] | None = None
        self._wmi_error: str | None = None
        self._wmi_executor = (
            ThreadPoolExecutor(max_workers=1, thread_name_prefix="gpu-wmi")
            if os.name == "nt" else None
        )
        self._wmi_future: Future[_WindowsMemorySample] | None = None
        self._wmi_processes: list[dict[str, Any]] = []
        self._wmi_adapter_memory: list[dict[str, Any]] = []
        self._wmi_requested_at = 0.0
        self._wmi_sampled_at: float | None = None
        self._process_samples: dict[int, _ProcessSample] = {}
        self._logical_cpus = max(1, psutil.cpu_count() or 1)
        try:
            import pynvml
            pynvml.nvmlInit()
            self.nvml = pynvml
        except Exception as exc:
            logger.warning("NVML unavailable: %s", exc)

    def close(self) -> None:
        if self._wmi_executor is not None:
            released = False
            try:
                self._wmi_executor.submit(self._release_wmi).result(timeout=12)
                released = True
            except Exception as exc:
                logger.warning("WMI shutdown incomplete: %s", exc)
            self._wmi_executor.shutdown(wait=released, cancel_futures=True)
        if self.nvml is not None:
            self.nvml.nvmlShutdown()
            self.nvml = None

    @staticmethod
    def _read(fn: Callable[[], Any], default: Any = None) -> Any:
        try:
            return fn()
        except Exception:
            return default

    def _process_names(self) -> dict[int, str]:
        if time.monotonic() - self._names_at < 5:
            return self._names
        names: dict[int, str] = {}
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                names[proc.info["pid"]] = proc.info["name"] or "Unknown"
            except (psutil.Error, KeyError):
                continue
        self._names = names
        self._names_at = time.monotonic()
        return names

    def _static_info(self, index: int, handle: Any) -> dict[str, Any]:
        if index not in self._static:
            nv = self.nvml
            capability = self._read(lambda: nv.nvmlDeviceGetCudaComputeCapability(handle))
            self._static[index] = {
                "uuid": self._read(lambda: nv.nvmlDeviceGetUUID(handle), ""),
                "architecture": self._read(lambda: str(nv.nvmlDeviceGetArchitecture(handle)), ""),
                "compute_capability": ".".join(map(str, capability)) if capability else "",
                "memory_bus_width": self._read(lambda: nv.nvmlDeviceGetMemoryBusWidth(handle)),
                "vbios": self._read(lambda: nv.nvmlDeviceGetVbiosVersion(handle), ""),
            }
        return self._static[index]

    def capture(self) -> dict[str, Any]:
        """One coherent sample; no per-field thread races or stale-value reuse."""
        vm = psutil.virtual_memory()
        result: dict[str, Any] = {
            "system": {
                "cpu_percent": psutil.cpu_percent(interval=None),
                "memory": {"total": vm.total, "used": vm.used, "percent": vm.percent},
            },
            "gpus": [], "processes": [], "process_utilization": [],
            "windows_adapter_memory": [],
            "source": "NVML" if self.nvml is not None else "unavailable",
            "errors": [],
        }
        if self.nvml is None:
            result["errors"].append("NVML unavailable; NVIDIA metrics cannot be read")
            return result
        nv = self.nvml
        names = self._process_names()
        driver = self._read(nv.nvmlSystemGetDriverVersion, "")
        try:
            count = nv.nvmlDeviceGetCount()
        except Exception as exc:
            result["errors"].append(f"GPU enumeration failed: {exc}")
            return result
        for index in range(count):
            try:
                handle = nv.nvmlDeviceGetHandleByIndex(index)
                memory = nv.nvmlDeviceGetMemoryInfo(handle)
                utilization = self._read(lambda: nv.nvmlDeviceGetUtilizationRates(handle))
                name = self._read(lambda: nv.nvmlDeviceGetName(handle), f"GPU {index}")
                total, used = int(memory.total), int(memory.used)
                power = self._read(lambda: nv.nvmlDeviceGetPowerUsage(handle))
                power_limit = self._read(lambda: nv.nvmlDeviceGetEnforcedPowerLimit(handle))
                bar1 = self._read(lambda: nv.nvmlDeviceGetBAR1MemoryInfo(handle))
                result["gpus"].append({
                    "id": index, "name": name, "driver_version": driver,
                    "memory": {
                        "total": total, "used": used, "free": int(memory.free),
                        "percent": used / total * 100 if total else 0,
                    },
                    "utilization": {
                        "gpu": getattr(utilization, "gpu", None),
                        "memory": getattr(utilization, "memory", None),
                    },
                    "temperature": self._read(
                        lambda: nv.nvmlDeviceGetTemperature(handle, nv.NVML_TEMPERATURE_GPU)
                    ),
                    "fan_speed": self._read(lambda: nv.nvmlDeviceGetFanSpeed(handle)),
                    "power": {
                        "usage": power / 1000 if power is not None else None,
                        "limit": power_limit / 1000 if power_limit is not None else None,
                    },
                    "clocks": {
                        "graphics": self._read(
                            lambda: nv.nvmlDeviceGetClockInfo(handle, nv.NVML_CLOCK_GRAPHICS)
                        ),
                        "memory": self._read(
                            lambda: nv.nvmlDeviceGetClockInfo(handle, nv.NVML_CLOCK_MEM)
                        ),
                    },
                    "engines": {
                        "encoder": self._read(lambda: nv.nvmlDeviceGetEncoderUtilization(handle)[0]),
                        "decoder": self._read(lambda: nv.nvmlDeviceGetDecoderUtilization(handle)[0]),
                    },
                    "pcie": {
                        "tx": self._read(lambda: nv.nvmlDeviceGetPcieThroughput(
                            handle, nv.NVML_PCIE_UTIL_TX_BYTES
                        )),
                        "rx": self._read(lambda: nv.nvmlDeviceGetPcieThroughput(
                            handle, nv.NVML_PCIE_UTIL_RX_BYTES
                        )),
                    },
                    "bar1": {
                        "total": getattr(bar1, "bar1Total", None),
                        "used": getattr(bar1, "bar1Used", None),
                    },
                    "performance_state": self._read(
                        lambda: f"P{nv.nvmlDeviceGetPerformanceState(handle)}"
                    ),
                    "device_info": self._static_info(index, handle),
                })
                self._capture_processes(result, index, handle, name, total, names)
                self._capture_utilization(result, index, handle, names)
            except Exception as exc:
                logger.warning("GPU %d capture failed: %s", index, exc)
                result["errors"].append(f"GPU {index}: {exc}")
        if os.name == "nt":
            self._update_windows_processes(result)
        self._sample_process_activity(result)
        result["processes"].sort(key=lambda p: p["used_memory"], reverse=True)
        return result

    def _sample_process_activity(self, result: dict[str, Any]) -> None:
        """Attach process CPU share and read+write I/O rates to this frame."""
        previous = self._process_samples
        current: dict[int, _ProcessSample] = {}
        activity: dict[int, tuple[float | None, float | None]] = {}
        rows = result["processes"] + result["process_utilization"]
        for pid in {row["pid"] for row in rows}:
            try:
                process = psutil.Process(pid)
                with process.oneshot():
                    created_at = process.create_time()
                    cpu_times = process.cpu_times()
                    cpu_seconds = cpu_times.user + cpu_times.system
                    io = self._read(process.io_counters)
                    io_bytes = int(io.read_bytes + io.write_bytes) if io is not None else None
                sample = _ProcessSample(
                    created_at, time.monotonic(), cpu_seconds, io_bytes
                )
            except (psutil.Error, ValueError):
                activity[pid] = (None, None)
                continue
            current[pid] = sample
            before = previous.get(pid)
            cpu_percent = None
            disk_bytes_per_sec = None
            if before is not None and before.created_at == created_at:
                elapsed = sample.sampled_at - before.sampled_at
                if elapsed > 0:
                    cpu_percent = min(100.0, max(
                        0.0, (cpu_seconds - before.cpu_seconds)
                        / elapsed / self._logical_cpus * 100
                    ))
                    if io_bytes is not None and before.io_bytes is not None:
                        disk_bytes_per_sec = max(
                            0.0, (io_bytes - before.io_bytes) / elapsed
                        )
            activity[pid] = (cpu_percent, disk_bytes_per_sec)
        self._process_samples = current

        nvml_usage = {
            (row["gpu_id"], row["pid"]): row.get("sm_util")
            for row in result["process_utilization"]
        }
        for row in rows:
            row["cpu_percent"], row["disk_bytes_per_sec"] = activity.get(
                row["pid"], (None, None)
            )
            if row.get("gpu_percent") is None:
                row["gpu_percent"] = nvml_usage.get((row["gpu_id"], row["pid"]))

    def _cuda_luid_map(self) -> dict[str, int]:
        """Match CUDA Driver LUIDs to NVML PCI IDs without creating a context."""
        import ctypes

        driver = ctypes.WinDLL("nvcuda.dll")
        driver.cuInit.argtypes = [ctypes.c_uint]
        driver.cuDeviceGetCount.argtypes = [ctypes.POINTER(ctypes.c_int)]
        driver.cuDeviceGet.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_int]
        driver.cuDeviceGetLuid.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint), ctypes.c_int
        ]
        driver.cuDeviceGetPCIBusId.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
        if driver.cuInit(0) != 0:
            raise RuntimeError("CUDA driver initialization failed")
        count = ctypes.c_int()
        if driver.cuDeviceGetCount(ctypes.byref(count)) != 0:
            raise RuntimeError("CUDA device enumeration failed")
        nvml_by_pci = {}
        for index in range(self.nvml.nvmlDeviceGetCount()):
            handle = self.nvml.nvmlDeviceGetHandleByIndex(index)
            pci = self.nvml.nvmlDeviceGetPciInfo(handle).busId
            key = _pci_key(pci)
            if key is not None:
                nvml_by_pci[key] = index
        luid_map: dict[str, int] = {}
        for ordinal in range(count.value):
            device = ctypes.c_int()
            if driver.cuDeviceGet(ctypes.byref(device), ordinal) != 0:
                continue
            bus_id = ctypes.create_string_buffer(32)
            luid = ctypes.create_string_buffer(8)
            node_mask = ctypes.c_uint()
            if driver.cuDeviceGetPCIBusId(bus_id, 32, device.value) != 0:
                continue
            if driver.cuDeviceGetLuid(luid, ctypes.byref(node_mask), device.value) != 0:
                continue
            bus_key = _pci_key(bus_id.value)
            if bus_key is None:
                continue
            gpu_id = nvml_by_pci.get(bus_key)
            if gpu_id is None:
                continue
            low, high = struct.unpack("<II", luid.raw)
            luid_map[f"0X{high:08X}_0X{low:08X}"] = gpu_id
        return luid_map

    def _update_windows_processes(self, result: dict[str, Any]) -> None:
        if self._luid_by_gpu is None:
            try:
                self._luid_by_gpu = self._cuda_luid_map()
                if not self._luid_by_gpu:
                    raise RuntimeError("No NVIDIA LUID could be matched to NVML")
            except Exception as exc:
                self._wmi_error = f"Windows GPU identity mapping failed: {exc}"
                self._luid_by_gpu = {}
        if not self._luid_by_gpu:
            result["errors"].append(self._wmi_error or "Windows GPU identity unavailable")
            return
        collected_this_tick = False
        if self._wmi_future is not None and self._wmi_future.done():
            try:
                sample = self._wmi_future.result()
                self._wmi_processes = sample.processes
                self._wmi_adapter_memory = sample.adapters
                self._wmi_sampled_at = time.time()
                self._wmi_error = None
            except Exception as exc:
                self._wmi_error = f"Windows GPU process counters unavailable: {exc}"
                self._wmi_processes = []
                self._wmi_adapter_memory = []
                self._wmi_sampled_at = None
            self._wmi_future = None
            collected_this_tick = True
        if (
            not collected_this_tick and self._wmi_future is None
            and time.monotonic() - self._wmi_requested_at >= 2
        ):
            gpu_names = {g["id"]: g["name"] for g in result["gpus"]}
            assert self._wmi_executor is not None
            self._wmi_future = self._wmi_executor.submit(
                self._query_windows_processes, self._process_names().copy(), gpu_names
            )
            self._wmi_requested_at = time.monotonic()
        if self._wmi_error:
            result["errors"].append(self._wmi_error)
        existing = {(p["gpu_id"], p["pid"]): p for p in result["processes"]}
        for process in self._wmi_processes:
            key = (process["gpu_id"], process["pid"])
            if key in existing:
                existing[key]["used_memory"] = max(existing[key]["used_memory"], process["used_memory"])
                existing[key]["shared_memory"] = process["shared_memory"]
                existing[key]["memory_source"] = process["memory_source"]
                existing[key]["gpu_percent"] = process["gpu_percent"]
            else:
                result["processes"].append(dict(process))
        result["processes_sampled_at"] = self._wmi_sampled_at
        result["windows_adapter_memory"] = [dict(row) for row in self._wmi_adapter_memory]
        result["source"] = "NVML + Windows GPU counters"

    def _query_windows_processes(
        self, names: dict[int, str], gpu_names: dict[int, str]
    ) -> _WindowsMemorySample:
        try:
            if self._wmi is None:
                import pythoncom
                import wmi

                pythoncom.CoInitialize()
                self._wmi_initialized = True
                self._wmi = wmi.WMI()
            counters = self._wmi.Win32_PerfFormattedData_GPUPerformanceCounters_GPUProcessMemory()
        except Exception as exc:
            raise RuntimeError(f"WMI query failed: {exc}") from exc
        grouped: dict[tuple[int, int], dict[str, Any]] = {}
        luid_map = self._luid_by_gpu
        assert luid_map is not None
        adapters: dict[int, dict[str, Any]] = {}
        adapter_counters = self._read(
            lambda: self._wmi.Win32_PerfFormattedData_GPUPerformanceCounters_GPUAdapterMemory(),
            [],
        )
        for counter in adapter_counters:
            match = re.search(
                r"luid_(0x[0-9a-f]+)_(0x[0-9a-f]+)", counter.Name, re.IGNORECASE
            )
            if match is None:
                continue
            luid = f"{match.group(1)}_{match.group(2)}".upper()
            gpu_id = luid_map.get(luid)
            if gpu_id is None:
                continue
            adapter = adapters.setdefault(gpu_id, {
                "gpu_id": gpu_id, "dedicated_usage": 0, "shared_usage": 0,
            })
            adapter["dedicated_usage"] += self._read(
                lambda: int(counter.DedicatedUsage), 0
            )
            adapter["shared_usage"] += self._read(
                lambda: int(counter.SharedUsage), 0
            )
        engine_load = self._read_engine_load(luid_map)
        for counter in counters:
            identity = _counter_identity(counter.Name)
            if identity is None:
                continue
            pid, luid = identity
            gpu_id = luid_map.get(luid)
            if gpu_id is None:
                continue
            dedicated = self._read(lambda: int(counter.DedicatedUsage), 0)
            shared = self._read(lambda: int(counter.SharedUsage), 0)
            if dedicated <= 0 and shared <= 0:
                continue
            key = (gpu_id, pid)
            if key not in grouped:
                grouped[key] = {
                    "pid": pid, "name": names.get(pid, "Unknown"),
                    "gpu_id": gpu_id,
                    "gpu_name": gpu_names.get(gpu_id, f"GPU {gpu_id}"),
                    "used_memory": 0, "shared_memory": 0,
                    "memory_source": "Windows GPU counters",
                    "gpu_percent": engine_load.get(key),
                }
            grouped[key]["used_memory"] += dedicated
            grouped[key]["shared_memory"] += shared
        return _WindowsMemorySample(
            [process for process in grouped.values() if process["used_memory"] > 0],
            [adapters[gpu_id] for gpu_id in sorted(adapters)],
        )

    def _read_engine_load(self, luid_map: dict[str, int]) -> dict[tuple[int, int], float]:
        """Batch-sample PDH engine rates without per-instance WMI COM reads."""
        try:
            import win32pdh

            query = win32pdh.OpenQuery()
        except Exception as exc:
            logger.debug("GPU Engine counters unavailable: %s", exc)
            return {}
        handles: list[tuple[Any, tuple[int, int]]] = []
        try:
            for luid in luid_map:
                path = (
                    r"\GPU Engine(pid_*_luid_" + luid.lower()
                    + r"_*)\Utilization Percentage"
                )
                for expanded in self._read(
                    lambda: win32pdh.ExpandCounterPath(path), []
                ):
                    identity = _counter_identity(expanded)
                    if identity is None:
                        continue
                    pid, actual_luid = identity
                    gpu_id = luid_map.get(actual_luid)
                    if gpu_id is None:
                        continue
                    handle = self._read(lambda: win32pdh.AddCounter(query, expanded))
                    if handle is not None:
                        handles.append((handle, (gpu_id, pid)))
            if not handles:
                return {}
            win32pdh.CollectQueryData(query)
            time.sleep(0.5)
            win32pdh.CollectQueryData(query)
            engine_load: dict[tuple[int, int], float] = {}
            for handle, key in handles:
                reading = self._read(
                    lambda: win32pdh.GetFormattedCounterValue(
                        handle, win32pdh.PDH_FMT_DOUBLE
                    )
                )
                if reading is None:
                    continue
                usage = reading[1]
                if not isinstance(usage, (float, int)) or not math.isfinite(usage):
                    continue
                engine_load[key] = max(
                    engine_load.get(key, 0.0), min(100.0, max(0.0, float(usage)))
                )
            return engine_load
        except Exception as exc:
            logger.debug("GPU Engine counter sampling failed: %s", exc)
            return {}
        finally:
            self._read(lambda: win32pdh.CloseQuery(query))

    def _release_wmi(self) -> None:
        if self._wmi_initialized:
            import gc
            self._wmi = None
            gc.collect()
            self._wmi_initialized = False

    def _capture_processes(
        self, result: dict[str, Any], index: int, handle: Any, name: str,
        total: int, names: dict[int, str],
    ) -> None:
        nv = self.nvml
        by_pid: dict[int, int] = {}
        getters = (
            nv.nvmlDeviceGetComputeRunningProcesses,
            nv.nvmlDeviceGetGraphicsRunningProcesses,
        )
        for getter in getters:
            for proc in self._read(lambda: getter(handle), []):
                size = getattr(proc, "usedGpuMemory", 0) or 0
                if not isinstance(size, int) or size <= 0 or size > total:
                    continue
                # The same PID can appear in both NVML lists for one allocation.
                by_pid[proc.pid] = max(by_pid.get(proc.pid, 0), size)
        for pid, size in by_pid.items():
            result["processes"].append({
                "pid": pid, "name": names.get(pid, "Unknown"),
                "gpu_id": index, "gpu_name": name,
                "used_memory": size, "shared_memory": None, "memory_source": "NVML",
            })

    def _capture_utilization(
        self, result: dict[str, Any], index: int, handle: Any, names: dict[int, str],
    ) -> None:
        nv = self.nvml
        samples = self._read(
            lambda: nv.nvmlDeviceGetProcessUtilization(handle, self._util_since.get(index, 0)), []
        )
        latest: dict[int, Any] = {}
        newest = self._util_since.get(index, 0)
        for sample in samples:
            timestamp = getattr(sample, "timeStamp", 0)
            newest = max(newest, timestamp)
            if sample.pid not in latest or timestamp > getattr(latest[sample.pid], "timeStamp", 0):
                latest[sample.pid] = sample
        self._util_since[index] = newest
        for pid, sample in latest.items():
            result["process_utilization"].append({
                "pid": pid, "name": names.get(pid, "Unknown"), "gpu_id": index,
                "sm_util": sample.smUtil, "mem_util": sample.memUtil,
                "enc_util": sample.encUtil, "dec_util": sample.decUtil,
            })


monitor = GPUMonitor()
