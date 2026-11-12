"""``vidroute doctor``: detect what this machine can run locally.

All probes run concurrently with a per-probe timeout and never block; results
are cached in state.json (TTL) and ``--refresh`` forces a re-check. Install and
remove operations invalidate the cache. Which agent hosts are installed and
whether they see the vidroute skill is cheap and checked on every run.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from functools import partial
from typing import Any, Callable

from vidroute.core import config, paths, state
from vidroute.core.context import Ctx
from vidroute.core.doctor import external, gpu, runtimes, system, tier
from vidroute.core.install.manifest import Manifest, load_manifest
from vidroute.core.util import ffmpeg
from vidroute.core.util.proc import run_parallel

CACHE_KEY = "doctor"
CACHE_TTL = 6 * 3600
PROBE_TIMEOUT = 5.0
CAPS = ("asr", "vad", "ocr", "scene", "diarize")


def invalidate() -> None:
    state.cache_clear(CACHE_KEY)


def _tasks(manifest: Manifest, offline: bool) -> dict[str, Callable[[], Any]]:
    tasks: dict[str, Callable[[], Any]] = {
        "os": system.os_info,
        "cpu": system.cpu_info,
        "memory": system.memory_info,
        "disk": lambda: system.disk_info(paths.home()),
        "ffmpeg": lambda: ffmpeg.detect(timeout=PROBE_TIMEOUT).to_dict(),
        "nvidia": partial(gpu.nvidia_gpus, PROBE_TIMEOUT),
        "platform_gpus": partial(gpu.platform_gpus, PROBE_TIMEOUT),
        "uv": runtimes.uv_info,
        "models": partial(runtimes.installed_models, manifest),
        "reusable": partial(runtimes.reusable_models, manifest),
        "ollama": external.ollama,
        "lmstudio": external.lmstudio,
    }
    for name, group in manifest.env_groups.items():
        tasks[f"env:{name}"] = partial(runtimes.env_status, group, True, PROBE_TIMEOUT - 0.5)
    if not offline:
        for name, url in external.MIRRORS.items():
            tasks[f"mirror:{name}"] = partial(external.check_mirror, url, PROBE_TIMEOUT - 1)
    return tasks


def summarize_capabilities(
    manifest: Manifest, ffmpeg_found: bool, envs: dict[str, Any], installed: list[dict[str, Any]]
) -> dict[str, Any]:
    installed_ids = {m["id"] for m in installed if m.get("installed")}
    out: dict[str, Any] = {}
    for cap in CAPS:
        ready = []
        for m in manifest.by_capability(cap):
            if m.status == "experimental":
                continue
            if m.builtin:
                ok = ffmpeg_found
            else:
                env = envs.get(m.env_group or "", {})
                ok = m.id in installed_ids and bool(env.get("exists")) and env.get("import_ok") is not False
            if ok:
                ready.append(m.id)
        entry: dict[str, Any] = {"ready": bool(ready), "models": ready}
        if cap == "vad" and ready == ["vad-energy"]:
            entry["note"] = "只有基于能量的回退；安装 vad-silero 才能真正检测人声"
        if not ready or entry.get("note"):
            entry["hint"] = f"vidroute install --capability {cap}"
        out[cap] = entry
    return out


def collect(offline: bool | None = None) -> dict[str, Any]:
    manifest = load_manifest()
    offline = bool(config.get("offline")) if offline is None else offline
    started = time.monotonic()
    results = run_parallel(_tasks(manifest, offline), timeout=PROBE_TIMEOUT)
    probe_errors = {name: r.error for name, r in results.items() if not r.ok}

    def value(name: str, default: Any) -> Any:
        r = results.get(name)
        return r.value if r is not None and r.ok else default

    cpu = value("cpu", {"model": None, "avx2": None, "x86": None})
    memory = value("memory", {"total_gb": None, "available_gb": None})
    ff = value("ffmpeg", {"found": False, "error": probe_errors.get("ffmpeg", "检测失败"),
                          "install_hint": ffmpeg.install_hint()})
    nvidia = value("nvidia", {"available": False, "gpus": [], "error": probe_errors.get("nvidia")})
    plat = value("platform_gpus", {"gpus": [], "error": probe_errors.get("platform_gpus")})
    gpus = gpu.merge(nvidia, plat)
    envs = {name: value(f"env:{name}", {"exists": False, "error": probe_errors.get(f"env:{name}")})
            for name in manifest.env_groups}
    installed = value("models", [])
    smoke = state.load().get("gpu_smoke") or {}
    tier_info = tier.decide(memory.get("total_gb"), cpu, gpus, smoke)
    mirrors = {name: value(f"mirror:{name}", {"ok": False, "error": probe_errors.get(f"mirror:{name}")})
               for name in external.MIRRORS} if not offline else {}

    return {
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "probe_seconds": round(time.monotonic() - started, 2),
        "home": str(paths.home()),
        "os": value("os", {}),
        "cpu": cpu,
        "ram_gb": memory.get("total_gb"),
        "ram_limited": bool(memory.get("limited")),
        "ram_available_gb": memory.get("available_gb"),
        "disk_free_gb": value("disk", {}).get("free_gb"),
        "ffmpeg": ff,
        "gpus": gpus,
        "gpu_probe": {"nvidia_smi": nvidia.get("error") or "ok", "platform": plat.get("error") or "ok"},
        "gpu_smoke": smoke,
        "runtimes": {"uv": value("uv", {"found": False}), "envs": envs},
        "installed_models": installed,
        "reusable_models": value("reusable", []),
        "external": {"ollama": value("ollama", {"running": False}), "lmstudio": value("lmstudio", {"running": False})},
        "network": {"checked": not offline, "mirrors": mirrors,
                    "online": any(m.get("ok") for m in mirrors.values()) if mirrors else None},
        "tier": tier_info["tier"],
        "tier_reasons": tier_info["reasons"],
        "upgrade_hints": tier_info["hints"],
        "capabilities": summarize_capabilities(manifest, bool(ff.get("found")), envs, installed),
        "probe_errors": {k: v for k, v in probe_errors.items() if v},
    }


def derive_warnings(data: dict[str, Any], ctx: Ctx) -> None:
    ff = data.get("ffmpeg") or {}
    if not ff.get("found"):
        ctx.warn("ffmpeg_missing", f"找不到 ffmpeg/ffprobe（{ff.get('error')}）。", ff.get("install_hint"))
    else:
        enc = ff.get("encoders") or {}
        if enc and not enc.get("libx264"):
            ctx.warn("ffmpeg_no_libx264", "这个 ffmpeg 构建没有 libx264；原生视频的转码会受限。",
                     ffmpeg.install_hint())
    for name, err in (data.get("probe_errors") or {}).items():
        if name.startswith("mirror:"):
            continue
        ctx.warn("probe_failed", f"检测项 '{name}' 未完成：{err}")
    for g in data.get("gpus") or []:
        if g.get("vram_source") == "cim-approx":
            ctx.warn("vram_approximate",
                     f"{g['name']}：来自 Win32_VideoController 的显存上限为 4 GB，只是近似值。")
    for hint in data.get("upgrade_hints") or []:
        ctx.warn("gpu_upgrade_available", hint)
    for cap, info in (data.get("capabilities") or {}).items():
        if not info.get("ready"):
            ctx.warn("capability_missing", f"没有安装本地 {cap} 引擎。", info.get("hint"))
    net = data.get("network") or {}
    if net.get("checked") and net.get("online") is False:
        ctx.warn("offline", "所有下载镜像都不可达；网络恢复之前安装都会失败。")
    for host in (data.get("integrations") or {}).get("hosts") or []:
        skill = host.get("skill") or {}
        if skill.get("managed") and (not skill.get("current") or skill.get("python_ok") is False):
            ctx.warn("skill_outdated", f"{host['name']}：{skill['path']} 中的 vidroute 技能来自 vidroute "
                                       f"{skill.get('version')}，或指向一个已不存在的 Python。",
                     "vidroute setup --no-install")


def run(refresh: bool = False, offline: bool | None = None, ctx: Ctx | None = None) -> dict[str, Any]:
    ctx = ctx or Ctx()
    data = None if refresh else state.cache_get(CACHE_KEY, CACHE_TTL)
    if data is None:
        ctx.progress("doctor", None, None, "正在检测系统")
        data = collect(offline)
        paths.ensure_dir(paths.home())
        state.cache_put(CACHE_KEY, data)
        data = dict(data, cached=False)
    else:
        data = dict(data, cached=True)
    from vidroute.core import integrate

    data["integrations"] = integrate.status()
    derive_warnings(data, ctx)
    return data
