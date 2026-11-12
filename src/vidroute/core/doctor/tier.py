"""Local capability tier: how the *local* half of the pipeline can run.

* ``cpu-lite``  : < 8 GB RAM, no AVX2 on x86, or RAM unknown (machines sold as
  8 GB report 7.5–7.99 GB usable, so 7.5 GB counts as the 8 GB class)
* ``cpu-full``  : everything else without a verified GPU
* ``gpu-cuda``  : NVIDIA GPU with >= 4 GB VRAM **and** a passed CUDA smoke test
* ``gpu-vulkan``: AMD/Intel GPU with a passed Vulkan smoke test (no engine yet)

A GPU never raises the tier by itself: it is an upgrade that has to be proven
by a smoke test (recorded in state.json by `vidroute install`).
"""

from __future__ import annotations

from typing import Any

TIERS = ("cpu-lite", "cpu-full", "gpu-cuda", "gpu-vulkan")
MIN_RAM_FULL_GB = 8.0
# Firmware/iGPU reservations make "8 GB" machines report ~7.5-7.99 GB (e.g. 7.99 GB on
# GitHub's windows-latest runners); treat those as the 8 GB class.
RAM_TOLERANCE_GB = 0.5
MIN_VRAM_CUDA_GB = 4.0


def decide(
    ram_gb: float | None,
    cpu: dict[str, Any],
    gpus: list[dict[str, Any]],
    smoke: dict[str, Any] | None = None,
) -> dict[str, Any]:
    smoke = smoke or {}
    reasons: list[str] = []
    hints: list[str] = []

    nvidia = [g for g in gpus if g.get("vendor") == "nvidia"]
    cuda_candidate = None
    for g in nvidia:
        vram = g.get("vram_gb")
        if vram is None:
            reasons.append(f"{g.get('name')}：显存未知，视为不足")
        elif vram < MIN_VRAM_CUDA_GB:
            reasons.append(f"{g.get('name')}：显存 {vram} GB < {MIN_VRAM_CUDA_GB} GB")
        else:
            cuda_candidate = g
            break

    cuda_smoke = (smoke.get("cuda") or {}).get("status")
    vulkan_smoke = (smoke.get("vulkan") or {}).get("status")

    if cuda_candidate and cuda_smoke == "passed":
        reasons.append(f"{cuda_candidate['name']}（{cuda_candidate['vram_gb']} GB）通过了 CUDA 冒烟测试")
        return {"tier": "gpu-cuda", "reasons": reasons, "hints": hints, "gpu_candidate": "cuda"}
    if cuda_candidate:
        if cuda_smoke == "failed":
            reasons.append(
                f"CUDA 冒烟测试失败（{(smoke.get('cuda') or {}).get('reason', '见 state.json')}）；使用 CPU"
            )
        else:
            hints.append(
                f"{cuda_candidate['name']}（{cuda_candidate['vram_gb']} GB）可以启用："
                "vidroute install --model asr-faster-whisper-large-v3-turbo"
            )
    if vulkan_smoke == "passed":
        reasons.append("Vulkan 冒烟测试通过")
        return {"tier": "gpu-vulkan", "reasons": reasons, "hints": hints, "gpu_candidate": "vulkan"}

    if ram_gb is None:
        reasons.append("内存未知：保守地使用 cpu-lite")
        tier = "cpu-lite"
    elif ram_gb < MIN_RAM_FULL_GB - RAM_TOLERANCE_GB:
        reasons.append(f"内存 {ram_gb} GB < {MIN_RAM_FULL_GB} GB")
        tier = "cpu-lite"
    elif cpu.get("x86") and cpu.get("avx2") is False:
        reasons.append("x86 CPU 不支持 AVX2")
        tier = "cpu-lite"
    else:
        avx = "AVX2" if cpu.get("avx2") else ("ARM/NEON" if not cpu.get("x86") else "AVX2 未知")
        reasons.append(f"内存 {ram_gb} GB，{avx}")
        tier = "cpu-full"
    return {
        "tier": tier,
        "reasons": reasons,
        "hints": hints,
        "gpu_candidate": "cuda" if cuda_candidate else None,
    }
