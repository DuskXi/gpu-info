import os
import struct

if os.name == "nt":
    import pythoncom
    import wmi
else:
    import pynvml


def _luid_bytes_to_key(luid: bytes) -> str:
    low, high = struct.unpack("<II", luid[:8])
    return f"0x{high:08X}_0x{low:08X}"


def _parse_gpu_counter_name(name: str) -> dict:
    parts = name.split("_")
    out = {"pid": None, "luid": None, "phys": None}
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


def _build_nvidia_luid_map() -> dict:
    from cuda.bindings import runtime as cudart

    luid_map = {}
    err, count = cudart.cudaGetDeviceCount()
    for i in range(count):
        err, p = cudart.cudaGetDeviceProperties(i)
        dev_name = p.name.decode() if isinstance(p.name, bytes) else str(p.name)
        luid_key = _luid_bytes_to_key(p.luid)
        luid_map[luid_key] = {"gpu_id": i, "name": dev_name}
    return luid_map


def get_process_gpu_memory():
    pythoncom.CoInitialize()
    try:
        w = wmi.WMI()
        nvidia_luids = _build_nvidia_luid_map()

        print(
            f"{'GPU':<6} | {'PID':<10} | {'专用 GPU 内存 (MB)':<20} | "
            f"{'共享 GPU 内存 (MB)':<20}"
        )
        print("-" * 70)

        gpu_counters = (
            w.Win32_PerfFormattedData_GPUPerformanceCounters_GPUProcessMemory()
        )
        aggregated = {}

        for counter in gpu_counters:
            inst = _parse_gpu_counter_name(counter.Name)
            pid = inst["pid"]
            luid = inst["luid"]
            if pid is None or luid is None:
                continue

            gpu_info = nvidia_luids.get(luid)
            if gpu_info is None:
                continue

            dedicated_mb = int(counter.DedicatedUsage) / (1024 * 1024)
            shared_mb = int(counter.SharedUsage) / (1024 * 1024)
            if dedicated_mb <= 0 and shared_mb <= 0:
                continue

            key = (pid, gpu_info["gpu_id"])
            if key not in aggregated:
                aggregated[key] = {
                    "gpu_id": gpu_info["gpu_id"],
                    "pid": pid,
                    "dedicated_mb": dedicated_mb,
                    "shared_mb": shared_mb,
                }
            else:
                aggregated[key]["dedicated_mb"] += dedicated_mb
                aggregated[key]["shared_mb"] += shared_mb

        for entry in sorted(
            aggregated.values(), key=lambda x: x["dedicated_mb"], reverse=True
        ):
            print(
                f"{entry['gpu_id']:<6} | {entry['pid']:<10} | "
                f"{entry['dedicated_mb']:<20.2f} | {entry['shared_mb']:<20.2f}"
            )
    finally:
        pythoncom.CoUninitialize()


def get_nvidia_process_memory():
    pynvml.nvmlInit()
    device_count = pynvml.nvmlDeviceGetCount()

    print(f"{'GPU ID':<8} | {'PID':<10} | {'VRAM 占用 (MB)':<15}")
    print("-" * 40)

    for i in range(device_count):
        handle = pynvml.nvmlDeviceGetHandleByIndex(i)

        compute_processes = pynvml.nvmlDeviceGetComputeRunningProcesses(handle)
        graphics_processes = pynvml.nvmlDeviceGetGraphicsRunningProcesses(handle)

        all_processes = compute_processes + graphics_processes

        for p in all_processes:
            pid = p.pid
            used_mb = (p.usedGpuMemory / (1024 * 1024)) if p.usedGpuMemory else 0

            print(f"{i:<8} | {pid:<10} | {used_mb:<15.2f}")

    pynvml.nvmlShutdown()


if __name__ == "__main__":
    if os.name == "nt":
        get_process_gpu_memory()
    else:
        get_nvidia_process_memory()
