import os
import struct
import logging
import time
import psutil
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)

PID_CACHE_TTL = 2.0


def _luid_bytes_to_key(luid: bytes) -> str:
    low, high = struct.unpack("<II", luid[:8])
    return f"0x{high:08X}_0x{low:08X}"


def _parse_gpu_counter_name(name: str) -> Dict[str, Any]:
    parts = name.split("_")
    out: Dict[str, Any] = {"pid": None, "luid": None, "phys": None}
    i = 0
    while i < len(parts):
        if parts[i] == "pid" and i + 1 < len(parts):
            try:
                out["pid"] = int(parts[i + 1])
            except ValueError:
                pass
            i += 2
        elif parts[i] == "luid" and i + 2 < len(parts):
            out["luid"] = f"{parts[i + 1]}_{parts[i + 2]}"
            i += 3
        elif parts[i] == "phys" and i + 1 < len(parts):
            out["phys"] = parts[i + 1]
            i += 2
        else:
            i += 1
    return out


class GPUMonitor:
    def __init__(self):
        try:
            import pynvml
            pynvml.nvmlInit()
            self.pynvml = pynvml
            self.nvml_initialized = True
        except Exception:
            self.nvml_initialized = False

        self.nvidia_luid_map: Dict[str, Dict[str, Any]] = {}
        self._pid_name_cache: Dict[int, str] = {}
        self._pid_name_cache_time: float = 0.0
        self._wmi = None
        self._wmi_executor: Optional[ThreadPoolExecutor] = None

        # CUDA Runtime device info (static, fetch once)
        self.cuda_available = False
        self.cuda_props: List[Dict[str, Any]] = []
        self.cuda_versions: Dict[str, int] = {}
        try:
            from cuda.bindings import runtime as cudart
            self._cudart = cudart
            err, count = cudart.cudaGetDeviceCount()
            if count > 0:
                self.cuda_available = True
                for i in range(count):
                    err, p = cudart.cudaGetDeviceProperties(i)
                    dev_name = (
                        p.name.decode() if isinstance(p.name, bytes) else str(p.name)
                    )
                    luid_key = _luid_bytes_to_key(p.luid)
                    self.nvidia_luid_map[luid_key] = {
                        "gpu_id": i,
                        "name": dev_name,
                    }
                    self.cuda_props.append({
                        "sm_count": p.multiProcessorCount,
                        "max_threads_per_sm": p.maxThreadsPerMultiProcessor,
                        "max_threads_per_block": p.maxThreadsPerBlock,
                        "warp_size": p.warpSize,
                        "max_warps_per_sm": p.maxThreadsPerMultiProcessor // p.warpSize,
                        "max_blocks_per_sm": p.maxBlocksPerMultiProcessor,
                        "shared_mem_per_block": p.sharedMemPerBlock,
                        "shared_mem_per_sm": p.sharedMemPerMultiprocessor,
                        "shared_mem_per_block_optin": p.sharedMemPerBlockOptin,
                        "regs_per_block": p.regsPerBlock,
                        "regs_per_sm": p.regsPerMultiprocessor,
                        "l2_cache_size": p.l2CacheSize,
                        "persisting_l2_max": p.persistingL2CacheMaxSize,
                        "total_global_mem": p.totalGlobalMem,
                        "total_const_mem": p.totalConstMem,
                        "compute_capability": f"{p.major}.{p.minor}",
                        "concurrent_kernels": p.concurrentKernels,
                        "async_engine_count": p.asyncEngineCount,
                        "ecc_enabled": p.ECCEnabled,
                        "compute_preemption": p.computePreemptionSupported,
                        "cooperative_launch": p.cooperativeLaunch,
                        "managed_memory": p.managedMemory,
                        "unified_addressing": p.unifiedAddressing,
                        "mps_enabled": p.mpsEnabled,
                    })
                try:
                    err, rtv = cudart.cudaRuntimeGetVersion()
                    err, drv = cudart.cudaDriverGetVersion()
                    self.cuda_versions = {
                        "runtime": rtv, "driver": drv
                    }
                except Exception:
                    pass
        except Exception:
            pass

        if os.name == "nt":
            self._wmi_executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="wmi"
            )
            try:
                self._wmi_executor.submit(self._init_wmi).result(timeout=15)
            except Exception as e:
                logger.warning("WMI initialization failed: %s", e)
                self._wmi_executor.shutdown(wait=False, cancel_futures=True)
                self._wmi_executor = None

    def _init_wmi(self) -> None:
        import pythoncom
        import wmi

        pythoncom.CoInitialize()
        self._wmi = wmi.WMI()

    def _get_pid_name_map(self) -> Dict[int, str]:
        now = time.monotonic()
        if (
            self._pid_name_cache
            and now - self._pid_name_cache_time < PID_CACHE_TTL
        ):
            return self._pid_name_cache

        pid_name_map: Dict[int, str] = {}
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                pid_name_map[proc.info["pid"]] = proc.info["name"]
            except Exception:
                pass
        self._pid_name_cache = pid_name_map
        self._pid_name_cache_time = now
        return pid_name_map

    def _aggregate_processes(
        self, process_list: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        aggregated: Dict[Tuple[int, Any], Dict[str, Any]] = {}
        for p in process_list:
            key = (p["pid"], p["gpu_id"])
            if key not in aggregated:
                aggregated[key] = p
            else:
                aggregated[key]["used_memory"] += p["used_memory"]
                aggregated[key]["shared_memory"] += p.get("shared_memory", 0)

        result = list(aggregated.values())
        result.sort(key=lambda x: x["used_memory"], reverse=True)
        return result

    def _get_process_info_wmi(
        self, pid_name_map: Dict[int, str]
    ) -> List[Dict[str, Any]]:
        if self._wmi is None or not self.nvidia_luid_map:
            return []

        process_list: List[Dict[str, Any]] = []
        try:
            gpu_counters = (
                self._wmi.Win32_PerfFormattedData_GPUPerformanceCounters_GPUProcessMemory()
            )
            for counter in gpu_counters:
                inst = _parse_gpu_counter_name(counter.Name)
                pid = inst["pid"]
                luid = inst["luid"]
                if pid is None or luid is None:
                    continue

                gpu_info = self.nvidia_luid_map.get(luid)
                if gpu_info is None:
                    continue

                dedicated_bytes = int(counter.DedicatedUsage)
                shared_bytes = int(counter.SharedUsage)
                if dedicated_bytes <= 0 and shared_bytes <= 0:
                    continue

                process_list.append({
                    "pid": pid,
                    "name": pid_name_map.get(pid, "Unknown"),
                    "gpu_id": gpu_info["gpu_id"],
                    "gpu_name": gpu_info["name"],
                    "used_memory": dedicated_bytes,
                    "shared_memory": shared_bytes,
                })
        except Exception as e:
            logger.warning("WMI process info query failed: %s", e)
            return []

        return self._aggregate_processes(process_list)

    def get_system_info(self) -> Dict[str, Any]:
        return {
            "cpu_percent": psutil.cpu_percent(interval=None),
            "memory": {
                "total": psutil.virtual_memory().total,
                "used": psutil.virtual_memory().used,
                "percent": psutil.virtual_memory().percent
            }
        }

    def get_gpu_info(self) -> List[Dict[str, Any]]:
        if not self.nvml_initialized:
            return []

        gpu_list = []
        device_count = self.pynvml.nvmlDeviceGetCount()

        # Driver version (once)
        try:
            driver_version = self.pynvml.nvmlSystemGetDriverVersion()
        except self.pynvml.NVMLError:
            driver_version = "N/A"

        for i in range(device_count):
            handle = self.pynvml.nvmlDeviceGetHandleByIndex(i)
            name = self.pynvml.nvmlDeviceGetName(handle)
            memory = self.pynvml.nvmlDeviceGetMemoryInfo(handle)
            utilization = self.pynvml.nvmlDeviceGetUtilizationRates(handle)

            # --- Basic metrics (already had) ---
            try:
                temp = self.pynvml.nvmlDeviceGetTemperature(
                    handle, self.pynvml.NVML_TEMPERATURE_GPU)
            except self.pynvml.NVMLError:
                temp = 0

            try:
                fan_speed = self.pynvml.nvmlDeviceGetFanSpeed(handle)
            except self.pynvml.NVMLError:
                fan_speed = 0

            try:
                power_usage = self.pynvml.nvmlDeviceGetPowerUsage(handle) / 1000.0
                power_limit = self.pynvml.nvmlDeviceGetEnforcedPowerLimit(handle) / 1000.0
            except self.pynvml.NVMLError:
                power_usage = 0
                power_limit = 0

            try:
                enc_util, _ = self.pynvml.nvmlDeviceGetEncoderUtilization(handle)
            except self.pynvml.NVMLError:
                enc_util = 0

            try:
                dec_util, _ = self.pynvml.nvmlDeviceGetDecoderUtilization(handle)
            except self.pynvml.NVMLError:
                dec_util = 0

            try:
                clock_graphics = self.pynvml.nvmlDeviceGetClockInfo(
                    handle, self.pynvml.NVML_CLOCK_GRAPHICS)
                clock_mem = self.pynvml.nvmlDeviceGetClockInfo(
                    handle, self.pynvml.NVML_CLOCK_MEM)
            except self.pynvml.NVMLError:
                clock_graphics = 0
                clock_mem = 0

            try:
                pcie_tx = self.pynvml.nvmlDeviceGetPcieThroughput(
                    handle, self.pynvml.NVML_PCIE_UTIL_TX_BYTES)
                pcie_rx = self.pynvml.nvmlDeviceGetPcieThroughput(
                    handle, self.pynvml.NVML_PCIE_UTIL_RX_BYTES)
            except self.pynvml.NVMLError:
                pcie_tx = 0
                pcie_rx = 0

            # --- NEW: Static device info ---
            try:
                cuda_cap = self.pynvml.nvmlDeviceGetCudaComputeCapability(handle)
                cuda_cap_str = f"{cuda_cap[0]}.{cuda_cap[1]}"
            except self.pynvml.NVMLError:
                cuda_cap_str = "N/A"

            try:
                num_cores = self.pynvml.nvmlDeviceGetNumGpuCores(handle)
            except self.pynvml.NVMLError:
                num_cores = 0

            try:
                arch = self.pynvml.nvmlDeviceGetArchitecture(handle)
            except self.pynvml.NVMLError:
                arch = -1

            arch_map = {
                2: "Kepler", 3: "Maxwell", 4: "Pascal",
                5: "Volta", 6: "Turing", 7: "Ampere",
                8: "Ada Lovelace", 9: "Hopper", 10: "Blackwell",
            }
            arch_name = arch_map.get(arch, f"Unknown ({arch})")

            try:
                mem_bus_width = self.pynvml.nvmlDeviceGetMemoryBusWidth(handle)
            except self.pynvml.NVMLError:
                mem_bus_width = 0

            try:
                vbios = self.pynvml.nvmlDeviceGetVbiosVersion(handle)
            except self.pynvml.NVMLError:
                vbios = "N/A"

            try:
                uuid = self.pynvml.nvmlDeviceGetUUID(handle)
            except self.pynvml.NVMLError:
                uuid = "N/A"

            # --- NEW: BAR1 memory ---
            try:
                bar1 = self.pynvml.nvmlDeviceGetBAR1MemoryInfo(handle)
                bar1_total = bar1.bar1Total
                bar1_used = bar1.bar1Used
            except self.pynvml.NVMLError:
                bar1_total = 0
                bar1_used = 0

            # --- NEW: Performance state ---
            try:
                pstate = self.pynvml.nvmlDeviceGetPerformanceState(handle)
            except self.pynvml.NVMLError:
                pstate = -1

            # --- NEW: Fan RPM & num fans ---
            try:
                fan_rpm = self.pynvml.nvmlDeviceGetFanSpeedRPM(handle)
            except self.pynvml.NVMLError:
                fan_rpm = 0

            try:
                num_fans = self.pynvml.nvmlDeviceGetNumFans(handle)
            except self.pynvml.NVMLError:
                num_fans = 0

            # --- NEW: PCIe link info ---
            try:
                pcie_gen = self.pynvml.nvmlDeviceGetCurrPcieLinkGeneration(handle)
                pcie_width = self.pynvml.nvmlDeviceGetCurrPcieLinkWidth(handle)
                pcie_max_gen = self.pynvml.nvmlDeviceGetMaxPcieLinkGeneration(handle)
                pcie_max_width = self.pynvml.nvmlDeviceGetMaxPcieLinkWidth(handle)
            except self.pynvml.NVMLError:
                pcie_gen = 0
                pcie_width = 0
                pcie_max_gen = 0
                pcie_max_width = 0

            # --- NEW: JPEG / OFA engine utilization ---
            try:
                jr = self.pynvml.nvmlDeviceGetJpgUtilization(handle)
                jpg_util = jr[0] if isinstance(jr, (list, tuple)) else jr
            except self.pynvml.NVMLError:
                jpg_util = 0

            try:
                ofr = self.pynvml.nvmlDeviceGetOfaUtilization(handle)
                ofa_util = ofr[0] if isinstance(ofr, (list, tuple)) else ofr
            except self.pynvml.NVMLError:
                ofa_util = 0

            # --- NEW: Total energy consumption ---
            try:
                total_energy_mj = self.pynvml.nvmlDeviceGetTotalEnergyConsumption(handle)
                total_energy_kwh = total_energy_mj / 3_600_000_000.0
            except self.pynvml.NVMLError:
                total_energy_kwh = 0

            # --- NEW: Temperature margin ---
            try:
                temp_margin = self.pynvml.nvmlDeviceGetMarginTemperature(handle)
            except self.pynvml.NVMLError:
                temp_margin = 0

            # --- NEW: Clock throttle reasons ---
            try:
                throttle = self.pynvml.nvmlDeviceGetCurrentClocksThrottleReasons(handle)
            except self.pynvml.NVMLError:
                throttle = 0

            throttle_reasons = []
            reason_map = {
                0x01: "Idle",
                0x02: "App Clock Setting",
                0x04: "SW Power Cap",
                0x08: "HW Slowdown",
                0x10: "Sync Boost",
                0x20: "SW Thermal Slowdown",
                0x40: "HW Thermal Slowdown",
                0x80: "HW Power Brake Slowdown",
                0x100: "Display Clock Setting",
            }
            for bit, reason in reason_map.items():
                if throttle & bit:
                    throttle_reasons.append(reason)
            if not throttle_reasons:
                throttle_reasons.append("None")

            # --- NEW: Power management limits ---
            try:
                pmd = self.pynvml
                power_default = pmd.nvmlDeviceGetPowerManagementDefaultLimit(
                    handle) / 1000.0
                constraints = pmd.nvmlDeviceGetPowerManagementLimitConstraints(
                    handle)
                power_min = constraints[0] / 1000.0
                power_max = constraints[1] / 1000.0
            except self.pynvml.NVMLError:
                power_default = 0
                power_min = 0
                power_max = 0

            # --- NEW: Supported memory clocks ---
            try:
                supported_mem_clocks = list(
                    self.pynvml.nvmlDeviceGetSupportedMemoryClocks(handle))
            except self.pynvml.NVMLError:
                supported_mem_clocks = []

            gpu_list.append({
                "id": i,
                "name": name,
                "driver_version": driver_version,
                "memory": {
                    "total": memory.total,
                    "used": memory.used,
                    "free": memory.free,
                    "percent": (memory.used / memory.total) * 100 if memory.total > 0 else 0
                },
                "utilization": {
                    "gpu": utilization.gpu,
                    "memory": utilization.memory
                },
                "temperature": temp,
                "temperature_margin": temp_margin,
                "fan_speed": fan_speed,
                "fan_rpm": fan_rpm,
                "num_fans": num_fans,
                "power": {
                    "usage": power_usage,
                    "limit": power_limit,
                    "default_limit": power_default,
                    "min_limit": power_min,
                    "max_limit": power_max,
                },
                "clocks": {
                    "graphics": clock_graphics,
                    "memory": clock_mem,
                },
                "engines": {
                    "encoder": enc_util,
                    "decoder": dec_util,
                    "jpeg": jpg_util,
                    "ofa": ofa_util,
                },
                "pcie": {
                    "tx": pcie_tx,
                    "rx": pcie_rx,
                    "gen": pcie_gen,
                    "width": pcie_width,
                    "max_gen": pcie_max_gen,
                    "max_width": pcie_max_width,
                },
                "device_info": {
                    "uuid": uuid,
                    "architecture": arch_name,
                    "cuda_capability": cuda_cap_str,
                    "num_cores": num_cores,
                    "memory_bus_width": mem_bus_width,
                    "vbios_version": vbios,
                    "supported_mem_clocks": supported_mem_clocks,
                },
                "bar1": {
                    "total": bar1_total,
                    "used": bar1_used,
                },
                "performance_state": f"P{pstate}" if pstate >= 0 else "N/A",
                "throttle_reasons": throttle_reasons,
                "total_energy_kwh": round(total_energy_kwh, 4),
            })

        return gpu_list

    def get_process_info(self) -> List[Dict[str, Any]]:
        pid_name_map = self._get_pid_name_map()

        if os.name == "nt":
            if self._wmi_executor is None:
                return []
            try:
                return self._wmi_executor.submit(
                    self._get_process_info_wmi, pid_name_map
                ).result(timeout=10)
            except Exception as e:
                logger.warning("WMI process info failed: %s", e)
                return []

        if not self.nvml_initialized:
            return []

        process_list: List[Dict[str, Any]] = []
        device_count = self.pynvml.nvmlDeviceGetCount()
        for i in range(device_count):
            handle = self.pynvml.nvmlDeviceGetHandleByIndex(i)
            try:
                gpu_name = self.pynvml.nvmlDeviceGetName(handle)
            except self.pynvml.NVMLError:
                gpu_name = f"GPU {i}"

            compute_processes = self.pynvml.nvmlDeviceGetComputeRunningProcesses(
                handle
            )
            graphics_processes = self.pynvml.nvmlDeviceGetGraphicsRunningProcesses(
                handle
            )
            all_processes = compute_processes + graphics_processes

            for p in all_processes:
                pid = p.pid
                used_bytes = p.usedGpuMemory if p.usedGpuMemory else 0
                if used_bytes > 0:
                    process_list.append({
                        "pid": pid,
                        "name": pid_name_map.get(pid, "Unknown"),
                        "gpu_id": i,
                        "gpu_name": gpu_name,
                        "used_memory": used_bytes,
                        "shared_memory": 0,
                    })

        return self._aggregate_processes(process_list)

    def get_process_utilization(self) -> List[Dict[str, Any]]:
        """Get per-process GPU utilization (SM, Mem, Enc, Dec)."""
        if not self.nvml_initialized:
            return []

        pid_name_map = self._get_pid_name_map()

        result: List[Dict[str, Any]] = []
        device_count = self.pynvml.nvmlDeviceGetCount()

        for i in range(device_count):
            handle = self.pynvml.nvmlDeviceGetHandleByIndex(i)
            try:
                procs = self.pynvml.nvmlDeviceGetProcessUtilization(
                    handle, 0
                )
                for p in procs:
                    result.append({
                        "pid": p.pid,
                        "name": pid_name_map.get(p.pid, "Unknown"),
                        "gpu_id": i,
                        "sm_util": p.smUtil,
                        "mem_util": p.memUtil,
                        "enc_util": p.encUtil,
                        "dec_util": p.decUtil,
                    })
            except self.pynvml.NVMLError:
                pass

        return result

    def get_cuda_info(self) -> Dict[str, Any]:
        """Get CUDA runtime info: device props + live memory."""
        if not self.cuda_available:
            return {"available": False}

        mem_info = []
        try:
            for i in range(len(self.cuda_props)):
                err = self._cudart.cudaSetDevice(i)
                err, free, total = self._cudart.cudaMemGetInfo()
                mem_info.append({
                    "free": free, "total": total,
                    "used": total - free,
                    "percent": ((total - free) / total * 100)
                    if total > 0 else 0,
                })
        except Exception:
            pass

        return {
            "available": True,
            "versions": self.cuda_versions,
            "devices": self.cuda_props,
            "memory": mem_info,
        }


monitor = GPUMonitor()
