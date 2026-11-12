"""``vidroute llm resolve``: effective capabilities = LLM capabilities ∩ host capabilities.

Every effective field reports where it came from (flag / config / provider /
builtin / default). When the model could take video but the host cannot
attach it, the result says so explicitly ("limited by the host, not the
model") — that is what makes the D5 delegate suggestion possible.
"""

from __future__ import annotations

from typing import Any

from vidroute.core import config
from vidroute.core.context import Ctx
from vidroute.core.errors import UsageError
from vidroute.core.llm import profiles as P

# Used when a model claims video (via flag/provider) but no limits are known.
CONSERVATIVE_VIDEO = {
    "audio_in_video": False, "max_seconds": 300, "delivery": ["inline_base64"], "inline_max_mb": 10,
    "inline_is_base64": True, "upload_max_mb": None, "fps": 1, "tokens_per_second": 300,
    "max_video_tokens": None, "max_files": 1,
}
CONSERVATIVE_IMAGE = {"max_images": 20, "tokens_per_image": 1600, "patch_px": None, "max_side_px": 1568,
                      "inline_max_mb": 5, "inline_is_base64": True}
CONSERVATIVE_AUDIO = {"max_seconds": 600, "tokens_per_second": 32, "inline_max_mb": 10}
BASE64_SAFETY = 0.75 * 0.97  # raw bytes per allowed base64 byte, with a margin


def parse_modalities(raw: str | list[str] | None) -> list[str] | None:
    if raw is None:
        return None
    items = raw.split(",") if isinstance(raw, str) else list(raw)
    mods = [m.strip().lower() for m in items if m.strip()]
    bad = set(mods) - set(P.MODALITIES)
    if bad:
        raise UsageError(f"未知模态 {sorted(bad)}；请使用 text,image,audio,video")
    if "text" not in mods:
        mods.insert(0, "text")
    return list(dict.fromkeys(mods))


def parse_yes_no(raw: str | bool | None) -> bool | None:
    if raw is None or isinstance(raw, bool):
        return raw
    val = raw.strip().lower()
    if val in ("yes", "y", "true", "1"):
        return True
    if val in ("no", "n", "false", "0"):
        return False
    raise UsageError(f"应为 yes 或 no，实际为 {raw!r}")


def raw_limit_mb(mb: float | None, is_base64: bool) -> float | None:
    if mb is None:
        return None
    return round(mb * (BASE64_SAFETY if is_base64 else 0.97), 2)


def resolve_llm(
    model_id: str | None,
    modalities: str | list[str] | None = None,
    audio_in_video: str | bool | None = None,
    ctx: Ctx | None = None,
) -> dict[str, Any]:
    ctx = ctx or Ctx()
    model_id = model_id or config.get("default_llm")
    if not model_id:
        raise UsageError("由哪个 LLM 来读结果？请传入 --llm <模型 id>（例如 claude-opus-5-5、gemini-3.8-flash）。",
                         hint="vidroute llm resolve --llm <模型 id>")
    sources: dict[str, str] = {}
    builtin, pattern = P.match_llm(model_id, P.builtin_llm_profiles())
    fallback = next(p for p in P.builtin_llm_profiles() if p["id"] == "*")
    if builtin is None or builtin["id"] == "*":
        prof = dict(fallback)
        origin = "default"
    else:
        prof = dict(builtin)
        origin = "builtin"
    for key in ("modalities_in", "video", "image", "audio", "context_tokens"):
        sources[key] = origin
    unverified = bool(prof.get("unverified"))

    provider = P.provider_lookup(model_id)
    if provider:
        mods = parse_modalities([m for m in provider.get("input_modalities", []) if m in P.MODALITIES])
        if mods:
            prof["modalities_in"] = mods
            sources["modalities_in"] = "provider"
        if provider.get("context_length"):
            prof["context_tokens"] = int(provider["context_length"])
            sources["context_tokens"] = "provider"

    user_prof, _ = P.match_llm(model_id, P.user_llm_profiles())
    if user_prof:
        override = {k: v for k, v in user_prof.items() if k not in ("id", "aliases")}
        prof = P.deep_merge(prof, override)
        for k in override:
            sources[k] = "config"

    flag_mods = parse_modalities(modalities)
    if flag_mods is not None:
        prof["modalities_in"] = flag_mods
        sources["modalities_in"] = "flag"
    flag_aiv = parse_yes_no(audio_in_video)
    # Warn only about what nobody told us: the user's own profile (config) or flags take responsibility.
    if origin == "default" and sources["modalities_in"] == "default":
        ctx.warn("unknown_model", f"未知模型 {model_id!r}：按只支持图片、采用保守限额处理。",
                 f"vidroute llm set {model_id} --modalities text,image,video  （或传入 --modalities）")
    elif unverified and not user_prof:
        ctx.warn("unverified_profile",
                 f"{model_id!r} 的档案（{prof.get('id')}）未经核实；限额为保守估计。",
                 f"vidroute llm set {model_id} --video-max-seconds N ...")
    elif origin == "builtin" and not user_prof and (age := P.stale_age_days(prof.get("verified_at"))):
        ctx.warn("stale_profile",
                 f"{model_id!r} 的档案（{prof.get('id')}）核实于 {prof['verified_at']}，已过去 {age} 天，模型能力可能已有变化。",
                 f"按官方文档核对后用 vidroute llm set {model_id} ... 覆盖，或升级 vidroute")

    mods = list(prof.get("modalities_in") or ["text"])
    if "video" in mods and not prof.get("video"):
        prof["video"] = dict(CONSERVATIVE_VIDEO)
        sources["video"] = "default"
        ctx.warn("video_limits_unknown", f"{model_id!r} 能接收视频，但限额未知；使用保守限额。",
                 f"vidroute llm set {model_id} --video-max-seconds N --video-max-mb N")
    if "image" in mods and not prof.get("image"):
        prof["image"] = dict(CONSERVATIVE_IMAGE)
        sources["image"] = "default"
    if "audio" in mods and not prof.get("audio"):
        prof["audio"] = dict(CONSERVATIVE_AUDIO)
        sources["audio"] = "default"
    if flag_aiv is not None and prof.get("video"):
        prof["video"] = {**prof["video"], "audio_in_video": flag_aiv}
        sources["video.audio_in_video"] = "flag"
    for drop in ("video", "image", "audio"):
        if drop not in mods:
            prof[drop] = None
    return {
        "model": model_id,
        "matched": prof.get("id") if origin != "default" else "*",
        "matched_pattern": pattern,
        "display_name": prof.get("display_name"),
        "modalities_in": mods,
        "video": prof.get("video"),
        "image": prof.get("image"),
        "audio": prof.get("audio"),
        "context_tokens": int(prof.get("context_tokens") or 128000),
        "verified_at": prof.get("verified_at"),
        "unverified": bool(prof.get("unverified")),
        "evidence": prof.get("evidence") or [],
        "notes": prof.get("notes") or "",
        "sources": sources,
    }


def resolve_host(host_id: str | None, ctx: Ctx | None = None) -> dict[str, Any]:
    ctx = ctx or Ctx()
    source = "flag"
    if not host_id:
        host_id = config.get("default_host")
        source = "config"
    if not host_id:
        detected, var = P.detect_host()
        if detected:
            host_id, source = detected, f"detected:{var}"
            ctx.warn("host_detected", f"根据 ${var} 推测宿主为 {detected!r}；可传入 --host 明确指定。")
    builtin = P.find_host(host_id, P.builtin_host_profiles()) if host_id else None
    if builtin is None:
        if host_id:
            ctx.warn("unknown_host", f"未知宿主 {host_id!r}：假定它能附带图片，但不能附带视频或音频。",
                     f"vidroute host set {host_id} --attach-video yes|no --video-delivery native|none")
        else:
            ctx.warn("host_missing", "未指定宿主：假定为只支持图片的 Agent 宿主。",
                     "传入 --host claude-code|codex-cli|gemini-cli|qwen-code|opencode|...")
        builtin = P.find_host("unknown", P.builtin_host_profiles())
        source = "default"
    host = dict(builtin or {})
    user = P.find_host(host_id, P.user_host_profiles()) if host_id else None
    if user:
        host = P.deep_merge(host, {k: v for k, v in user.items() if k not in ("id", "aliases")})
        source = "config"
    if host.get("confidence") == "unverified" and source != "config":
        ctx.warn("unverified_host", f"宿主档案 {host['id']!r} 未经核实；采用保守假设。",
                 f"vidroute host set {host['id']} ...")
    elif source != "config" and (age := P.stale_age_days(host.get("verified_at"))):
        ctx.warn("stale_host",
                 f"宿主档案 {host['id']!r} 核实于 {host['verified_at']}，已过去 {age} 天，宿主能力可能已有变化。",
                 f"按宿主文档核对后用 vidroute host set {host['id']} ... 覆盖，或升级 vidroute")
    host["requested"] = host_id
    host["source"] = source
    return host


def effective_capabilities(llm: dict[str, Any], host: dict[str, Any]) -> dict[str, Any]:
    mods = set(llm["modalities_in"])
    blocked: list[dict[str, str]] = []

    image = "image" in mods and bool(host.get("can_attach_image"))
    if "image" in mods and not image:
        blocked.append({"modality": "image", "why": f"宿主 {host['id']} 不能附带图片"})

    delivery = host.get("video_delivery", "none")
    video_ok_host = bool(host.get("can_attach_video")) and delivery == "native"
    video = "video" in mods and video_ok_host
    if "video" in mods and not video:
        why = {"frame-sampled": "会自己把视频转成帧", "preprocessed": "会自己把视频转成文本"}.get(
            delivery, "不能附带视频文件")
        blocked.append({"modality": "video", "why": f"宿主 {host['id']} {why}（受宿主限制，而非模型限制）"})
    audio_in_video = bool(video and (llm.get("video") or {}).get("audio_in_video") and host.get("video_includes_audio"))

    audio = "audio" in mods and bool(host.get("can_attach_audio"))
    if "audio" in mods and not audio:
        blocked.append({"modality": "audio", "why": f"宿主 {host['id']} 不能附带音频"})

    host_mb = host.get("max_attachment_mb")
    inline = host.get("delivery", "inline") == "inline"

    def file_limit(spec: dict[str, Any] | None) -> float | None:
        if not spec:
            return host_mb
        llm_mb = raw_limit_mb(spec.get("inline_max_mb"), bool(spec.get("inline_is_base64"))) if inline \
            else spec.get("upload_max_mb")
        vals = [v for v in (host_mb, llm_mb) if v]
        return min(vals) if vals else None

    v = llm.get("video") or {}
    i = llm.get("image") or {}
    a = llm.get("audio") or {}
    max_images = [x for x in (i.get("max_images"), host.get("max_images_per_turn")) if x]
    return {
        "text": True,
        "image": image,
        "video": video,
        "audio_in_video": audio_in_video,
        "audio": audio,
        "video_limits": {
            "max_seconds": v.get("max_seconds"),
            "max_file_mb": file_limit(v),
            "max_files": v.get("max_files"),
            "fps": v.get("fps"),
            "tokens_per_second": v.get("tokens_per_second"),
            "max_video_tokens": v.get("max_video_tokens"),
        } if video else None,
        "image_limits": {
            "max_images": min(max_images) if max_images else None,
            "max_file_mb": file_limit(i),
            "tokens_per_image": i.get("tokens_per_image"),
            "patch_px": i.get("patch_px"),
            "max_side_px": i.get("max_side_px"),
            "dense_threshold": i.get("dense_threshold"),
            "dense_max_side_px": i.get("dense_max_side_px"),
        } if image else None,
        "audio_limits": {
            "max_seconds": a.get("max_seconds"),
            "max_file_mb": file_limit(a),
            "tokens_per_second": a.get("tokens_per_second"),
        } if audio else None,
        "context_tokens": llm["context_tokens"],
        "blocked_by_host": blocked,
        "llm_could_take_video": "video" in mods,
    }


def resolve(
    llm_id: str | None,
    host_id: str | None = None,
    modalities: str | list[str] | None = None,
    audio_in_video: str | bool | None = None,
    ctx: Ctx | None = None,
) -> dict[str, Any]:
    ctx = ctx or Ctx()
    llm = resolve_llm(llm_id, modalities, audio_in_video, ctx)
    host = resolve_host(host_id, ctx)
    eff = effective_capabilities(llm, host)
    for b in eff["blocked_by_host"]:
        if b["modality"] == "video":
            ctx.warn("host_blocks_video", f"{llm['model']} 能理解视频，但{b['why']}。",
                     "vidroute plan ...（将使用 D3 关键帧 + 转写）")
    return {"llm": llm, "host": host, "effective": eff}
