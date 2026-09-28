"""Short, opt-in Nsight Systems GPU hardware metric capture.

The normal monitor never loads a GPU performance profiler. This probe runs
Nsight Systems without API/WDDM/CPU tracing and reads only its SQLite export.
"""

import math
import os
import shutil
import sqlite3
import statistics
import subprocess
import tempfile
import threading
import time
from contextlib import closing
from pathlib import Path
from typing import Any


_METRICS = {
    "sm_active": "SMs Active [Throughput %]",
    "sm_issue": "SM Issue [Throughput %]",
    "graphics_vertex": "Vertex/Tess/Geometry Warps in Flight [Throughput %]",
    "graphics_pixel": "Pixel Warps in Flight [Throughput %]",
    "compute_warps": "Compute Warps in Flight [Throughput %]",
    "tensor_active": "Tensor Active [Throughput %]",
    "gr_active": "GR Active [Throughput %]",
    "compute_sync": "Sync Compute in Flight [Throughput %]",
    "compute_async": "Async Compute in Flight [Throughput %]",
    "unallocated_warps": "Unallocated Warps in Active SMs [Throughput %]",
    "dram_read": "DRAM Read Bandwidth [Throughput %]",
    "dram_write": "DRAM Write Bandwidth [Throughput %]",
}
_PIPELINE_METRICS = {
    "fma_light": "SM FMA Light Pipe Throughput [Throughput %]",
    "fma_heavy": "SM FMA Heavy Pipe Throughput [Throughput %]",
    "alu": "SM ALU Pipe Throughput [Throughput %]",
    "sfu": "SM SFU Pipe Throughput [Throughput %]",
    "tensor_active": "Tensor Active [Throughput %]",
}
_PIPELINE_CONFIG = Path(__file__).parent / "gpu_metrics" / "ad10x-pipes.config"
_GENERAL_FREQUENCY_HZ = 100
_PIPELINE_FREQUENCY_HZ = 10


class ProbeError(RuntimeError):
    pass


def find_nsys() -> Path | None:
    configured = os.getenv("GPU_INFO_NSYS_PATH")
    if configured:
        path = Path(configured)
        return path if path.is_file() else None
    in_path = shutil.which("nsys")
    if in_path:
        return Path(in_path)
    root = Path(os.getenv("ProgramFiles", r"C:\Program Files")) / "NVIDIA Corporation"
    candidates = list(root.glob("Nsight Systems */target-windows-x64/nsys.exe"))
    return sorted(candidates, reverse=True)[0] if candidates else None


def parse_metrics(database: Path, kind: str = "general") -> dict[str, dict[str, Any]]:
    """Match metric names from the report instead of assuming GPU-specific IDs."""
    try:
        with closing(sqlite3.connect(database)) as connection:
            rows = connection.execute(
                "SELECT i.metricName, m.value FROM GPU_METRICS AS m "
                "JOIN TARGET_INFO_GPU_METRICS AS i "
                "ON m.typeId = i.typeId AND m.metricId = i.metricId "
                "ORDER BY m.timestamp"
            ).fetchall()
    except sqlite3.Error as exc:
        raise ProbeError(f"Nsight 报告缺少 GPU 指标：{exc}") from exc
    names = _PIPELINE_METRICS if kind == "pipelines" else _METRICS
    readings: dict[str, list[float]] = {key: [] for key in names}
    by_name = {name: key for key, name in names.items()}
    for name, value in rows:
        key = by_name.get(name)
        if key is not None and isinstance(value, (int, float)):
            readings[key].append(float(value))
    return {
        key: {
            "mean_percent": round(statistics.fmean(values), 1),
            "p90_percent": round(sorted(values)[math.ceil(len(values) * 0.9) - 1], 1),
            "peak_percent": round(max(values), 1),
            "active_samples": sum(value > 0 for value in values),
            "samples": len(values),
        }
        for key, values in readings.items() if values
    }


def report_window(database: Path) -> tuple[float, float] | None:
    """Resolve Nsight's relative GPU timestamps to Unix time for correlation."""
    try:
        with closing(sqlite3.connect(database)) as connection:
            origin = connection.execute(
                "SELECT utcEpochNs FROM TARGET_INFO_SESSION_START_TIME LIMIT 1"
            ).fetchone()
            span = connection.execute(
                "SELECT MIN(timestamp), MAX(timestamp) FROM GPU_METRICS"
            ).fetchone()
    except sqlite3.Error:
        return None
    if not origin or not span or span[0] is None or span[1] is None:
        return None
    return ((origin[0] + span[0]) / 1e9, (origin[0] + span[1]) / 1e9)


def _run(command: list[str], timeout: int) -> None:
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, errors="replace",
            timeout=timeout, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProbeError(f"Nsight 采集失败：{exc}") from exc
    if result.returncode:
        detail = (result.stdout + "\n" + result.stderr).strip()[-1200:]
        raise ProbeError(f"Nsight 返回 {result.returncode}：{detail}")


class ActivityProbe:
    def __init__(self) -> None:
        self._lock = threading.Lock()

    def capture(self, gpu_id: int, kind: str = "general") -> dict[str, Any]:
        if kind not in ("general", "pipelines"):
            raise ProbeError("未知硬件采样类型")
        if not self._lock.acquire(blocking=False):
            raise ProbeError("硬件探针正在采样，请等待本次结果")
        try:
            nsys = find_nsys()
            if nsys is None:
                raise ProbeError("未找到 Nsight Systems CLI；可设置 GPU_INFO_NSYS_PATH")
            frequency_hz = (
                _PIPELINE_FREQUENCY_HZ if kind == "pipelines" else _GENERAL_FREQUENCY_HZ
            )
            with tempfile.TemporaryDirectory(prefix="gpu-info-probe-") as directory:
                base = Path(directory) / "capture"
                report = base.with_suffix(".nsys-rep")
                database = base.with_suffix(".sqlite")
                command = [
                    str(nsys), "profile", "--trace=none", "--sample=none",
                    "--cpuctxsw=none", f"--gpu-metrics-device={gpu_id}",
                    f"--gpu-metrics-frequency={frequency_hz}", "--duration=5",
                    f"--output={base}",
                ]
                if kind == "pipelines":
                    command.insert(-1, f"--gpu-metrics-set=file:{_PIPELINE_CONFIG}")
                _run(command, timeout=35)
                if not report.is_file():
                    raise ProbeError("Nsight 未生成 GPU 指标报告")
                _run([
                    str(nsys), "export", "--type=sqlite", f"--output={database}",
                    str(report),
                ], timeout=20)
                metrics = parse_metrics(database, kind)
                window = report_window(database)
            if not metrics:
                raise ProbeError("本次报告没有可用的 GPU 硬件指标；管线采样仅支持 AD10x 设备")
            return {
                "gpu_id": gpu_id, "kind": kind, "captured_at": time.time(),
                "duration_seconds": 5, "frequency_hz": frequency_hz,
                "scope": "device", "source": "Nsight Systems GPU Metrics",
                "metrics": metrics,
                "window_started_at": window[0] if window else None,
                "window_ended_at": window[1] if window else None,
            }
        finally:
            self._lock.release()
