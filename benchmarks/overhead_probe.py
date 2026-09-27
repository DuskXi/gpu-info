"""Short, read-only NVML probe for profiling-tool overhead experiments.

Run while the dashboard backend is active. The probe does not create a CUDA
context or allocate GPU memory; it reads NVML and the existing status endpoint.
"""

import argparse
import json
import statistics
import time
from pathlib import Path
from urllib.request import urlopen

import psutil
import pynvml


TOOL_NAMES = ("nsys", "nsight", "gpuview", "wpr", "wpa", "xperf")


def mib(value):
    return round(value / 1048576, 2) if value is not None else None


def summarize(values):
    return {
        "min": round(min(values), 2),
        "median": round(statistics.median(values), 2),
        "max": round(max(values), 2),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("label")
    parser.add_argument("--seconds", type=float, default=12)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    pynvml.nvmlInit()
    handle = pynvml.nvmlDeviceGetHandleByIndex(0)
    samples = []
    start = time.monotonic()
    previous_io = psutil.disk_io_counters()
    try:
        while time.monotonic() - start < args.seconds:
            mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
            util = pynvml.nvmlDeviceGetUtilizationRates(handle)
            try:
                with urlopen("http://127.0.0.1:8001/api/status", timeout=2) as response:
                    frame = json.load(response)
                rows = frame.get("processes", [])
                tool_rows = [
                    row for row in rows
                    if any(name in row.get("name", "").lower() for name in TOOL_NAMES)
                ]
                local = sum(row.get("local_memory") or 0 for row in tool_rows)
                tool_processes = [
                    {"pid": row["pid"], "name": row["name"], "local_mib": mib(row.get("local_memory"))}
                    for row in tool_rows
                ]
            except Exception as exc:
                local = None
                tool_processes = []
                frame = {"error": str(exc)}
            tool_os_rows = []
            for process in psutil.process_iter(["pid", "name", "memory_info", "cpu_times"]):
                try:
                    info = process.info
                    if any(name in (info["name"] or "").lower() for name in TOOL_NAMES):
                        tool_os_rows.append(info)
                except psutil.Error:
                    continue
            current_io = psutil.disk_io_counters()
            samples.append({
                "t": round(time.monotonic() - start, 3),
                "vram_mib": mib(mem.used),
                "gpu_percent": util.gpu,
                "tool_local_mib": mib(local),
                "tool_processes": tool_processes,
                "tool_rss_mib": mib(sum(row["memory_info"].rss for row in tool_os_rows)),
                "tool_cpu_seconds": round(sum(
                    row["cpu_times"].user + row["cpu_times"].system
                    for row in tool_os_rows
                ), 2),
                "system_available_mib": mib(psutil.virtual_memory().available),
                "system_cpu_percent": frame.get("system", {}).get("cpu_percent"),
                "disk_write_mib_since_start": mib(current_io.write_bytes - previous_io.write_bytes),
            })
            time.sleep(max(0, start + len(samples) * 0.5 - time.monotonic()))
    finally:
        pynvml.nvmlShutdown()

    valid = [sample for sample in samples if sample["vram_mib"] is not None]
    result = {
        "label": args.label,
        "duration_seconds": round(time.monotonic() - start, 2),
        "samples": len(samples),
        "device_vram_mib": summarize([sample["vram_mib"] for sample in valid]),
        "tool_local_mib": summarize([
            sample["tool_local_mib"] for sample in samples if sample["tool_local_mib"] is not None
        ]),
        "tool_rss_mib": summarize([sample["tool_rss_mib"] for sample in samples]),
        "gpu_percent": summarize([sample["gpu_percent"] for sample in samples]),
        "disk_write_mib": samples[-1]["disk_write_mib_since_start"] if samples else None,
        "tool_processes_seen": sorted({
            (row["name"], row["pid"], row["local_mib"])
            for sample in samples for row in sample["tool_processes"]
        }),
        "samples_raw": samples,
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "samples_raw"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
