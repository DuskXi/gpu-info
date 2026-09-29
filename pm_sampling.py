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
    "memory": [metric("dram_read", "dram__read_throughput" + PCT),
               metric("dram_write", "dram__write_throughput" + PCT)],
}

MODES = tuple(GROUPS) + ("all", "diagnostic", "compute")


def sampling_plan(mode):
    """Cohorts are physically sampled together; UI groups are only projections."""
    if mode not in MODES:
        raise ProbeError("未知硬件指标组")
    if mode in ("diagnostic", "compute"):
        cohorts = {"execution": GROUPS["pipelines"] + GROUPS["tensor"] + GROUPS["memory"]}
        views = {"pipelines": "execution", "tensor": "execution", "memory": "execution"}
        if mode == "diagnostic":
            cohorts = {"activity": GROUPS["activity"], "cache": GROUPS["cache"],
                       **cohorts, "scalar": GROUPS["scalar"]}
            views = {"activity": "activity", "cache": "cache", **views, "scalar": "scalar"}
        interval_ms, publish_ms, batches = 50, 250, 2
    else:
        cohorts = dict(GROUPS) if mode == "all" else {mode: GROUPS[mode]}
        views = {key: key for key in cohorts}
        interval_ms, publish_ms, batches = 100, 1000, 3
    return {"cohorts": cohorts, "views": views, "interval_ms": interval_ms,
            "publish_ms": publish_ms, "batches_per_group": batches,
            "simultaneous_all_metrics": len(cohorts) == 1}


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
                # Cross-batch clock conversion can overlap by ~10 us on this driver.
                # Bound correction to 0.1% of an interval and at most 100 us.
                # Large overlaps still indicate invalid data rather than clock jitter.
                trimmed_ns = self.last_end - start
                tolerance_ns = min(100_000, (end - start) // 1000)
                if trimmed_ns > tolerance_ns:
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
            "sequence": packet.get("sequence"), "epoch": packet.get("epoch"),
            "sampled_at": packet["received_at"], "samples": len(self.rows),
            "observed_seconds": duration / 1e9,
            "span_seconds": (self.last_end - max(self.rows[0]["start_ns"], cutoff)) / 1e9,
            "gpu_start_ns": max(self.rows[0]["start_ns"], cutoff), "gpu_end_ns": self.last_end,
            "incomplete_samples": packet.get("incomplete_samples", 0),
            "timestamp_trimmed_ns": sum(r["timestamp_trimmed_ns"] for r in self.rows),
            "coverage_ratio": duration / (self.last_end - max(self.rows[0]["start_ns"], cutoff)),
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
    def __init__(self, profiler_lock, memory_reference=None):
        self._profiler_lock = profiler_lock
        self._memory_reference = memory_reference
        self._control = threading.RLock()
        self._data = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._process = None
        self._state = {"state": "off", "enabled": False, "groups": {}, "error": None}
        self._packets = deque(maxlen=240)

    def samples(self, after=0, limit=100, session_id=None):
        with self._data:
            current = self._state.get("session_id")
            reset = session_id is not None and session_id != current
            cursor = 0 if reset else after
            oldest = self._packets[0]["sequence"] if self._packets else None
            selected = [p for p in self._packets if p["sequence"] > cursor][:limit]
            return copy.deepcopy({"session_id": current, "reset": reset,
                "truncated": oldest is not None and cursor < oldest - 1,
                "oldest_sequence": oldest,
                "next_sequence": selected[-1]["sequence"] if selected else cursor,
                "packets": selected, "cohorts": self._state.get("cohorts", {}),
                "scope": "device", "state": self._state["state"], "error": self._state.get("error")})

    def snapshot(self):
        with self._data:
            state = copy.deepcopy(self._state)
        now = time.time()
        expected = set(state.get("view_cohorts", {}))
        missing = sorted(expected - state["groups"].keys())
        ages = []
        for group in state["groups"].values():
            group["age_seconds"] = max(0, now - group["sampled_at"])
            ages.append(group["age_seconds"])
            group["fresh"] = state["enabled"] and group["age_seconds"] <= state.get("freshness_budget_seconds", 3)
            group["sampling_active"] = state["enabled"] and group.get("cohort") == state.get("current_cohort")
        state["checked_at"] = now
        state["max_age_seconds"] = max(ages) if ages else None
        state["missing_groups"] = missing
        state["ready"] = bool(state["enabled"] and state["state"] == "running" and expected
                              and not missing and all(g["fresh"] for g in state["groups"].values()))
        return state

    def start(self, gpu_id, pci_bus_id, mode="all", replace=False):
        plan = sampling_plan(mode)
        if not pci_bus_id:
            raise ProbeError("缺少 PCI 设备标识，无法安全对应 CUDA 与 NVML 设备")
        with self._control:
            if self._thread and self._thread.is_alive():
                current = self.snapshot()
                if current["gpu_id"] == gpu_id and current["mode"] == mode:
                    return current
                if not replace:
                    raise ProbeError("请先停止当前连续采样，或指定 replace=true")
                self.stop()
                if self._thread.is_alive():
                    raise ProbeError("旧采样器尚未停止")
            executable = Path(os.getenv("GPU_INFO_PM_HELPER", Path(__file__).parent / "native/bin/cupti_stream.exe"))
            cupti = Path(os.getenv("GPU_INFO_CUPTI_PATH", r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.6\extras\CUPTI\lib64"))
            if not executable.is_file():
                raise ProbeError("缺少 CUPTI helper；请运行 native/build.ps1")
            if not self._profiler_lock.acquire(blocking=False):
                raise ProbeError("另一硬件探针正在采样，请稍后重试")
            command = [str(executable), str(pci_bus_id), *[
                name + "=" + ",".join(item["name"] for item in definitions)
                for name, definitions in plan["cohorts"].items()
            ]]
            env = {**os.environ, "PATH": str(cupti) + os.pathsep + os.environ.get("PATH", ""),
                   "GPU_INFO_PM_INTERVAL_MS": str(plan["interval_ms"]),
                   "GPU_INFO_PM_PUBLISH_MS": str(plan["publish_ms"]),
                   "GPU_INFO_PM_BATCHES_PER_GROUP": str(plan["batches_per_group"])}
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
                self._packets.clear()
                self._state = {"state": "starting", "enabled": True, "gpu_id": gpu_id,
                               "mode": mode, "session_id": uuid.uuid4().hex, "groups": {}, "error": None,
                               "source": "CUPTI PM Sampling", "hardware_buffer_bytes": 8 * 1024**2,
                               "frequency_hz": 1000 / plan["interval_ms"], "window_seconds": 3,
                               "publish_interval_seconds": plan["publish_ms"] / 1000,
                               "freshness_budget_seconds": 1 if mode == "compute" else 3,
                               "cohort_dwell_seconds": plan["publish_ms"] * plan["batches_per_group"] / 1000,
                               "simultaneous_all_metrics": plan["simultaneous_all_metrics"],
                               "cohorts": plan["cohorts"], "view_cohorts": plan["views"],
                               "scope": "device", "pid": process.pid}
            self._thread = threading.Thread(target=self._read, args=(process, plan), daemon=True,
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

    def _read(self, process, plan):
        windows = {name: RollingSamples() for name in plan["cohorts"]}
        failure = None
        last_sequence = 0
        try:
            for line in process.stdout:
                packet = json.loads(line)
                group = packet["group"]
                if group not in windows or packet["sequence"] <= last_sequence:
                    raise ValueError("CUPTI 数据序号或指标组无效")
                last_sequence = packet["sequence"]
                result = windows[group].add(packet, plan["cohorts"][group])
                if result and "dram_read" in result["metrics"] and self._memory_reference:
                    reference = self._memory_reference(self._state["gpu_id"])
                    result["memory_reference"] = reference
                    packet["memory_reference"] = reference
                    peak = reference.get("estimated_peak_bytes_per_second")
                    for direction in ("read", "write"):
                        result["metrics"][f"dram_{direction}_estimated_bytes_per_second"] = {
                            "value": result["metrics"][f"dram_{direction}"]["value"] / 100 * peak if peak else None,
                            "unit": "estimated_bytes_per_second", "estimated": True,
                            "counter": "CUPTI throughput percent × NVML theoretical DDR bandwidth; not a byte counter"}
                with self._data:
                    self._packets.append({**packet, "cohort": group})
                if result:
                    self._last_packet_at = time.monotonic()
                    with self._data:
                        result["cohort"] = group
                        result["window_id"] = f"{self._state['session_id']}:{packet['epoch']}:{packet['sequence']}"
                        for view, cohort in plan["views"].items():
                            if cohort != group:
                                continue
                            keys = {item["key"] for item in GROUPS[view]}
                            if view == "scalar":
                                keys.add("fma_light_active_lanes")
                            if view == "memory":
                                keys.update({"dram_read_estimated_bytes_per_second", "dram_write_estimated_bytes_per_second"})
                            self._state["groups"][view] = {**result, "metrics": {
                                key: value for key, value in result["metrics"].items() if key in keys}}
                        self._state.update(state="running", current_group=group,
                                           current_cohort=group, sequence=packet["sequence"])
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
