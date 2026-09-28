"""One opt-in, bounded-memory CUPTI helper shared by all web clients."""
import copy
import json
import math
import os
import subprocess
import threading
import time
import uuid
from collections import deque
from pathlib import Path

from activity_probe import ProbeError


def metric(key, name, unit="percent"):
    return {"key": key, "name": name, "unit": unit}


PCT = ".avg.pct_of_peak_sustained_elapsed"
RATE = ".sum.per_second"
GROUPS = {
    "activity": [metric("sm_active", "sm__cycles_active" + PCT),
                 metric("tensor_active", "sm__pipe_tensor_cycles_active_realtime" + PCT)],
    "cache": [metric("l1_hit", "l1tex__t_sector_hit_rate.pct"),
              metric("l2_hit", "lts__average_t_sector_hit_rate_realtime.pct"),
              metric("warps_active", "sm__warps_active_realtime" + PCT)],
    "pipelines": [
        *[metric(key, f"sm__inst_executed_pipe_{pipe}_realtime" + suffix, unit)
          for key, pipe, suffix, unit in (
              ("fma_heavy", "fmaheavy", PCT, "percent"),
              ("fma_light", "fmalite", PCT, "percent"),
              ("alu", "alu", PCT, "percent"),
              ("sfu", "xu", PCT, "percent"),
              ("fma_heavy_ips", "fmaheavy", RATE, "warp_instructions_per_second"),
              ("fma_light_ips", "fmalite", RATE, "warp_instructions_per_second"),
              ("alu_ips", "alu", RATE, "warp_instructions_per_second"),
              ("sfu_ips", "xu", RATE, "warp_instructions_per_second"),
          )],
    ],
    "tensor": [metric("tensor_ips", "sm__inst_executed_pipe_tensor" + RATE,
                      "warp_instructions_per_second")],
    "scalar": [metric("fma_light_thread_ips", "sm__thread_inst_executed_pipe_fmalite_pred_on" + RATE,
                      "thread_instructions_per_second"),
               metric("fma_light_warp_ips", "sm__inst_executed_pipe_fmalite_realtime" + RATE,
                      "warp_instructions_per_second")],
}


class RollingSamples:
    """A time-weighted 3 s window of observed intervals, never filled across gaps."""
    def __init__(self):
        self.rows = deque(maxlen=128)
        self.last_end = 0
        self.epoch = None

    def add(self, packet, definitions):
        if packet.get("overflow"):
            raise ValueError("CUPTI 缓冲溢出")
        if packet.get("epoch") != self.epoch:
            self.rows.clear()
            self.last_end = 0
            self.epoch = packet.get("epoch")
        accepted = 0
        for row in packet["samples"]:
            start, end, values = row["start_ns"], row["end_ns"], row["values"]
            if (not isinstance(start, int) or not isinstance(end, int) or end <= start
                    or len(values) != len(definitions)
                    or any(isinstance(v, bool) or not isinstance(v, (int, float))
                           or not math.isfinite(v) or v < 0 for v in values)):
                raise ValueError("无效的 CUPTI 样本")
            if end <= self.last_end:
                continue
            trimmed_ns = 0
            if start < self.last_end:
                # CUPTI's cross-batch GPU clock conversion has sub-microsecond jitter
                # on this driver. Clip only <=10 us; never hide a substantial overlap.
                trimmed_ns = self.last_end - start
                if trimmed_ns > 10_000:
                    raise ValueError(f"CUPTI 采样区间重叠 {trimmed_ns} ns")
                start = self.last_end
            row = {**row, "start_ns": start, "timestamp_trimmed_ns": trimmed_ns}
            self.rows.append(row)
            self.last_end = end
            accepted += 1
        if not accepted:
            return None
        cutoff = self.last_end - 3_000_000_000
        while self.rows and self.rows[0]["end_ns"] <= cutoff:
            self.rows.popleft()
        weights = [r["end_ns"] - max(r["start_ns"], cutoff) for r in self.rows]
        duration = sum(weights)
        result = {
            "sampled_at": packet["received_at"], "samples": len(self.rows),
            "observed_seconds": duration / 1e9,
            "span_seconds": (self.last_end - max(self.rows[0]["start_ns"], cutoff)) / 1e9,
            "gpu_start_ns": max(self.rows[0]["start_ns"], cutoff), "gpu_end_ns": self.last_end,
            "incomplete_samples": packet.get("incomplete_samples", 0),
            "timestamp_trimmed_ns": sum(r["timestamp_trimmed_ns"] for r in self.rows),
            "metrics": {
                item["key"]: {
                    "value": sum(r["values"][i] * w for r, w in zip(self.rows, weights)) / duration,
                    "peak": max(r["values"][i] for r in self.rows),
                    "unit": item["unit"], "counter": item["name"],
                } for i, item in enumerate(definitions)
            },
        }
        metrics = result["metrics"]
        if "fma_light_thread_ips" in metrics:
            threads = metrics["fma_light_thread_ips"]["value"]
            warps = metrics["fma_light_warp_ips"]["value"]
            lanes = threads / warps if warps > 0 else None
            metrics["fma_light_active_lanes"] = {
                "value": lanes if lanes is not None and 0 <= lanes <= 32 else None,
                "unit": "threads_per_warp_instruction",
                "counter": "FMA Lite predicated-on thread instructions / warp instructions",
            }
        return result


class PMSampling:
    def __init__(self, profiler_lock):
        self._profiler_lock = profiler_lock
        self._control = threading.Lock()
        self._data = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._process = None
        self._state = {"state": "off", "enabled": False, "groups": {}, "error": None}

    def snapshot(self):
        with self._data:
            state = copy.deepcopy(self._state)
        now = time.time()
        for group in state["groups"].values():
            group["age_seconds"] = max(0, now - group["sampled_at"])
            group["fresh"] = state["enabled"] and group["age_seconds"] <= 3
        return state

    def start(self, gpu_id, pci_bus_id, mode="all"):
        if mode != "all" and mode not in GROUPS:
            raise ProbeError("未知硬件指标组")
        if not pci_bus_id:
            raise ProbeError("缺少 PCI 设备标识，无法安全对应 CUDA 与 NVML 设备")
        with self._control:
            if self._thread and self._thread.is_alive():
                current = self.snapshot()
                if current["gpu_id"] == gpu_id and current["mode"] == mode:
                    return current
                raise ProbeError("请先停止当前连续采样")
            executable = Path(os.getenv("GPU_INFO_PM_HELPER", Path(__file__).parent / "native/bin/cupti_stream.exe"))
            cupti = Path(os.getenv("GPU_INFO_CUPTI_PATH", r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.6\extras\CUPTI\lib64"))
            if not executable.is_file():
                raise ProbeError("缺少 CUPTI helper；请运行 native/build.ps1")
            if not self._profiler_lock.acquire(blocking=False):
                raise ProbeError("另一硬件探针正在采样，请稍后重试")
            groups = list(GROUPS) if mode == "all" else [mode]
            command = [str(executable), str(pci_bus_id), *[
                name + "=" + ",".join(item["name"] for item in GROUPS[name]) for name in groups
            ]]
            env = {**os.environ, "PATH": str(cupti) + os.pathsep + os.environ.get("PATH", "")}
            try:
                process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                           stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                                           env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except Exception as exc:
                self._profiler_lock.release()
                raise ProbeError(f"CUPTI 启动失败：{exc}") from exc
            self._process = process
            self._stop.clear()
            self._last_packet_at = time.monotonic()
            with self._data:
                self._state = {"state": "starting", "enabled": True, "gpu_id": gpu_id,
                               "mode": mode, "session_id": uuid.uuid4().hex, "groups": {}, "error": None,
                               "source": "CUPTI PM Sampling", "hardware_buffer_bytes": 8 * 1024**2,
                               "frequency_hz": 10, "window_seconds": 3, "pid": process.pid}
            self._thread = threading.Thread(target=self._read, args=(process, groups), daemon=True,
                                            name="cupti-stream")
            self._thread.start()
            threading.Thread(target=self._watchdog, args=(process,), daemon=True,
                             name="cupti-watchdog").start()
            return self.snapshot()

    def _watchdog(self, process):
        while process.poll() is None:
            if time.monotonic() - self._last_packet_at > 10:
                process.terminate()
                return
            time.sleep(1)

    def _read(self, process, groups):
        windows = {name: RollingSamples() for name in groups}
        failure = None
        last_sequence = 0
        try:
            for line in process.stdout:
                packet = json.loads(line)
                group = packet["group"]
                if group not in windows or packet["sequence"] <= last_sequence:
                    raise ValueError("CUPTI 数据序号或指标组无效")
                last_sequence = packet["sequence"]
                result = windows[group].add(packet, GROUPS[group])
                if result:
                    self._last_packet_at = time.monotonic()
                    with self._data:
                        self._state["groups"][group] = result
                        self._state.update(state="running", current_group=group)
            process.wait(timeout=3)
            if process.returncode != 0 and not self._stop.is_set():
                failure = process.stderr.read()[-1600:] or f"helper exit {process.returncode}"
        except Exception as exc:
            failure = str(exc)
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=3)
        finally:
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream:
                    stream.close()
            with self._data:
                self._state.update(enabled=False, state="error" if failure else "off", error=failure)
            self._profiler_lock.release()

    def stop(self):
        with self._control:
            self._stop.set()
            process = self._process
            if process and process.poll() is None:
                try:
                    process.stdin.write("stop\n")
                    process.stdin.flush()
                except (OSError, ValueError):
                    pass
                self._thread.join(timeout=3)
                if self._thread.is_alive():
                    process.terminate()
                    self._thread.join(timeout=3)
            return self.snapshot()
