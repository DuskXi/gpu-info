"""Read-only renderers for the latest telemetry frame.

These functions format a stored sample and never query hardware themselves.
"""

from datetime import datetime, timezone
import math
from typing import Any


def _value(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) else None


def _bytes(value: Any) -> str:
    number = _value(value)
    if number is None:
        return "—"
    for unit, size in (("GiB", 1024**3), ("MiB", 1024**2), ("KiB", 1024)):
        if abs(number) >= size:
            return f"{number / size:.1f} {unit}"
    return f"{number:g} B"


def _percent(value: Any) -> str:
    number = _value(value)
    return f"{number:.1f}%" if number is not None else "—"


def _reading(value: Any, unit: str = "") -> str:
    number = _value(value)
    return f"{number:g}{unit}" if number is not None else "—"


def _time(value: Any) -> str:
    number = _value(value)
    if number is None:
        return "—"
    return datetime.fromtimestamp(number, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _cell(value: Any) -> str:
    return str(value if value is not None else "—").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _process_memory(row: dict[str, Any]) -> Any:
    local = row.get("local_memory")
    return local if _value(local) is not None else (
        row.get("used_memory") if row.get("memory_source") == "NVML" else None
    )


def render_status_markdown(frame: dict[str, Any]) -> str:
    """Summarize one frame without treating process memory as a device partition."""
    system = frame.get("system") or {}
    system_memory = system.get("memory") or {}
    gpus = frame.get("gpus") or []
    processes = frame.get("processes") or []
    errors = frame.get("errors") or []
    lines = [
        "# GPU Monitor 状态", "",
        f"- 采样时间：{_time(frame.get('timestamp'))}",
        f"- 序号：{_cell(frame.get('sequence'))}",
        f"- 数据源：{_cell(frame.get('source'))}",
        f"- GPU 数量：{len(gpus)}；进程数量：{len(processes)}", "",
        "## 系统", "",
        f"- CPU 占用：{_percent(system.get('cpu_percent'))}",
        f"- 内存：{_bytes(system_memory.get('used'))} / {_bytes(system_memory.get('total'))}"
        f"（{_percent(system_memory.get('percent'))}）", "",
        "## GPU", "",
        "| GPU | 设备 | 显存已用 / 总量 | GPU 负载 | 显存控制器 | 温度 | 功耗 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for gpu in gpus:
        memory = gpu.get("memory") or {}
        utilization = gpu.get("utilization") or {}
        power = gpu.get("power") or {}
        lines.append(
            f"| {_cell(gpu.get('id'))} | {_cell(gpu.get('name'))} | "
            f"{_bytes(memory.get('used'))} / {_bytes(memory.get('total'))} | "
            f"{_percent(utilization.get('gpu'))} | {_percent(utilization.get('memory'))} | "
            f"{_reading(gpu.get('temperature'), ' °C')} | {_reading(power.get('usage'), ' W')} |"
        )
    if not gpus:
        lines.append("| — | 暂无 NVIDIA GPU 读数 | — | — | — | — | — |")

    lines += [
        "", "## 进程显存", "",
        f"Windows 进程计数采样：{_time(frame.get('processes_sampled_at'))}。"
        "本地用量可能重复包含共享资源，不能相加推算整卡显存；"
        "专用计数可能异常偏高，不代表进程独占 VRAM。", "",
        "| GPU | 进程 | PID | 本地 / NVML 读数 | 专用计数 | CPU | GPU | 磁盘 I/O |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    ranked = sorted(processes, key=lambda row: _value(_process_memory(row)) or -1, reverse=True)
    for row in ranked[:20]:
        disk = _value(row.get("disk_bytes_per_sec"))
        lines.append(
            f"| {_cell(row.get('gpu_id'))} | {_cell(row.get('name'))} | {_cell(row.get('pid'))} | "
            f"{_bytes(_process_memory(row))} | {_bytes(row.get('claimed_memory'))} | "
            f"{_percent(row.get('cpu_percent'))} | {_percent(row.get('gpu_percent'))} | "
            f"{_bytes(disk) + '/s' if disk is not None else '—'} |"
        )
    if not ranked:
        lines.append("| — | 暂无进程读数 | — | — | — | — | — | — |")
    if len(ranked) > 20:
        lines += ["", f"显示占用最高的 20 个进程；完整的 {len(ranked)} 条记录见 `/status.json`。"]

    display = frame.get("display_state")
    if isinstance(display, dict):
        lines += ["", "## 显示状态", "", f"活动显示器：{_cell(display.get('count'))}。"]
        for item in display.get("displays") or []:
            size = f"{item.get('width', '?')} × {item.get('height', '?')}"
            refresh = _reading(item.get("refresh_hz"), " Hz")
            hdr = item.get("hdr_enabled")
            hdr_text = "未知" if hdr is None else ("开" if hdr else "关")
            lines.append(f"- {_cell(item.get('name'))}：{size}，{refresh}，HDR {hdr_text}")
    if errors:
        lines += ["", "## 采集提示", ""]
        lines.extend(f"- {_cell(error)}" for error in errors)
    return "\n".join(lines) + "\n"


# Prometheus text exposition 0.0.4. Names and help strings are fixed; labels are escaped.
_METRICS = {
    "gpu_info_up": "A telemetry frame is available from the collector.",
    "gpu_info_snapshot_timestamp_seconds": "Unix timestamp of the latest frame.",
    "gpu_info_process_sample_timestamp_seconds": "Unix timestamp of the latest process counter sample.",
    "gpu_info_display_sample_timestamp_seconds": "Unix timestamp of the latest display configuration sample.",
    "gpu_info_collection_errors": "Number of errors reported in the latest frame.",
    "gpu_info_gpu_count": "Number of NVIDIA GPUs in the latest frame.",
    "gpu_info_process_count": "Number of GPU processes in the latest frame.",
    "gpu_info_display_count": "Number of active displays, when available.",
    "gpu_info_system_cpu_percent": "System CPU usage percent.",
    "gpu_info_system_memory_total_bytes": "Total system memory in bytes.",
    "gpu_info_system_memory_used_bytes": "Used system memory in bytes.",
    "gpu_info_gpu_memory_total_bytes": "NVML device physical memory total in bytes.",
    "gpu_info_gpu_memory_used_bytes": "NVML device physical memory used in bytes.",
    "gpu_info_gpu_memory_free_bytes": "NVML device physical memory free in bytes.",
    "gpu_info_gpu_utilization_percent": "GPU engine utilization percent.",
    "gpu_info_gpu_memory_controller_utilization_percent": "Memory controller utilization percent.",
    "gpu_info_gpu_temperature_celsius": "GPU temperature in degrees Celsius.",
    "gpu_info_gpu_power_watts": "GPU power draw in watts.",
    "gpu_info_gpu_graphics_clock_mhz": "GPU graphics clock in MHz.",
    "gpu_info_gpu_memory_clock_mhz": "GPU memory clock in MHz.",
    "gpu_info_gpu_bar1_used_bytes": "NVML BAR1 memory used in bytes.",
    "gpu_info_windows_adapter_dedicated_bytes": "Windows adapter dedicated usage in bytes; separate from NVML physical memory.",
    "gpu_info_windows_adapter_shared_bytes": "Windows adapter shared usage in bytes.",
    "gpu_info_process_local_memory_bytes": "Windows process local usage in bytes; shared resources may be counted more than once.",
    "gpu_info_process_claimed_memory_bytes": "Windows process dedicated counter in bytes; may exceed physical capacity.",
    "gpu_info_process_committed_memory_bytes": "Windows process total committed memory in bytes.",
    "gpu_info_process_nonlocal_memory_bytes": "Windows process nonlocal usage in bytes.",
    "gpu_info_process_shared_memory_bytes": "Windows process shared counter in bytes.",
    "gpu_info_process_nvml_memory_bytes": "NVML process memory reading in bytes when Windows local usage is unavailable.",
    "gpu_info_process_cpu_percent": "Process CPU use as percent of the whole host.",
    "gpu_info_process_gpu_percent": "Process GPU utilization percent, from Windows engines or NVML SM.",
    "gpu_info_process_disk_io_bytes_per_second": "Process read plus write disk I/O rate in bytes per second.",
    "gpu_info_process_sm_utilization_percent": "NVML process SM utilization percent.",
    "gpu_info_process_memory_utilization_percent": "NVML process memory utilization percent.",
}


def _labels(values: dict[str, Any]) -> str:
    if not values:
        return ""
    parts = []
    for key, value in values.items():
        escaped = str(value if value is not None else "").replace("\\", "\\\\")
        escaped = escaped.replace("\n", "\\n").replace('"', '\\"')
        parts.append(f'{key}="{escaped}"')
    return "{" + ",".join(parts) + "}"


def render_prometheus(frame: dict[str, Any]) -> str:
    """Expose present numeric values only; never manufacture zero for a missing counter."""
    samples: dict[str, list[str]] = {name: [] for name in _METRICS}

    def add(name: str, value: Any, labels: dict[str, Any] | None = None) -> None:
        number = _value(value)
        if number is not None:
            samples[name].append(f"{name}{_labels(labels or {})} {number}")

    system = frame.get("system") or {}
    memory = system.get("memory") or {}
    gpus = frame.get("gpus") or []
    processes = frame.get("processes") or []
    add("gpu_info_up", 1)
    add("gpu_info_snapshot_timestamp_seconds", frame.get("timestamp"))
    add("gpu_info_process_sample_timestamp_seconds", frame.get("processes_sampled_at"))
    add("gpu_info_display_sample_timestamp_seconds", frame.get("display_sampled_at"))
    add("gpu_info_collection_errors", len(frame.get("errors") or []))
    add("gpu_info_gpu_count", len(gpus))
    add("gpu_info_process_count", len(processes))
    display = frame.get("display_state")
    if isinstance(display, dict):
        add("gpu_info_display_count", display.get("count"))
    add("gpu_info_system_cpu_percent", system.get("cpu_percent"))
    add("gpu_info_system_memory_total_bytes", memory.get("total"))
    add("gpu_info_system_memory_used_bytes", memory.get("used"))

    for gpu in gpus:
        label = {"gpu": gpu.get("id"), "name": gpu.get("name")}
        memory = gpu.get("memory") or {}
        utilization = gpu.get("utilization") or {}
        power = gpu.get("power") or {}
        clocks = gpu.get("clocks") or {}
        bar1 = gpu.get("bar1") or {}
        for metric, value in (
            ("gpu_info_gpu_memory_total_bytes", memory.get("total")),
            ("gpu_info_gpu_memory_used_bytes", memory.get("used")),
            ("gpu_info_gpu_memory_free_bytes", memory.get("free")),
            ("gpu_info_gpu_utilization_percent", utilization.get("gpu")),
            ("gpu_info_gpu_memory_controller_utilization_percent", utilization.get("memory")),
            ("gpu_info_gpu_temperature_celsius", gpu.get("temperature")),
            ("gpu_info_gpu_power_watts", power.get("usage")),
            ("gpu_info_gpu_graphics_clock_mhz", clocks.get("graphics")),
            ("gpu_info_gpu_memory_clock_mhz", clocks.get("memory")),
            ("gpu_info_gpu_bar1_used_bytes", bar1.get("used")),
        ):
            add(metric, value, label)

    for adapter in frame.get("windows_adapter_memory") or []:
        label = {"gpu": adapter.get("gpu_id")}
        add("gpu_info_windows_adapter_dedicated_bytes", adapter.get("dedicated_usage"), label)
        add("gpu_info_windows_adapter_shared_bytes", adapter.get("shared_usage"), label)

    for process in processes:
        label = {"gpu": process.get("gpu_id"), "pid": process.get("pid"),
                 "name": process.get("name")}
        for metric, key in (
            ("gpu_info_process_local_memory_bytes", "local_memory"),
            ("gpu_info_process_claimed_memory_bytes", "claimed_memory"),
            ("gpu_info_process_committed_memory_bytes", "committed_memory"),
            ("gpu_info_process_nonlocal_memory_bytes", "nonlocal_memory"),
            ("gpu_info_process_shared_memory_bytes", "shared_memory"),
            ("gpu_info_process_cpu_percent", "cpu_percent"),
            ("gpu_info_process_gpu_percent", "gpu_percent"),
            ("gpu_info_process_disk_io_bytes_per_second", "disk_bytes_per_sec"),
        ):
            add(metric, process.get(key), label)
        if process.get("memory_source") == "NVML" and _value(process.get("local_memory")) is None:
            add("gpu_info_process_nvml_memory_bytes", process.get("used_memory"), label)

    for process in frame.get("process_utilization") or []:
        label = {"gpu": process.get("gpu_id"), "pid": process.get("pid"),
                 "name": process.get("name")}
        add("gpu_info_process_sm_utilization_percent", process.get("sm_util"), label)
        add("gpu_info_process_memory_utilization_percent", process.get("mem_util"), label)

    lines: list[str] = []
    for name, help_text in _METRICS.items():
        lines.extend((f"# HELP {name} {help_text}", f"# TYPE {name} gauge"))
        lines.extend(samples[name])
    return "\n".join(lines) + "\n"
