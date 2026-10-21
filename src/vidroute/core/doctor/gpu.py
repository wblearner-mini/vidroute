"""GPU discovery: nvidia-smi first, then a platform listing (CIM on Windows, sysfs on Linux).

Lessons from other projects (SmartSub): GPU detection can hang on first run or
misidentify cards with new drivers. Every probe here has a hard timeout, never
blocks doctor, and unknown VRAM is reported as ``null`` (callers treat that
conservatively).
"""

from __future__ import annotations

import json
import os
import platform
from pathlib import Path
from typing import Any

from vidroute.core.util import proc

TIMEOUT = 5.0
VENDOR_IDS = {"10de": "nvidia", "1002": "amd", "1022": "amd", "8086": "intel", "106b": "apple", "5143": "qualcomm"}


def _nvidia_smi_path() -> Path | None:
    extra = []
    if platform.system() == "Windows":
        pf = os.environ.get("ProgramFiles", r"C:\Program Files")
        extra.append(Path(pf) / "NVIDIA Corporation" / "NVSMI")
        extra.append(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32")
    return proc.which("nvidia-smi", extra)


def parse_nvidia_smi(text: str, with_compute_cap: bool) -> list[dict[str, Any]]:
    gpus = []
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3 or not parts[0]:
            continue
        name, mem, driver = parts[0], parts[1], parts[2]
        try:
            vram_gb: float | None = round(float(mem) / 1024, 2)
        except ValueError:
            vram_gb = None
        cc = parts[3] if with_compute_cap and len(parts) > 3 and parts[3] not in ("[N/A]", "N/A", "") else None
        gpus.append(
            {
                "vendor": "nvidia",
                "name": name,
                "vram_gb": vram_gb,
                "vram_source": "nvidia-smi" if vram_gb is not None else "unknown",
                "driver": driver or None,
                "compute_cap": cc,
            }
        )
    return gpus


def nvidia_gpus(timeout: float = TIMEOUT) -> dict[str, Any]:
    exe = _nvidia_smi_path()
    if exe is None:
        return {"available": False, "gpus": [], "error": "nvidia-smi not found"}
    base = ["--format=csv,noheader,nounits"]
    res = proc.run([exe, "--query-gpu=name,memory.total,driver_version,compute_cap", *base], timeout=timeout)
    with_cc = True
    if not res.ok and not res.timed_out:
        # Older drivers do not know the compute_cap field.
        res = proc.run([exe, "--query-gpu=name,memory.total,driver_version", *base], timeout=timeout)
        with_cc = False
    if res.timed_out:
        return {"available": False, "gpus": [], "error": f"nvidia-smi timed out after {timeout:.0f}s"}
    if not res.ok:
        return {"available": False, "gpus": [], "error": (res.stderr or res.stdout).strip()[:300]}
    gpus = parse_nvidia_smi(res.stdout, with_cc)
    return {"available": bool(gpus), "gpus": gpus, "error": None}


def _vendor_from(name: str, pnp: str) -> str:
    pnp = (pnp or "").lower()
    for vid, vendor in VENDOR_IDS.items():
        if f"ven_{vid}" in pnp:
            return vendor
    low = (name or "").lower()
    for key, vendor in (("nvidia", "nvidia"), ("geforce", "nvidia"), ("quadro", "nvidia"), ("radeon", "amd"),
                        ("amd", "amd"), ("intel", "intel"), ("arc", "intel"), ("qualcomm", "qualcomm")):
        if key in low:
            return vendor
    return "unknown"


def parse_cim_json(text: str) -> list[dict[str, Any]]:
    """Parse ``Get-CimInstance Win32_VideoController | ConvertTo-Json`` output.

    AdapterRAM is a 32-bit field and saturates at 4 GiB, so it is only an
    approximation (``vram_source: cim-approx``) and never trusted for tiering.
    """
    text = text.strip()
    if not text:
        return []
    data = json.loads(text)
    if isinstance(data, dict):
        data = [data]
    gpus = []
    for item in data:
        name = str(item.get("Name") or "").strip()
        if not name or "basic display" in name.lower() or "remote display" in name.lower():
            continue
        ram = item.get("AdapterRAM")
        approx = round(int(ram) / 1024**3, 2) if isinstance(ram, (int, float)) and ram > 0 else None
        gpus.append(
            {
                "vendor": _vendor_from(name, str(item.get("PNPDeviceID") or "")),
                "name": name,
                "vram_gb": None,
                "vram_approx_gb": approx,
                "vram_source": "cim-approx" if approx else "unknown",
                "driver": item.get("DriverVersion"),
            }
        )
    return gpus


def windows_video_controllers(timeout: float = TIMEOUT) -> dict[str, Any]:
    ps = proc.which("powershell") or proc.which("pwsh")
    if ps is None:
        return {"gpus": [], "error": "PowerShell not found"}
    cmd = (
        "Get-CimInstance Win32_VideoController | "
        "Select-Object Name,AdapterRAM,DriverVersion,PNPDeviceID | ConvertTo-Json -Compress"
    )
    res = proc.run([ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", cmd], timeout=timeout)
    if not res.ok:
        return {"gpus": [], "error": ("timed out" if res.timed_out else (res.stderr or "").strip()[:300])}
    try:
        return {"gpus": parse_cim_json(res.stdout), "error": None}
    except ValueError as exc:
        return {"gpus": [], "error": f"unparseable CIM output: {exc}"}


def linux_drm_gpus(root: Path = Path("/sys/class/drm")) -> dict[str, Any]:
    gpus = []
    seen = set()
    try:
        cards = sorted(p for p in root.glob("card[0-9]*") if "-" not in p.name)
    except OSError:
        cards = []
    for card in cards:
        dev = card / "device"
        try:
            vendor_id = (dev / "vendor").read_text().strip().lower().removeprefix("0x")
        except OSError:
            continue
        try:
            driver = os.path.basename(os.readlink(dev / "driver"))
        except OSError:
            driver = None
        key = (vendor_id, os.path.realpath(dev))
        if key in seen:
            continue
        seen.add(key)
        vram = None
        try:
            vram = round(int((dev / "mem_info_vram_total").read_text().strip()) / 1024**3, 2)  # amdgpu only
        except (OSError, ValueError):
            pass
        gpus.append(
            {
                "vendor": VENDOR_IDS.get(vendor_id, "unknown"),
                "name": f"{VENDOR_IDS.get(vendor_id, vendor_id)} GPU ({card.name})",
                "vram_gb": vram,
                "vram_source": "sysfs" if vram else "unknown",
                "driver": driver,
            }
        )
    return {"gpus": gpus, "error": None}


def platform_gpus(timeout: float = TIMEOUT) -> dict[str, Any]:
    system = platform.system()
    if system == "Windows":
        return windows_video_controllers(timeout)
    if system == "Linux":
        return linux_drm_gpus()
    if system == "Darwin":
        if platform.machine() == "arm64":
            return {"gpus": [{"vendor": "apple", "name": "Apple GPU (unified memory)", "vram_gb": None,
                              "vram_source": "unknown", "driver": None}], "error": None}
        return {"gpus": [], "error": None}
    return {"gpus": [], "error": None}


def merge(nvidia: dict[str, Any], plat: dict[str, Any]) -> list[dict[str, Any]]:
    """nvidia-smi entries are authoritative; add platform entries for other vendors."""
    gpus = list(nvidia.get("gpus") or [])
    have_nvidia = bool(gpus)
    for g in plat.get("gpus") or []:
        if g["vendor"] == "nvidia" and have_nvidia:
            continue
        gpus.append(g)
    return gpus
