"""Install planning: what would be installed, why, how big, under which license.

The same plan backs every entry point (CLI prompt, ``--dry-run``, MCP
``needs_setup``, a future desktop wizard). Nothing is downloaded until the user
confirms it.

Profiles:
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

from vidroute.core import config, paths
from vidroute.core.doctor.runtimes import UV_VERSION, find_uv
from vidroute.core.errors import InsufficientHardware, UsageError
from vidroute.core.install import envs as envmod
from vidroute.core.install import network, portable
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
    if cap == "diarize":
        return ["diarize-pyannote-campplus"]
    raise UsageError(f"未知能力 {cap!r}；请使用 asr、vad、ocr、scene 或 diarize")


def _python_runtime_present(request: str) -> bool:
    """Is the uv-managed Python for ``request`` ("3.12" or "cpython-3.12-windows-x86_64-none") already there?"""
    root = paths.runtimes_dir() / "python"
    if not root.is_dir():
        return False
    if request.startswith("cpython-"):  # cpython-<version>-<os>-<arch>-<libc>
        _, version, os_name, arch, libc = request.split("-")
        suffix = f"-{os_name}-{arch}-{libc}"
        return any(p.name.startswith(f"cpython-{version}") and p.name.endswith(suffix) for p in root.iterdir())
    return any(p.name.startswith(f"cpython-{request}") for p in root.iterdir())


def _python_item(request: str) -> dict[str, Any]:
    note = "由 uv 管理，不加入 PATH 或注册表"
    version, arch = request, None
    if request.startswith("cpython-"):  # cpython-<version>-<os>-<arch>-<libc>
        _, version, _os, arch, _libc = request.split("-")
        note += f"；{arch} 构建（在 Windows on Arm 上仿真运行）"
    return {"kind": "python", "id": f"python-{version}" + (f"-{arch}" if arch else ""), "version": version,
            "request": request, "size_mb": PYTHON_RUNTIME_MB, "path": str(paths.runtimes_dir() / "python"),
            "note": note}


WINGET_FFMPEG = ["winget", "install", "--id", "Gyan.FFmpeg", "-e", "--silent",
                 "--accept-package-agreements", "--accept-source-agreements"]


def _portable_item(entry: Any, why: str) -> dict[str, Any]:
    return {"kind": "ffmpeg", "action": "download", "id": entry.id, "version": entry.version,
            "size_mb": entry.size_mb, "disk_mb": entry.disk_mb, "path": str(entry.path),
            "license": {"spdx": entry.license.spdx, "name": entry.license.name, "url": entry.license.url,
                        "notes": entry.license.notes},
            "note": f"{why}；便携包放入 VIDROUTE_HOME，不加入 PATH"}


def _ffmpeg_item(manifest: Manifest) -> dict[str, Any] | None:
    """ffmpeg is missing: winget on Windows (D-025), the pinned portable build when winget is unavailable or the
    user prefers it (D-043), else the package-manager command for the user to run."""
    if ffmpeg.find_tool("ffmpeg") and ffmpeg.find_tool("ffprobe"):
        return None
    prefer = config.get("ffmpeg_source") or "auto"
    entry = portable.for_platform(manifest)
    winget = platform.system() == "Windows" and proc.which("winget") is not None
    if entry is not None and (prefer == "portable" or (prefer == "auto" and not winget)):
        return _portable_item(entry, "ffmpeg_source=portable" if prefer == "portable" else "没有 winget")
    if winget and prefer != "portable":
        item: dict[str, Any] = {"kind": "ffmpeg", "action": "run_with_yes", "command": WINGET_FFMPEG,
                                "note": "只在 --yes 时运行；通过 winget（Gyan.FFmpeg）为当前用户安装 FFmpeg。"}
        if entry is not None:
            item["fallback"] = _portable_item(entry, "仅在 winget 失败时使用")
        return item
    return {"kind": "ffmpeg", "action": "manual", "command": ffmpeg.install_hint(),
            "note": "vidroute 不会自己安装系统软件包；请运行该命令，然后重新运行 install。"}


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
        raise UsageError(f"--profile 必须是 {', '.join(PROFILES)} 之一")
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
        selected[mid] = "通过 --model 指定"
    for cap in capabilities or []:
        for mid in default_for(cap, tier, manifest):
            selected.setdefault(mid, f"{tier} 档位的默认 {cap} 引擎")
    if not explicit and not capabilities:
        for mid in BASE:
            selected[mid] = "基础包（让 D3/D4 开箱即用）"
        if profile in ("auto", "full") and lang_base and lang_base not in SENSEVOICE_LANGS:
            selected["asr-whisper-small-int8"] = f"SenseVoice 不支持系统语言 '{lang}'"
        if profile == "full":
            selected.setdefault("asr-whisper-small-int8", "full 套餐：多语种 ASR")
            selected.setdefault(default_for("ocr", tier, manifest)[0], "full 套餐：用于幻灯片/屏幕的 OCR")
            if cuda_candidate:
                selected["asr-faster-whisper-large-v3-turbo"] = "full 套餐：发现 NVIDIA GPU（使用前会做冒烟测试）"

    skipped: list[dict[str, Any]] = []
    models: list[ModelEntry] = []
    for mid, why in selected.items():
        entry = manifest.model(mid)
        problem = None
        if entry.builtin:
            problem = "内置（使用 ffmpeg），无需安装"
        elif entry.status == "experimental" and mid not in explicit:
            problem = "实验性；请用 --model 明确安装"
        elif entry.requires_gpu == "cuda" and not cuda_candidate:
            problem = "需要显存 >= 4 GB 的 NVIDIA GPU"
        elif entry.min_ram_gb and ram and ram < entry.min_ram_gb:
            problem = f"需要 {entry.min_ram_gb} GB 内存，本机只有 {ram} GB"
        if problem and mid in explicit and "内置" not in problem and "实验性" not in problem:
            raise InsufficientHardware(f"无法安装 {mid}：{problem}。", hint="vidroute doctor --refresh")
        if problem:
            skipped.append({"id": mid, "reason": problem})
            continue
        models.append(entry)

    accepted = set(accept_licenses or [])
    items: list[dict[str, Any]] = []
    ff = _ffmpeg_item(manifest)
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
            skipped.append({"id": f"env:{name}", "reason": "已安装"})
            continue
        size = group.approx_size_mb_cuda if variant == "cuda" and group.approx_size_mb_cuda else group.approx_size_mb
        env_items.append({"kind": "env", "id": name, "variant": variant, "python": group.python,
                          "python_request": envmod.python_request(group),
                          "size_mb": size, "description": group.description, "path": str(group.path),
                          "lock": envmod.lock_path(group, variant).name})
    if env_items:
        if find_uv() is None:
            items.append({"kind": "uv", "id": "uv", "version": UV_VERSION, "size_mb": UV_MB,
                          "path": str(paths.runtimes_dir() / "uv"), "note": "从 PyPI 获取，固定版本 + 校验哈希"})
        for req in sorted({i["python_request"] for i in env_items}):
            if not _python_runtime_present(req):
                items.append(_python_item(req))
        items.extend(env_items)

    reusable = {r["id"]: r for r in doctor_data.get("reusable_models") or []}
    license_needed = []
    for entry in models:
        st = entry.installed_status()
        if st["installed"] and not force:
            skipped.append({"id": entry.id, "reason": "已安装"})
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

    download_mb = round(sum(float(i.get("size_mb") or 0) for i in items
                            if i["kind"] in ("model", "env", "python", "uv")
                            or (i["kind"] == "ffmpeg" and i["action"] == "download")), 1)
    net = network.resolve(
        mirror,
        need_packages=any(i["kind"] in ("env", "uv") for i in items),
        need_python=any(i["kind"] == "python" for i in items),
        need_models=any(i["kind"] == "model" and i["action"] != "reuse" for i in items),
    )
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
        "network": net,
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
    lines = [f"安装计划（{plan['profile']}，档位 {plan['tier']}）-> {plan['home']}"]
    for i in plan["items"]:
        if i["kind"] == "model":
            lic = i["license"]
            flag = "  [需要接受许可]" if i["needs_license_acceptance"] else ""
            lines.append(f"  模型   {i['id']:<34} {i['size_mb']:>7.1f} MB  {lic['spdx']}{flag}")
            lines.append(f"         {i['reason']}")
            if lic.get("notes"):
                lines.append(f"         许可：{lic['notes']}")
        elif i["kind"] == "env":
            lines.append(f"  环境   {i['id']:<34} {i['size_mb'] or 0:>7.1f} MB  ({i['variant']}, Python {i['python']}, {i['lock']})")
        elif i["kind"] in ("python", "uv"):
            lines.append(f"  {i['kind']:<6} {i['id']:<34} {i['size_mb']:>7.1f} MB  ({i.get('note', '')})")
        elif i["kind"] == "ffmpeg" and i["action"] == "download":
            lines.append(f"  ffmpeg {i['id'] + ' ' + i['version']:<34} {i['size_mb']:>7.1f} MB  {i['license']['spdx']}"
                         f"  ({i['note']})")
        elif i["kind"] == "ffmpeg":
            cmd = i["command"] if isinstance(i["command"], str) else " ".join(i["command"])
            lines.append(f"  缺少 ffmpeg -> {cmd}  （{i['note']}）")
            if i.get("fallback"):
                fb = i["fallback"]
                lines.append(f"         备选：{fb['id']} {fb['version']} ({fb['size_mb']:.1f} MB, {fb['license']['spdx']})")
    for s in plan["skipped"]:
        lines.append(f"  跳过   {s['id']:<34} {s['reason']}")
    lines.append(f"  总下载量约 {plan['total_download_mb']} MB；需要约 {plan['disk_needed_gb']} GB 空闲空间"
                 f"（当前空闲：{plan['disk_free_gb']} GB）")
    net = network.describe(plan.get("network"))
    if net:
        lines.append(f"  {net}")
    return "\n".join(lines)
