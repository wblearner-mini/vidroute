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
        raise UsageError(f"Unknown modality {sorted(bad)}; use text,image,audio,video")
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
    raise UsageError(f"Expected yes or no, got {raw!r}")


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
        raise UsageError("Which LLM will read the result? Pass --llm <model-id> (e.g. claude-opus-5-5, gemini-3.8-flash).",
                         hint="vidroute llm resolve --llm <model-id>")
    sources: dict[str, str] = {}
    builtin, pattern = P.match_llm(model_id, P.builtin_llm_profiles())
    fallback = next(p for p in P.builtin_llm_profiles() if p["id"] == "*")
    if builtin is None or builtin["id"] == "*":
        prof = dict(fallback)
        origin = "default"
        ctx.warn("unknown_model", f"Unknown model {model_id!r}: treated as image-only with conservative limits.",
                 f"vidroute llm set {model_id} --modalities text,image,video  (or pass --modalities)")
    else:
        prof = dict(builtin)
        origin = "builtin"
    for key in ("modalities_in", "video", "image", "audio", "context_tokens"):
        sources[key] = origin
    if prof.get("unverified"):
        ctx.warn("unverified_profile",
                 f"The profile for {model_id!r} ({prof['id']}) is unverified; limits are conservative guesses.",
                 f"vidroute llm set {model_id} --video-max-seconds N ...")

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

    mods = list(prof.get("modalities_in") or ["text"])
    if "video" in mods and not prof.get("video"):
        prof["video"] = dict(CONSERVATIVE_VIDEO)
        sources["video"] = "default"
        ctx.warn("video_limits_unknown", f"{model_id!r} takes video but its limits are unknown; using conservative limits.",
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
            ctx.warn("host_detected", f"Host guessed as {detected!r} from ${var}; pass --host to be explicit.")
    builtin = P.find_host(host_id, P.builtin_host_profiles()) if host_id else None
    if builtin is None:
        if host_id:
            ctx.warn("unknown_host", f"Unknown host {host_id!r}: assuming it can attach images but not video or audio.",
                     f"vidroute host set {host_id} --attach-video yes|no --video-delivery native|none")
        else:
            ctx.warn("host_missing", "No host given: assuming an image-only agent host.",
                     "pass --host claude-code|codex-cli|gemini-cli|qwen-code|opencode|...")
        builtin = P.find_host("unknown", P.builtin_host_profiles())
        source = "default"
    host = dict(builtin or {})
    user = P.find_host(host_id, P.user_host_profiles()) if host_id else None
    if user:
        host = P.deep_merge(host, {k: v for k, v in user.items() if k not in ("id", "aliases")})
        source = "config"
    if host.get("confidence") == "unverified" and source != "config":
        ctx.warn("unverified_host", f"Host profile {host['id']!r} is unverified; conservative assumptions apply.",
                 f"vidroute host set {host['id']} ...")
    host["requested"] = host_id
    host["source"] = source
    return host


def effective_capabilities(llm: dict[str, Any], host: dict[str, Any]) -> dict[str, Any]:
    mods = set(llm["modalities_in"])
    blocked: list[dict[str, str]] = []

    image = "image" in mods and bool(host.get("can_attach_image"))
    if "image" in mods and not image:
        blocked.append({"modality": "image", "why": f"host {host['id']} cannot attach images"})

    delivery = host.get("video_delivery", "none")
    video_ok_host = bool(host.get("can_attach_video")) and delivery == "native"
    video = "video" in mods and video_ok_host
    if "video" in mods and not video:
        why = {"frame-sampled": "turns video into frames itself", "preprocessed": "converts video to text itself"}.get(
            delivery, "cannot attach video files")
        blocked.append({"modality": "video", "why": f"host {host['id']} {why} (limited by the host, not the model)"})
    audio_in_video = bool(video and (llm.get("video") or {}).get("audio_in_video") and host.get("video_includes_audio"))

    audio = "audio" in mods and bool(host.get("can_attach_audio"))
    if "audio" in mods and not audio:
        blocked.append({"modality": "audio", "why": f"host {host['id']} cannot attach audio"})

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
            ctx.warn("host_blocks_video", f"{llm['model']} understands video, but {b['why']}.",
                     "vidroute plan ... (D3 keyframes + transcript will be used)")
    return {"llm": llm, "host": host, "effective": eff}
