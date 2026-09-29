"""Read-only NVML bandwidth samples; independent of CUPTI counter scheduling."""
import copy
import threading
import time
import uuid
from collections import deque

from activity_probe import ProbeError
from training_health import read


def link_capacity(generation, width):
    # Per-direction line rate after encoding, before PCIe packet overhead.
    rates = {1: 2.5e9 * 8/10, 2: 5e9 * 8/10, 3: 8e9 * 128/130,
             4: 16e9 * 128/130, 5: 32e9 * 128/130}
    return rates[generation] * width / 8 if generation in rates and width and width > 0 else None


def memory_reference(nv, handle):
    clock = read(lambda: nv.nvmlDeviceGetClockInfo(handle, nv.NVML_CLOCK_MEM))
    width = read(lambda: nv.nvmlDeviceGetMemoryBusWidth(handle))
    return {"sampled_at": time.time(), "clock_mhz": clock, "bus_width_bits": width,
            "estimated_peak_bytes_per_second": clock * 1e6 * 2 * width / 8 if clock and width else None,
            "basis": "NVML memory clock MHz × 1e6 × 2 × bus width bits / 8; theoretical DDR estimate"}


def capture_bandwidth(nv, handle):
    started = time.time()
    generation = read(lambda: nv.nvmlDeviceGetCurrPcieLinkGeneration(handle))
    width = read(lambda: nv.nvmlDeviceGetCurrPcieLinkWidth(handle))
    capacity = link_capacity(generation, width)
    result = {"sampled_at": started, "source": "NVML", "scope": "device",
              "pcie_generation": generation, "pcie_width": width,
              "pcie_capacity_bytes_per_second": capacity, "driver_window_seconds": .020,
              "pcie_percent_basis": "current negotiated link, per direction, encoding removed, packet overhead not removed",
              "memory_reference": memory_reference(nv, handle)}
    for direction in ("tx", "rx"):
        kb = read(lambda: nv.nvmlDeviceGetPcieThroughput(handle, getattr(nv, f"NVML_PCIE_UTIL_{direction.upper()}_BYTES")))
        rate = kb * 1024 if kb is not None else None
        result[f"pcie_{direction}_kib_per_second"] = kb
        result[f"pcie_{direction}_bytes_per_second"] = rate
        result[f"pcie_{direction}_percent"] = rate / capacity * 100 if rate is not None and capacity else None
    result["query_duration_seconds"] = time.time() - started
    return result


class BandwidthSampling:
    def __init__(self, nv):
        self.nv = nv
        self._control = threading.RLock()
        self._data = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._state = {"enabled": False, "state": "off", "latest": None, "error": None}
        self._samples = deque(maxlen=600)

    def snapshot(self):
        with self._data:
            state = copy.deepcopy(self._state)
        state["checked_at"] = time.time()
        latest = state.get("latest")
        state["age_seconds"] = max(0, time.time() - latest["sampled_at"]) if latest else None
        state["fresh"] = bool(state["enabled"] and latest and state["age_seconds"] < 1)
        return state

    def samples(self, after=0, limit=100, session_id=None):
        with self._data:
            current = self._state.get("session_id")
            reset = session_id is not None and session_id != current
            cursor = 0 if reset else after
            oldest = self._samples[0]["sequence"] if self._samples else None
            rows = [r for r in self._samples if r["sequence"] > cursor][:limit]
            return copy.deepcopy({"session_id": current, "reset": reset,
                "truncated": oldest is not None and cursor < oldest - 1,
                "oldest_sequence": oldest, "next_sequence": rows[-1]["sequence"] if rows else cursor,
                "samples": rows, "state": self._state["state"], "error": self._state.get("error")})

    def start(self, gpu_id=0):
        with self._control:
            if self._thread and self._thread.is_alive():
                if self.snapshot().get("gpu_id") == gpu_id:
                    return self.snapshot()
                self.stop()
                if self._thread.is_alive():
                    raise ProbeError("旧带宽采样器尚未停止")
            if self.nv is None:
                raise ProbeError("NVML 不可用")
            handle = self.nv.nvmlDeviceGetHandleByIndex(gpu_id)
            self._stop.clear()
            with self._data:
                self._samples.clear()
                self._state = {"enabled": True, "state": "starting", "gpu_id": gpu_id,
                    "session_id": uuid.uuid4().hex, "frequency_hz": 10, "latest": None, "error": None}
            self._thread = threading.Thread(target=self._run, args=(handle,), daemon=True, name="nvml-bandwidth")
            self._thread.start()
            return self.snapshot()

    def _run(self, handle):
        sequence = 0
        try:
            while not self._stop.is_set():
                started = time.monotonic()
                sample = capture_bandwidth(self.nv, handle)
                sequence += 1
                sample["sequence"] = sequence
                with self._data:
                    self._state.update(state="running", latest=sample)
                    self._samples.append(sample)
                self._stop.wait(max(0, .1 - (time.monotonic() - started)))
        except Exception as exc:
            with self._data:
                self._state.update(state="error", error=str(exc))
        finally:
            with self._data:
                self._state["enabled"] = False
                if self._state["state"] != "error":
                    self._state["state"] = "off"

    def stop(self):
        with self._control:
            self._stop.set()
            if self._thread:
                self._thread.join(timeout=3)
            return self.snapshot()
