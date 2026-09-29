"""Validate process-local Activity buffer controls; never initialize CUDA or enable tracing."""
import ctypes
import json
import os
from pathlib import Path


ATTRIBUTES = {
    "buffer_bytes": (0, ctypes.c_size_t),
    "pool_limit_per_context": (2, ctypes.c_size_t),
    "preallocate_per_context": (6, ctypes.c_size_t),
    "host_pinned": (8, ctypes.c_uint8),
}


def main():
    directory = Path(os.getenv("GPU_INFO_CUPTI_PATH", r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.6\extras\CUPTI\lib64"))
    with os.add_dll_directory(str(directory)):
        library = ctypes.WinDLL(str(directory / "cupti64_2024.3.0.dll"))
        get = library.cuptiActivityGetAttribute
        get.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p]
        get.restype = ctypes.c_int
        set_value = library.cuptiActivitySetAttribute
        set_value.argtypes = get.argtypes
        set_value.restype = ctypes.c_int

        def read():
            result = {}
            for name, (identifier, value_type) in ATTRIBUTES.items():
                value = value_type()
                size = ctypes.c_size_t(ctypes.sizeof(value))
                status = get(identifier, ctypes.byref(size), ctypes.byref(value))
                result[name] = {"status": status, "value": value.value if status == 0 else None}
            return result

        result = {"cuda_initialized_by_probe": False, "activity_enabled": False,
                  "scope": "this probe process only", "defaults": read()}
        # Reduce preallocation before pool limit, so each intermediate configuration is valid.
        requests = {"preallocate_per_context": 1, "pool_limit_per_context": 4,
                    "buffer_bytes": 4 * 1024**2, "host_pinned": 1}
        result["set_status"] = {}
        for name, requested in requests.items():
            identifier, value_type = ATTRIBUTES[name]
            value = value_type(requested)
            size = ctypes.c_size_t(ctypes.sizeof(value))
            result["set_status"][name] = set_value(identifier, ctypes.byref(size), ctypes.byref(value))
        result["requested"] = requests
        result["readback"] = read()
        result["settings_accepted"] = all(
            result["set_status"][name] == 0 and result["readback"][name]["value"] == value
            for name, value in requests.items())
        result["actual_trace_allocation_measured"] = False
        print(json.dumps(result, indent=2))
        return 0 if result["settings_accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
