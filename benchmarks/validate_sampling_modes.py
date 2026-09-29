"""Bounded live sampler validation. Uses the shared API, never attaches to training."""
import json
import time
import urllib.request
from pathlib import Path
import psutil

BASE = "http://127.0.0.1:8001"


def request(path, method="GET"):
    with urllib.request.urlopen(urllib.request.Request(BASE + path, method=method), timeout=10) as response:
        return json.load(response)


def validate(mode, seconds=20):
    request("/api/sampling-control?action=stop&target=hardware", "POST")
    control = request(f"/api/sampling-control?action=start&target=all&mode={mode}&replace=true", "POST")
    pid = control["hardware"]["pid"]
    process = psutil.Process(pid)
    process.cpu_percent()
    result = {"mode": mode, "pid": pid, "seconds": seconds, "states": [], "max_local_bytes": 0,
              "max_shared_bytes": 0, "max_rss_bytes": 0}
    deadline = time.monotonic() + seconds
    packets = {}
    while time.monotonic() < deadline:
        state = request("/api/hardware-sampling")
        result["states"].append({key: state.get(key) for key in (
            "state", "ready", "max_age_seconds", "sequence", "error", "current_cohort", "checked_at")})
        if state["state"] == "error":
            break
        raw = request("/api/hardware-sampling/samples?limit=240")
        packets.update({packet["sequence"]: packet for packet in raw["packets"]})
        result["max_rss_bytes"] = max(result["max_rss_bytes"], process.memory_info().rss)
        snapshot = request("/status.json")
        for row in snapshot.get("processes", []):
            if row["pid"] == pid:
                result["max_local_bytes"] = max(result["max_local_bytes"], row.get("local_memory") or 0)
                result["max_shared_bytes"] = max(result["max_shared_bytes"], row.get("shared_memory") or 0)
        time.sleep(.15)
    result["cpu_percent_one_core"] = process.cpu_percent() if process.is_running() else None
    result["final_state"] = request("/api/hardware-sampling")
    result["packets"] = list(packets.values())
    request("/api/sampling-control?action=stop&target=hardware", "POST")
    return result


if __name__ == "__main__":
    results = []
    try:
        for mode in ("compute", "diagnostic"):
            result = validate(mode)
            results.append(result)
            print(json.dumps({key: value for key, value in result.items() if key not in (
                "states", "packets", "final_state")}, ensure_ascii=False), flush=True)
            print("final", result["final_state"]["state"], result["final_state"].get("error"), flush=True)
    finally:
        request("/api/sampling-control?action=stop&target=hardware", "POST")
        Path("benchmarks/results/sampling_modes_validation.json").write_text(
            json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
