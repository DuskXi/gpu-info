"""Read-only renderers for the latest telemetry frame.

These functions format a stored sample and never query hardware themselves.
"""

from datetime import datetime, timezone
import math
import time
from typing import Any


FRAME_STALE_AFTER_SECONDS = 5
PROCESS_STALE_AFTER_SECONDS = 30
DISPLAY_STALE_AFTER_SECONDS = 15


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


def sample_health(frame: dict[str, Any], now: float | None = None) -> dict[str, Any]:
    """Evaluate freshness at request time without changing the captured frame."""
    current = time.time() if now is None else now
    sampled_at = _value(frame.get("timestamp"))
    process_at = _value(frame.get("processes_sampled_at"))
    display_at = _value(frame.get("display_sampled_at"))
    frame_age = max(0.0, current - sampled_at) if sampled_at is not None else None
    process_age = max(0.0, current - process_at) if process_at is not None else None
    display_age = max(0.0, current - display_at) if display_at is not None else None
    fresh = frame_age is not None and frame_age <= FRAME_STALE_AFTER_SECONDS
    windows_processes = "Windows GPU counters" in str(frame.get("source") or "")
    process_fresh = fresh
    if windows_processes:
        process_fresh = fresh and process_age is not None and process_age <= PROCESS_STALE_AFTER_SECONDS
    return {
        "snapshot_age_seconds": frame_age,
        "snapshot_fresh": fresh,
        "process_age_seconds": process_age,
        "process_fresh": process_fresh,
        "display_age_seconds": display_age,
        "display_fresh": fresh and display_age is not None and display_age <= DISPLAY_STALE_AFTER_SECONDS,
        "gpu_available": bool(frame.get("gpus")),
    }


def status_message(frame: dict[str, Any]) -> dict[str, Any]:
    """Add request-time health to the same captured snapshot used elsewhere."""
    return {**frame, "health": sample_health(frame)}


def render_status_markdown(frame: dict[str, Any]) -> str:
    """Summarize one frame without treating process memory as a device partition."""
    system = frame.get("system") or {}
    system_memory = system.get("memory") or {}
    gpus = frame.get("gpus") or []
    processes = frame.get("processes") or []
    errors = frame.get("errors") or []
    health = sample_health(frame)
    freshness = "实时" if health["snapshot_fresh"] else "已过期"
    process_freshness = "实时" if health["process_fresh"] else "已过期或不可用"
    lines = [
        "# GPU Monitor 状态", "",
        f"- 采样时间：{_time(frame.get('timestamp'))}",
        f"- 采样状态：{freshness}（距今 {_reading(health['snapshot_age_seconds'], ' 秒')}）",
        f"- 进程计数状态：{process_freshness}",
        f"- 序号：{_cell(frame.get('sequence'))}",
        f"- 数据源：{_cell(frame.get('source'))}",
        f"- GPU 数量：{len(gpus)}；进程数量：{len(processes)}", "",
    ]
    if not health["snapshot_fresh"]:
        lines += ["> 注意：以下为最后一次成功采样的旧读数。", ""]
    lines += [
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
    ]
    if not health["process_fresh"]:
        lines += ["> 注意：以下进程读数可能已经过期。", ""]
    lines += [
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
        if not health["display_fresh"]:
            lines.append("> 注意：显示配置读数可能已经过期。")
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
    "gpu_info_up": "A fresh telemetry frame with at least one NVIDIA GPU is available.",
    "gpu_info_snapshot_timestamp_seconds": "Unix timestamp of the latest frame.",
    "gpu_info_snapshot_fresh": "Whether the latest frame is no older than five seconds.",
    "gpu_info_process_sample_timestamp_seconds": "Unix timestamp of the latest process counter sample.",
    "gpu_info_process_sample_fresh": "Whether process counters are fresh enough to export.",
    "gpu_info_display_sample_timestamp_seconds": "Unix timestamp of the latest display configuration sample.",
    "gpu_info_display_sample_fresh": "Whether display configuration is fresh enough to export.",
    "gpu_info_collection_errors": "Number of errors reported in the latest frame.",
    "gpu_info_gpu_count": "Number of NVIDIA GPUs in the latest frame.",
    "gpu_info_process_count": "Number of GPU processes in the latest frame.",
    "gpu_info_display_count": "Number of active displays, when available.",
    "gpu_info_system_cpu_ratio": "System CPU usage as a ratio from zero to one.",
    "gpu_info_system_memory_total_bytes": "Total system memory in bytes.",
    "gpu_info_system_memory_used_bytes": "Used system memory in bytes.",
    "gpu_info_gpu_memory_total_bytes": "NVML device physical memory total in bytes.",
    "gpu_info_gpu_memory_used_bytes": "NVML device physical memory used in bytes.",
    "gpu_info_gpu_memory_free_bytes": "NVML device physical memory free in bytes.",
    "gpu_info_gpu_utilization_ratio": "GPU engine utilization as a ratio from zero to one.",
    "gpu_info_gpu_memory_controller_utilization_ratio": "Memory controller utilization as a ratio from zero to one.",
    "gpu_info_gpu_temperature_celsius": "GPU temperature in degrees Celsius.",
    "gpu_info_gpu_power_watts": "GPU power draw in watts.",
    "gpu_info_gpu_graphics_clock_hertz": "GPU graphics clock in hertz.",
    "gpu_info_gpu_memory_clock_hertz": "GPU memory clock in hertz.",
    "gpu_info_gpu_bar1_used_bytes": "NVML BAR1 memory used in bytes.",
    "gpu_info_windows_adapter_dedicated_bytes": "Windows adapter dedicated usage in bytes; separate from NVML physical memory.",
    "gpu_info_windows_adapter_shared_bytes": "Windows adapter shared usage in bytes.",
    "gpu_info_process_local_memory_bytes": "Windows process local usage in bytes; shared resources may be counted more than once.",
    "gpu_info_process_claimed_memory_bytes": "Windows process dedicated counter in bytes; may exceed physical capacity.",
    "gpu_info_process_committed_memory_bytes": "Windows process total committed memory in bytes.",
    "gpu_info_process_nonlocal_memory_bytes": "Windows process nonlocal usage in bytes.",
    "gpu_info_process_shared_memory_bytes": "Windows process shared counter in bytes.",
    "gpu_info_process_nvml_memory_bytes": "NVML process memory reading in bytes when Windows local usage is unavailable.",
    "gpu_info_process_cpu_ratio": "Process CPU use as a ratio of the whole host.",
    "gpu_info_process_gpu_ratio": "Process GPU utilization ratio, from Windows engines or NVML SM.",
    "gpu_info_process_disk_io_bytes_per_second": "Process read plus write disk I/O rate in bytes per second.",
    "gpu_info_process_sm_utilization_ratio": "NVML process SM utilization ratio.",
    "gpu_info_process_memory_utilization_ratio": "NVML process memory utilization ratio.",
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


def render_prometheus(frame: dict[str, Any], include_processes: bool = False) -> str:
    """Expose present numeric values only; never manufacture zero for a missing counter."""
    samples: dict[str, list[str]] = {name: [] for name in _METRICS}

    def add(name: str, value: Any, labels: dict[str, Any] | None = None) -> None:
        number = _value(value)
        if number is not None:
            samples[name].append(f"{name}{_labels(labels or {})} {number}")

    def ratio(value: Any) -> float | None:
        number = _value(value)
        return number / 100 if number is not None else None

    def hertz(value: Any) -> int | float | None:
        number = _value(value)
        return number * 1_000_000 if number is not None else None

    system = frame.get("system") or {}
    memory = system.get("memory") or {}
    gpus = frame.get("gpus") or []
    processes = frame.get("processes") or []
    health = sample_health(frame)
    add("gpu_info_up", int(health["snapshot_fresh"] and health["gpu_available"]))
    add("gpu_info_snapshot_timestamp_seconds", frame.get("timestamp"))
    add("gpu_info_snapshot_fresh", int(health["snapshot_fresh"]))
    add("gpu_info_process_sample_timestamp_seconds", frame.get("processes_sampled_at"))
    add("gpu_info_process_sample_fresh", int(health["process_fresh"]))
    add("gpu_info_display_sample_timestamp_seconds", frame.get("display_sampled_at"))
    add("gpu_info_display_sample_fresh", int(health["display_fresh"]))
    add("gpu_info_collection_errors", len(frame.get("errors") or []))
    if health["snapshot_fresh"]:
        add("gpu_info_gpu_count", len(gpus))
        if health["process_fresh"]:
            add("gpu_info_process_count", len(processes))
        display = frame.get("display_state")
        if isinstance(display, dict) and health["display_fresh"]:
            add("gpu_info_display_count", display.get("count"))
    add("gpu_info_system_cpu_ratio", ratio(system.get("cpu_percent")) if health["snapshot_fresh"] else None)
    if health["snapshot_fresh"]:
        add("gpu_info_system_memory_total_bytes", memory.get("total"))
        add("gpu_info_system_memory_used_bytes", memory.get("used"))

    fresh_gpus = gpus if health["snapshot_fresh"] else []
    for gpu in fresh_gpus:
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
            ("gpu_info_gpu_utilization_ratio", ratio(utilization.get("gpu"))),
            ("gpu_info_gpu_memory_controller_utilization_ratio", ratio(utilization.get("memory"))),
            ("gpu_info_gpu_temperature_celsius", gpu.get("temperature")),
            ("gpu_info_gpu_power_watts", power.get("usage")),
            ("gpu_info_gpu_graphics_clock_hertz", hertz(clocks.get("graphics"))),
            ("gpu_info_gpu_memory_clock_hertz", hertz(clocks.get("memory"))),
            ("gpu_info_gpu_bar1_used_bytes", bar1.get("used")),
        ):
            add(metric, value, label)

    fresh_adapters = (frame.get("windows_adapter_memory") or []) if health["process_fresh"] else []
    for adapter in fresh_adapters:
        label = {"gpu": adapter.get("gpu_id")}
        add("gpu_info_windows_adapter_dedicated_bytes", adapter.get("dedicated_usage"), label)
        add("gpu_info_windows_adapter_shared_bytes", adapter.get("shared_usage"), label)

    fresh_processes = processes if include_processes and health["process_fresh"] else []
    for process in fresh_processes:
        label = {"gpu": process.get("gpu_id"), "pid": process.get("pid"),
                 "name": process.get("name")}
        for metric, key in (
            ("gpu_info_process_local_memory_bytes", "local_memory"),
            ("gpu_info_process_claimed_memory_bytes", "claimed_memory"),
            ("gpu_info_process_committed_memory_bytes", "committed_memory"),
            ("gpu_info_process_nonlocal_memory_bytes", "nonlocal_memory"),
            ("gpu_info_process_shared_memory_bytes", "shared_memory"),
            ("gpu_info_process_disk_io_bytes_per_second", "disk_bytes_per_sec"),
        ):
            add(metric, process.get(key), label)
        add("gpu_info_process_cpu_ratio", ratio(process.get("cpu_percent")), label)
        add("gpu_info_process_gpu_ratio", ratio(process.get("gpu_percent")), label)
        if process.get("memory_source") == "NVML" and _value(process.get("local_memory")) is None:
            add("gpu_info_process_nvml_memory_bytes", process.get("used_memory"), label)

    fresh_utilization = (frame.get("process_utilization") or []) if (
        include_processes and health["snapshot_fresh"]
    ) else []
    for process in fresh_utilization:
        label = {"gpu": process.get("gpu_id"), "pid": process.get("pid"),
                 "name": process.get("name")}
        add("gpu_info_process_sm_utilization_ratio", ratio(process.get("sm_util")), label)
        add("gpu_info_process_memory_utilization_ratio", ratio(process.get("mem_util")), label)

    lines: list[str] = []
    for name, help_text in _METRICS.items():
        lines.extend((f"# HELP {name} {help_text}", f"# TYPE {name} gauge"))
        lines.extend(samples[name])
    return "\n".join(lines) + "\n"
