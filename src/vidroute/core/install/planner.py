"""Install planning: what would be installed, why, how big, under which license.

The same plan backs every entry point (CLI prompt, ``--dry-run``, MCP
``needs_setup``, a future desktop wizard). Nothing is downloaded until the user
confirms it.

Profiles (§3.1):
  * ``lite`` — the base package: Silero VAD + SenseVoice-Small (D3/D4 ready).
  * ``auto`` — base package, plus Whisper small when the system language is not
    covered by SenseVoice (zh/en/ja/ko/yue).
  * ``full`` — auto + OCR + Whisper small, and the CUDA ASR when an NVIDIA GPU
    with enough VRAM is present (still smoke-tested before use).
"""

from __future__ import annotations

import locale
import os
import platform
from typing import Any

from vidroute.core import paths
from vidroute.core.doctor.runtimes import UV_VERSION, find_uv
from vidroute.core.errors import InsufficientHardware, UsageError
from vidroute.core.install import envs as envmod
from vidroute.core.install.manifest import Manifest, ModelEntry, load_manifest
from vidroute.core.util import ffmpeg, proc

PROFILES = ("auto", "lite", "full")
BASE = ("vad-silero", "asr-sensevoice-small-int8")
SENSEVOICE_LANGS = {"zh", "en", "ja", "ko", "yue"}
PYTHON_RUNTIME_MB = 105
UV_MB = 20


def system_language() -> str | None:
    for var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        raw = os.environ.get(var)
        if raw and raw not in ("C", "POSIX") and not raw.startswith("C."):
            return raw.split(".")[0].split(":")[0].replace("_", "-").lower()
    if os.name == "nt":
        try:
            import ctypes

            lcid = ctypes.windll.kernel32.GetUserDefaultUILanguage()  # type: ignore[attr-defined]
            name = locale.windows_locale.get(lcid)
            if name:
                return name.replace("_", "-").lower()
        except (AttributeError, OSError):
            pass
    try:
        loc = locale.getlocale()[0]
    except ValueError:
        loc = None
    return loc.replace("_", "-").lower() if loc else None


def default_for(cap: str, tier: str, manifest: Manifest) -> list[str]:
    if cap == "vad":
        return ["vad-silero"]
    if cap == "asr":
        return ["asr-sensevoice-small-int8"]
    if cap == "ocr":
        return ["ocr-ppocrv6-tiny" if tier == "cpu-lite" else "ocr-ppocrv6-small"]
    if cap == "scene":
        return []  # built in (ffmpeg)
    raise UsageError(f"Unknown capability {cap!r}; use asr, vad, ocr or scene")


def _python_runtime_present(version: str) -> bool:
    root = paths.runtimes_dir() / "python"
    return root.is_dir() and any(p.name.startswith(f"cpython-{version}") for p in root.iterdir())


def _ffmpeg_item() -> dict[str, Any] | None:
    if ffmpeg.find_tool("ffmpeg") and ffmpeg.find_tool("ffprobe"):
        return None
    if platform.system() == "Windows" and proc.which("winget"):
        cmd = ["winget", "install", "--id", "Gyan.FFmpeg", "-e", "--silent",
               "--accept-package-agreements", "--accept-source-agreements"]
        return {"kind": "ffmpeg", "action": "run_with_yes", "command": cmd,
                "note": "Runs only with --yes; installs FFmpeg system-wide via winget (Gyan.FFmpeg)."}
    return {"kind": "ffmpeg", "action": "manual", "command": ffmpeg.install_hint(),
            "note": "vidroute does not install system packages itself; run the command, then re-run install."}


def plan_install(
    capabilities: list[str] | None = None,
    profile: str = "auto",
    model_ids: list[str] | None = None,
    accept_licenses: list[str] | None = None,
    mirror: str = "auto",
    doctor_data: dict[str, Any] | None = None,
    force: bool = False,
    manifest: Manifest | None = None,
) -> dict[str, Any]:
    manifest = manifest or load_manifest()
    if profile not in PROFILES:
        raise UsageError(f"--profile must be one of {', '.join(PROFILES)}")
    if doctor_data is None:
        from vidroute.core import doctor

        doctor_data = doctor.run(refresh=False, offline=True)
    tier = doctor_data.get("tier", "cpu-lite")
    gpus = doctor_data.get("gpus") or []
    cuda_candidate = any(g.get("vendor") == "nvidia" and (g.get("vram_gb") or 0) >= 4 for g in gpus)
    ram = doctor_data.get("ram_gb")
    lang = system_language()
    lang_base = (lang or "").split("-")[0]

    explicit = list(dict.fromkeys(model_ids or []))
    selected: dict[str, str] = {}
    for mid in explicit:
        manifest.model(mid)
        selected[mid] = "requested with --model"
    for cap in capabilities or []:
        for mid in default_for(cap, tier, manifest):
            selected.setdefault(mid, f"default {cap} engine for tier {tier}")
    if not explicit and not capabilities:
        for mid in BASE:
            selected[mid] = "base package (makes D3/D4 work out of the box)"
        if profile in ("auto", "full") and lang_base and lang_base not in SENSEVOICE_LANGS:
            selected["asr-whisper-small-int8"] = f"system language '{lang}' is not covered by SenseVoice"
        if profile == "full":
            selected.setdefault("asr-whisper-small-int8", "full profile: multilingual ASR")
            selected.setdefault(default_for("ocr", tier, manifest)[0], "full profile: OCR for slides/screens")
            if cuda_candidate:
                selected["asr-faster-whisper-large-v3-turbo"] = "full profile: NVIDIA GPU found (smoke-tested before use)"

    skipped: list[dict[str, Any]] = []
    models: list[ModelEntry] = []
    for mid, why in selected.items():
        entry = manifest.model(mid)
        problem = None
        if entry.builtin:
            problem = "built in (uses ffmpeg), nothing to install"
        elif entry.status == "experimental" and mid not in explicit:
            problem = "experimental; install explicitly with --model"
        elif entry.requires_gpu == "cuda" and not cuda_candidate:
            problem = "needs an NVIDIA GPU with >= 4 GB VRAM"
        elif entry.min_ram_gb and ram and ram < entry.min_ram_gb:
            problem = f"needs {entry.min_ram_gb} GB RAM, this machine has {ram} GB"
        if problem and mid in explicit and "built in" not in problem and "experimental" not in problem:
            raise InsufficientHardware(f"Cannot install {mid}: {problem}.", hint="vidroute doctor --refresh")
        if problem:
            skipped.append({"id": mid, "reason": problem})
            continue
        models.append(entry)

    accepted = set(accept_licenses or [])
    items: list[dict[str, Any]] = []
    ff = _ffmpeg_item()
    if ff:
        items.append(ff)

    groups: dict[str, str] = {}
    for entry in models:
        if entry.env_group:
            variant = "cuda" if entry.requires_gpu == "cuda" else "cpu"
            if groups.get(entry.env_group) != "cuda":
                groups[entry.env_group] = variant
    env_items = []
    for name, variant in groups.items():
        group = manifest.env_groups[name]
        if not force and envmod.env_ready(group, variant):
            skipped.append({"id": f"env:{name}", "reason": "already installed"})
            continue
        size = group.approx_size_mb_cuda if variant == "cuda" and group.approx_size_mb_cuda else group.approx_size_mb
        env_items.append({"kind": "env", "id": name, "variant": variant, "python": group.python,
                          "size_mb": size, "description": group.description, "path": str(group.path),
                          "lock": envmod.lock_path(group, variant).name})
    if env_items:
        if find_uv() is None:
            items.append({"kind": "uv", "id": "uv", "version": UV_VERSION, "size_mb": UV_MB,
                          "path": str(paths.runtimes_dir() / "uv"), "note": "pinned + hash-checked from PyPI"})
        pythons = {i["python"] for i in env_items}
        for ver in sorted(pythons):
            if not _python_runtime_present(ver):
                items.append({"kind": "python", "id": f"python-{ver}", "version": ver, "size_mb": PYTHON_RUNTIME_MB,
                              "path": str(paths.runtimes_dir() / "python"), "note": "uv-managed, not added to PATH or registry"})
        items.extend(env_items)

    reusable = {r["id"]: r for r in doctor_data.get("reusable_models") or []}
    license_needed = []
    for entry in models:
        st = entry.installed_status()
        if st["installed"] and not force:
            skipped.append({"id": entry.id, "reason": "already installed"})
            continue
        needs_acceptance = entry.license.needs_acceptance and entry.id not in accepted and entry.license.spdx not in accepted
        if needs_acceptance:
            license_needed.append(entry.id)
        bundled = any(s.type == "bundled" for s in entry.sources)
        action = "reuse" if entry.id in reusable else ("copy_or_download" if bundled else "download")
        items.append({
            "kind": "model",
            "id": entry.id,
            "capability": entry.capability,
            "action": action,
            "reason": selected[entry.id],
            "size_mb": 0 if action == "reuse" else entry.size_mb,
            "gpu": entry.requires_gpu,
            "status": entry.status,
            "license": {"spdx": entry.license.spdx, "name": entry.license.name, "url": entry.license.url,
                        "notes": entry.license.notes, "restricted": entry.license.restricted,
                        "gated": entry.license.gated},
            "needs_license_acceptance": needs_acceptance,
            "sources": sorted({s.type for s in entry.sources}),
            "reuse_path": reusable.get(entry.id, {}).get("path"),
            "path": str(entry.model_dir),
        })

    download_mb = round(sum(float(i.get("size_mb") or 0) for i in items if i["kind"] in ("model", "env", "python", "uv")), 1)
    disk_free = doctor_data.get("disk_free_gb")
    disk_needed_gb = round(download_mb * 2.2 / 1024, 2)  # archive + extracted + pip cache headroom
    actionable = [i for i in items if not (i["kind"] == "ffmpeg" and i["action"] == "manual")]
    args = _cli_args(capabilities, profile, explicit, mirror, accepted)
    return {
        "profile": profile,
        "tier": tier,
        "system_language": lang,
        "mirror": mirror,
        "items": items,
        "skipped": skipped,
        "total_download_mb": download_mb,
        "disk_needed_gb": disk_needed_gb,
        "disk_free_gb": disk_free,
        "disk_ok": disk_free is None or disk_free >= disk_needed_gb,
        "license_acceptance_required": license_needed,
        "force": force,
        "nothing_to_do": not actionable,
        "home": str(paths.home()),
        "confirm_command": f"vidroute install{args} --yes",
        "accept_command": (f"vidroute install{args}" + "".join(f" --accept-license {m}" for m in license_needed) + " --yes")
        if license_needed else None,
    }


def _cli_args(capabilities: list[str] | None, profile: str, models: list[str], mirror: str, accepted: set[str]) -> str:
    parts = []
    for c in capabilities or []:
        parts.append(f" --capability {c}")
    for m in models:
        parts.append(f" --model {m}")
    if not capabilities and not models and profile != "auto":
        parts.append(f" --profile {profile}")
    if mirror != "auto":
        parts.append(f" --mirror {mirror}")
    for a in sorted(accepted):
        parts.append(f" --accept-license {a}")
    return "".join(parts)


def describe(plan: dict[str, Any]) -> str:
    """Human-readable plan for CLI prompts."""
    lines = [f"Install plan ({plan['profile']}, tier {plan['tier']}) -> {plan['home']}"]
    for i in plan["items"]:
        if i["kind"] == "model":
            lic = i["license"]
            flag = "  [LICENSE ACCEPTANCE REQUIRED]" if i["needs_license_acceptance"] else ""
            lines.append(f"  model  {i['id']:<34} {i['size_mb']:>7.1f} MB  {lic['spdx']}{flag}")
            lines.append(f"         {i['reason']}")
            if lic.get("notes"):
                lines.append(f"         license: {lic['notes']}")
        elif i["kind"] == "env":
            lines.append(f"  env    {i['id']:<34} {i['size_mb'] or 0:>7.1f} MB  ({i['variant']}, Python {i['python']}, {i['lock']})")
        elif i["kind"] in ("python", "uv"):
            lines.append(f"  {i['kind']:<6} {i['id']:<34} {i['size_mb']:>7.1f} MB  ({i.get('note', '')})")
        elif i["kind"] == "ffmpeg":
            cmd = i["command"] if isinstance(i["command"], str) else " ".join(i["command"])
            lines.append(f"  ffmpeg missing -> {cmd}  ({i['note']})")
    for s in plan["skipped"]:
        lines.append(f"  skip   {s['id']:<34} {s['reason']}")
    lines.append(f"  total download ~{plan['total_download_mb']} MB; needs ~{plan['disk_needed_gb']} GB free "
                 f"(free: {plan['disk_free_gb']} GB)")
    return "\n".join(lines)
