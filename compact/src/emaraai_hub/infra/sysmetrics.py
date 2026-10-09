"""CPU and memory of the PC without extra packages (Windows API through ctypes; zeros elsewhere)."""
from __future__ import annotations

import ctypes
import sys

_last: tuple[int, int] | None = None


def _filetime(ft) -> int:
    return (ft.dwHighDateTime << 32) | ft.dwLowDateTime


def cpu_percent() -> float:
    """System-wide CPU use since the previous call."""
    global _last
    if sys.platform != "win32":
        return 0.0

    class FT(ctypes.Structure):
        _fields_ = [("dwLowDateTime", ctypes.c_uint32), ("dwHighDateTime", ctypes.c_uint32)]
    idle, kernel, user = FT(), FT(), FT()
    if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
        return 0.0
    i, total = _filetime(idle), _filetime(kernel) + _filetime(user)
    prev, _last = _last, (i, total)
    if not prev or total == prev[1]:
        return 0.0
    return round(max(0.0, min(100.0, 100.0 * (1 - (i - prev[0]) / (total - prev[1])))), 1)


def memory_percent() -> float:
    if sys.platform != "win32":
        return 0.0

    class MS(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_uint32), ("dwMemoryLoad", ctypes.c_uint32), ("ullTotalPhys", ctypes.c_uint64),
                    ("ullAvailPhys", ctypes.c_uint64), ("ullTotalPageFile", ctypes.c_uint64), ("ullAvailPageFile", ctypes.c_uint64),
                    ("ullTotalVirtual", ctypes.c_uint64), ("ullAvailVirtual", ctypes.c_uint64), ("ullAvailExtendedVirtual", ctypes.c_uint64)]
    ms = MS()
    ms.dwLength = ctypes.sizeof(MS)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms)):
        return 0.0
    return float(ms.dwMemoryLoad)


_chrome: tuple[float, dict] | None = None


def browser_memory(max_age: float = 20.0) -> dict:
    """RAM used by Chrome and Edge right now (all their processes), in MB. Cached: it starts a small system command."""
    global _chrome
    import subprocess
    import time
    if _chrome and time.monotonic() - _chrome[0] < max_age:
        return _chrome[1]
    out = {"chrome_mb": 0, "chrome_processes": 0, "edge_mb": 0}
    if sys.platform == "win32":
        for exe, key in (("chrome.exe", "chrome"), ("msedge.exe", "edge")):
            try:
                txt = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {exe}", "/FO", "CSV", "/NH"], capture_output=True, text=True,
                                     timeout=8, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
            except (OSError, subprocess.SubprocessError):
                continue
            kb = n = 0
            for line in txt.splitlines():
                cols = [c.strip('"') for c in line.split('","')]
                if len(cols) >= 5 and cols[0].lower().strip('"') == exe:
                    digits = "".join(ch for ch in cols[4] if ch.isdigit())
                    kb += int(digits or 0)
                    n += 1
            out[f"{key}_mb"] = kb // 1024
            if key == "chrome":
                out["chrome_processes"] = n
    _chrome = (time.monotonic(), out)
    return out
