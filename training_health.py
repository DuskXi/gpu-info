"""Read-only NVML training diagnostics. Unknown/unsupported is never zero."""
import math


FIELDS = {
    "instant_mw": "POWER_INSTANT", "energy_mj": "ENERGY",
    "power_limit_us": "CLOCKS_EVENT_REASON_SW_POWER_CAP",
    "software_thermal_us": "CLOCKS_EVENT_REASON_SW_THERM_SLOWDOWN",
    "hardware_thermal_us": "CLOCKS_EVENT_REASON_HW_THERM_SLOWDOWN",
}
REASONS = {1: "GPU 空闲", 2: "应用时钟设置", 4: "软件功率限制", 8: "硬件降速",
           16: "同步 Boost", 32: "软件温度限制", 64: "硬件温度限制",
           128: "外部供电制动", 256: "显示时钟设置"}


def read(fn):
    try:
        return fn()
    except Exception:
        return None


class TrainingHealth:
    def __init__(self):
        self.previous = {}

    def capture(self, nv, handle, identity):
        fields = {}
        supported = [(key, getattr(nv, "NVML_FI_DEV_" + name, None)) for key, name in FIELDS.items()]
        supported = [(key, identifier) for key, identifier in supported if identifier is not None]
        values = read(lambda: nv.nvmlDeviceGetFieldValues(handle, [identifier for _, identifier in supported]))
        if values:
            for (key, _), field in zip(supported, values):
                member = {0: "dVal", 1: "uiVal", 2: "ulVal", 3: "ullVal", 4: "sllVal"}.get(field.valueType)
                if field.nvmlReturn == 0 and member:
                    value = getattr(field.value, member)
                    if math.isfinite(value) and 0 <= value < 2**63:
                        fields[key] = (value, field.timestamp)
        before = self.previous.get(identity, {})
        self.previous[identity] = fields

        def delta_rate(key, factor):
            if key not in before or key not in fields:
                return None
            value, stamp = fields[key]
            old, old_stamp = before[key]
            if stamp <= old_stamp or value < old:
                return None
            return (value - old) / (stamp - old_stamp) * factor

        memory = read(lambda: nv.nvmlDeviceGetMemoryInfo(handle, version=nv.nvmlMemory_v2))
        mask = read(lambda: nv.nvmlDeviceGetCurrentClocksEventReasons(handle))
        return {
            "instant_power_w": fields["instant_mw"][0] / 1000 if "instant_mw" in fields else None,
            "energy_total_j": fields["energy_mj"][0] / 1000 if "energy_mj" in fields else None,
            "energy_window_w": delta_rate("energy_mj", 1000),
            "power_limit_time_percent": delta_rate("power_limit_us", 100),
            "software_thermal_time_percent": delta_rate("software_thermal_us", 100),
            "hardware_thermal_time_percent": delta_rate("hardware_thermal_us", 100),
            "temperature_margin_c": read(lambda: nv.nvmlDeviceGetMarginTemperature(handle)),
            "driver_reserved_bytes": getattr(memory, "reserved", None),
            "clock_reasons_raw": mask,
            "clock_reasons": [name for bit, name in REASONS.items() if mask & bit] if mask is not None else [],
            "unknown_clock_reasons": mask & ~sum(REASONS) if mask is not None else None,
            "pcie_generation": read(lambda: nv.nvmlDeviceGetCurrPcieLinkGeneration(handle)),
            "pcie_width": read(lambda: nv.nvmlDeviceGetCurrPcieLinkWidth(handle)),
            "pcie_replay_count": read(lambda: nv.nvmlDeviceGetPcieReplayCounter(handle)),
        }
