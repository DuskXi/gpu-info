"""Read-only capability probe; no CUDA context, clock changes or power changes."""
import json
import time

import pynvml as nv


FIELDS = (
    "POWER_AVERAGE", "POWER_INSTANT", "ENERGY", "PCIE_COUNT_TX_BYTES",
    "PCIE_COUNT_RX_BYTES", "CLOCKS_EVENT_REASON_SW_POWER_CAP",
    "CLOCKS_EVENT_REASON_SW_THERM_SLOWDOWN", "CLOCKS_EVENT_REASON_HW_THERM_SLOWDOWN",
)
UNION_FIELDS = {0: "dVal", 1: "uiVal", 2: "ulVal", 3: "ullVal", 4: "sllVal"}


def optional(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except nv.NVMLError as error:
        return {"error": str(error)}


def main():
    nv.nvmlInit()
    try:
        handle = nv.nvmlDeviceGetHandleByIndex(0)
        gpm = optional(nv.nvmlGpmQueryDeviceSupport, handle)
        memory = optional(nv.nvmlDeviceGetMemoryInfo, handle, version=nv.nvmlMemory_v2)
        result = {
            "gpu": nv.nvmlDeviceGetName(handle),
            "driver": nv.nvmlSystemGetDriverVersion(),
            "gpm_supported": gpm if isinstance(gpm, dict) else bool(gpm.isSupportedDevice),
            "memory_v2": memory if isinstance(memory, dict) else {
                name: getattr(memory, name) for name in ("total", "reserved", "used", "free")
            },
            "pcie_gen": optional(nv.nvmlDeviceGetCurrPcieLinkGeneration, handle),
            "pcie_width": optional(nv.nvmlDeviceGetCurrPcieLinkWidth, handle),
            "pcie_replays": optional(nv.nvmlDeviceGetPcieReplayCounter, handle),
            "samples": [],
        }
        field_ids = [getattr(nv, "NVML_FI_DEV_" + name) for name in FIELDS]
        cpu_start = time.process_time()
        for index in range(5):
            sample = {"monotonic_s": time.monotonic(), "fields": {}}
            sample["clock_reasons_raw"] = optional(nv.nvmlDeviceGetCurrentClocksEventReasons, handle)
            sample["temperature_margin_c"] = optional(nv.nvmlDeviceGetMarginTemperature, handle)
            for name, field in zip(FIELDS, nv.nvmlDeviceGetFieldValues(handle, field_ids)):
                key = UNION_FIELDS.get(field.valueType)
                sample["fields"][name] = {
                    "status": field.nvmlReturn, "value_type": field.valueType,
                    "timestamp_us": field.timestamp,
                    "value": getattr(field.value, key) if field.nvmlReturn == 0 and key else None,
                }
            result["samples"].append(sample)
            if index != 4:
                time.sleep(1)
        result["query_loop_cpu_s"] = time.process_time() - cpu_start
        print(json.dumps(result, indent=2))
    finally:
        nv.nvmlShutdown()


if __name__ == "__main__":
    main()
