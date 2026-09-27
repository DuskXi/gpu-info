"""Read active Windows display paths without creating a graphics context."""

import ctypes
import os
from typing import Any


class _Luid(ctypes.Structure):
    _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_int32)]


class _Rational(ctypes.Structure):
    _fields_ = [("numerator", ctypes.c_uint32), ("denominator", ctypes.c_uint32)]


class _Region(ctypes.Structure):
    _fields_ = [("width", ctypes.c_uint32), ("height", ctypes.c_uint32)]


class _Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_int32), ("y", ctypes.c_int32)]


class _SourceMode(ctypes.Structure):
    _fields_ = [
        ("width", ctypes.c_uint32), ("height", ctypes.c_uint32),
        ("pixel_format", ctypes.c_uint32), ("position", _Point),
    ]


class _VideoSignal(ctypes.Structure):
    _fields_ = [
        ("pixel_rate", ctypes.c_uint64), ("horizontal", _Rational),
        ("vertical", _Rational), ("active", _Region), ("total", _Region),
        ("standard", ctypes.c_uint32), ("scanline", ctypes.c_uint32),
    ]


class _ModeUnion(ctypes.Union):
    _fields_ = [("source", _SourceMode), ("target", _VideoSignal),
                ("raw", ctypes.c_ubyte * 48)]


class _ModeInfo(ctypes.Structure):
    _fields_ = [
        ("kind", ctypes.c_uint32), ("id", ctypes.c_uint32),
        ("adapter", _Luid), ("mode", _ModeUnion),
    ]


class _SourceInfo(ctypes.Structure):
    _fields_ = [
        ("adapter", _Luid), ("id", ctypes.c_uint32),
        ("mode_index", ctypes.c_uint32), ("flags", ctypes.c_uint32),
    ]


class _TargetInfo(ctypes.Structure):
    _fields_ = [
        ("adapter", _Luid), ("id", ctypes.c_uint32),
        ("mode_index", ctypes.c_uint32), ("technology", ctypes.c_uint32),
        ("rotation", ctypes.c_uint32), ("scaling", ctypes.c_uint32),
        ("refresh", _Rational), ("scanline", ctypes.c_uint32),
        ("available", ctypes.c_int32), ("flags", ctypes.c_uint32),
    ]


class _PathInfo(ctypes.Structure):
    _fields_ = [("source", _SourceInfo), ("target", _TargetInfo),
                ("flags", ctypes.c_uint32)]


class _DeviceHeader(ctypes.Structure):
    _fields_ = [
        ("kind", ctypes.c_uint32), ("size", ctypes.c_uint32),
        ("adapter", _Luid), ("id", ctypes.c_uint32),
    ]


class _ColorInfo(ctypes.Structure):
    _fields_ = [
        ("header", _DeviceHeader), ("flags", ctypes.c_uint32),
        ("encoding", ctypes.c_uint32), ("bits_per_channel", ctypes.c_uint32),
        ("active_mode", ctypes.c_uint32),
    ]


class _TargetName(ctypes.Structure):
    _fields_ = [
        ("header", _DeviceHeader), ("flags", ctypes.c_uint32),
        ("technology", ctypes.c_uint32), ("manufacturer", ctypes.c_uint16),
        ("product", ctypes.c_uint16), ("connector", ctypes.c_uint32),
        ("friendly", ctypes.c_wchar * 64), ("path", ctypes.c_wchar * 128),
    ]


def _luid(value: _Luid) -> str:
    return f"0X{value.high & 0xffffffff:08X}_0X{value.low:08X}"


def _rate(value: _Rational) -> float | None:
    if not value.denominator:
        return None
    return round(value.numerator / value.denominator, 3)


def read_display_state() -> dict[str, Any] | None:
    """Return active desktop paths; None means this platform has no CCD API."""
    if os.name != "nt":
        return None
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    get_sizes = user32.GetDisplayConfigBufferSizes
    get_sizes.argtypes = [ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32),
                          ctypes.POINTER(ctypes.c_uint32)]
    query = user32.QueryDisplayConfig
    query.argtypes = [ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32),
                      ctypes.POINTER(_PathInfo), ctypes.POINTER(ctypes.c_uint32),
                      ctypes.POINTER(_ModeInfo), ctypes.c_void_p]
    device_info = user32.DisplayConfigGetDeviceInfo
    device_info.argtypes = [ctypes.POINTER(_DeviceHeader)]

    # Windows may change the topology between the two calls. Retry the entire read.
    for _ in range(3):
        path_count, mode_count = ctypes.c_uint32(), ctypes.c_uint32()
        error = get_sizes(2, ctypes.byref(path_count), ctypes.byref(mode_count))
        if error:
            raise OSError(error, "GetDisplayConfigBufferSizes failed")
        paths = (_PathInfo * max(1, path_count.value))()
        modes = (_ModeInfo * max(1, mode_count.value))()
        error = query(2, ctypes.byref(path_count), paths,
                      ctypes.byref(mode_count), modes, None)
        if error == 122:  # ERROR_INSUFFICIENT_BUFFER
            continue
        if error:
            raise OSError(error, "QueryDisplayConfig failed")
        break
    else:
        raise OSError(122, "Display topology changed while reading")

    displays: list[dict[str, Any]] = []
    for path in paths[:path_count.value]:
        if not path.flags & 1:
            continue
        virtual = bool(path.flags & 0x8)
        source_index = path.source.mode_index >> 16 if virtual else path.source.mode_index
        target_index = path.target.mode_index >> 16 if virtual else path.target.mode_index
        source = modes[source_index].mode.source if source_index < mode_count.value else None
        target = modes[target_index].mode.target if target_index < mode_count.value else None

        name = _TargetName()
        name.header = _DeviceHeader(2, ctypes.sizeof(name), path.target.adapter,
                                    path.target.id)
        friendly = name.friendly if device_info(ctypes.byref(name.header)) == 0 else ""

        color = _ColorInfo()
        color.header = _DeviceHeader(15, ctypes.sizeof(color), path.target.adapter,
                                     path.target.id)
        if device_info(ctypes.byref(color.header)) == 0:
            hdr_enabled = color.active_mode == 2
            hdr_supported = bool(color.flags & (1 << 4))
            advanced_color = bool(color.flags & (1 << 1))
        else:
            # The older API combines HDR and wide gamut; do not call that HDR.
            color.header.kind = 9
            color.header.size = ctypes.sizeof(color) - ctypes.sizeof(ctypes.c_uint32)
            if device_info(ctypes.byref(color.header)) == 0:
                hdr_enabled = None
                hdr_supported = None
                advanced_color = bool(color.flags & 2)
            else:
                hdr_enabled = hdr_supported = advanced_color = None

        displays.append({
            "id": f"{_luid(path.target.adapter)}:{path.target.id}",
            "name": friendly or f"显示器 {len(displays) + 1}",
            "adapter_luid": _luid(path.target.adapter),
            "source_id": path.source.id,
            "width": source.width if source else None,
            "height": source.height if source else None,
            "signal_width": target.active.width if target else None,
            "signal_height": target.active.height if target else None,
            "refresh_hz": (_rate(target.vertical) if target else None)
            or _rate(path.target.refresh),
            "hdr_enabled": hdr_enabled,
            "hdr_supported": hdr_supported,
            "advanced_color_enabled": advanced_color,
        })
    displays.sort(key=lambda item: item["id"])
    return {"count": len(displays), "displays": displays}
