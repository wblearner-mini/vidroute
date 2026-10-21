"""OS, CPU, memory and disk facts using only the standard library."""

from __future__ import annotations

import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Any

from vidroute.core.util import proc

GIB = 1024**3


def os_info() -> dict[str, Any]:
    system = platform.system()
    info: dict[str, Any] = {
        "system": system,
        "release": platform.release(),
        "version": platform.version(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "python_executable": sys.executable,
    }
    if system == "Linux":
        try:
            info["wsl"] = "microsoft" in Path("/proc/version").read_text(encoding="utf-8", errors="replace").lower()
        except OSError:
            info["wsl"] = False
    return info


# ----------------------------------------------------------------------- CPU


def parse_proc_cpuinfo(text: str) -> dict[str, Any]:
    model = None
    flags: set[str] = set()
    for line in text.splitlines():
        key, _, value = line.partition(":")
        key = key.strip().lower()
        if key in ("model name", "hardware", "cpu model") and model is None:
            model = value.strip()
        elif key in ("flags", "features") and not flags:
            flags = set(value.split())
    return {
        "model": model,
        "avx2": ("avx2" in flags) if flags else None,
        "avx512": any(f.startswith("avx512") for f in flags) if flags else None,
    }


def _windows_cpu() -> dict[str, Any]:
    out: dict[str, Any] = {"model": None, "avx2": None, "avx512": None}
    try:
        import winreg  # type: ignore[import-not-found]

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
            out["model"] = str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
    except OSError:
        pass
    try:
        import ctypes

        is_present = ctypes.windll.kernel32.IsProcessorFeaturePresent  # type: ignore[attr-defined]
        # PF_AVX2_INSTRUCTIONS_AVAILABLE = 40, PF_AVX512F_INSTRUCTIONS_AVAILABLE = 41 (Windows 10+).
        out["avx2"] = bool(is_present(40))
        out["avx512"] = bool(is_present(41))
    except (AttributeError, OSError):
        pass
    return out


def _macos_cpu() -> dict[str, Any]:
    out: dict[str, Any] = {"model": None, "avx2": None, "avx512": None}
    res = proc.run(["sysctl", "-n", "machdep.cpu.brand_string"], timeout=3)
    if res.ok:
        out["model"] = res.stdout.strip()
    if platform.machine() == "x86_64":
        res = proc.run(["sysctl", "-n", "hw.optional.avx2_0"], timeout=3)
        if res.ok:
            out["avx2"] = res.stdout.strip() == "1"
    return out


def cpu_info() -> dict[str, Any]:
    system = platform.system()
    if system == "Linux":
        try:
            data = parse_proc_cpuinfo(Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace"))
        except OSError:
            data = {"model": None, "avx2": None, "avx512": None}
    elif system == "Windows":
        data = _windows_cpu()
    elif system == "Darwin":
        data = _macos_cpu()
    else:
        data = {"model": None, "avx2": None, "avx512": None}
    arch = platform.machine().lower()
    data["arch"] = arch
    data["logical_cores"] = os.cpu_count()
    # AVX2 only matters on x86; ARM CPUs use NEON, which every runtime we ship supports.
    data["x86"] = arch in ("x86_64", "amd64", "i386", "i686", "x86")
    return data


# -------------------------------------------------------------------- memory


def parse_meminfo(text: str) -> dict[str, float | None]:
    values: dict[str, int] = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            values[key.strip()] = int(parts[0]) * 1024
    total = values.get("MemTotal")
    avail = values.get("MemAvailable", values.get("MemFree"))
    return {
        "total_gb": round(total / GIB, 2) if total else None,
        "available_gb": round(avail / GIB, 2) if avail else None,
    }


def _windows_memory() -> dict[str, float | None]:
    import ctypes

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    stat = MEMORYSTATUSEX()
    stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):  # type: ignore[attr-defined]
        return {"total_gb": None, "available_gb": None}
    return {"total_gb": round(stat.ullTotalPhys / GIB, 2), "available_gb": round(stat.ullAvailPhys / GIB, 2)}


def memory_info() -> dict[str, float | None]:
    system = platform.system()
    try:
        if system == "Linux":
            return parse_meminfo(Path("/proc/meminfo").read_text(encoding="utf-8", errors="replace"))
        if system == "Windows":
            return _windows_memory()
        if system == "Darwin":
            res = proc.run(["sysctl", "-n", "hw.memsize"], timeout=3)
            if res.ok and res.stdout.strip().isdigit():
                return {"total_gb": round(int(res.stdout.strip()) / GIB, 2), "available_gb": None}
    except (OSError, AttributeError, ValueError):
        pass
    return {"total_gb": None, "available_gb": None}


# ---------------------------------------------------------------------- disk


def disk_info(path: Path) -> dict[str, float | str | None]:
    probe = path
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        usage = shutil.disk_usage(probe)
    except OSError:
        return {"path": str(path), "free_gb": None, "total_gb": None}
    return {"path": str(path), "free_gb": round(usage.free / GIB, 1), "total_gb": round(usage.total / GIB, 1)}
