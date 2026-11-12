"""User-maintained profile overrides (``llm set`` / ``host set``) and the opt-in provider sync.

Profiles go stale quickly (new model names every month), so overriding must be
cheap: one command writes a small entry into config.json that wins over the
bundled profile. ``llm sync`` is an explicit, user-triggered download of a
public model list (no user data is sent).
"""

from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone
from typing import Any

from vidroute import __version__
from vidroute.core import config
from vidroute.core.errors import RunFailed, UsageError
from vidroute.core.llm import profiles as P
from vidroute.core.llm.resolve import parse_modalities, parse_yes_no
from vidroute.core.util.jsonio import write_json_atomic

OPENROUTER_MODELS = "https://openrouter.ai/api/v1/models"
VIDEO_DELIVERIES = ("native", "frame-sampled", "preprocessed", "none")


def _upsert(key: str, entry: dict[str, Any]) -> list[dict[str, Any]]:
    items = [e for e in (config.get(key) or []) if isinstance(e, dict)]
    for i, e in enumerate(items):
        if e.get("id") == entry["id"]:
            items[i] = P.deep_merge(e, entry)
            break
    else:
        items.append(entry)
    config.set_value(key, items)
    return items


def llm_set(model_id: str, **opts: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {"id": model_id.strip().lower(), "source": "user"}
    mods = parse_modalities(opts.get("modalities"))
    if mods:
        entry["modalities_in"] = mods
    video: dict[str, Any] = {}
    if opts.get("audio_in_video") is not None:
        video["audio_in_video"] = parse_yes_no(opts["audio_in_video"])
    if opts.get("video_max_seconds"):
        video["max_seconds"] = float(opts["video_max_seconds"])
    if opts.get("video_max_mb"):
        video["inline_max_mb"] = float(opts["video_max_mb"])
        video["inline_is_base64"] = False
    if opts.get("video_tokens_per_second"):
        video["tokens_per_second"] = float(opts["video_tokens_per_second"])
    if video:
        video.setdefault("delivery", ["inline_base64"])
        entry["video"] = video
    if opts.get("max_images"):
        entry["image"] = {"max_images": int(opts["max_images"])}
    if opts.get("context_tokens"):
        entry["context_tokens"] = int(opts["context_tokens"])
    if opts.get("notes"):
        entry["notes"] = str(opts["notes"])
    if len(entry) == 2:
        raise UsageError("没有要设置的内容；请传入例如 --modalities text,image,video --video-max-seconds 600")
    entry["updated_at"] = datetime.now(timezone.utc).date().isoformat()
    _upsert("llm_profiles", entry)
    return entry


def host_set(host_id: str, **opts: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {"id": host_id.strip().lower()}
    for flag, key in (("attach_image", "can_attach_image"), ("attach_video", "can_attach_video"),
                      ("attach_audio", "can_attach_audio"), ("video_includes_audio", "video_includes_audio")):
        if opts.get(flag) is not None:
            entry[key] = parse_yes_no(opts[flag])
    if opts.get("video_delivery"):
        if opts["video_delivery"] not in VIDEO_DELIVERIES:
            raise UsageError(f"--video-delivery 必须是 {', '.join(VIDEO_DELIVERIES)} 之一")
        entry["video_delivery"] = opts["video_delivery"]
        if opts["video_delivery"] == "native":
            entry.setdefault("can_attach_video", True)
    if opts.get("max_attachment_mb"):
        entry["max_attachment_mb"] = float(opts["max_attachment_mb"])
    if opts.get("notes"):
        entry["notes"] = str(opts["notes"])
    if len(entry) == 1:
        raise UsageError("没有要设置的内容；请传入例如 --attach-video yes --video-delivery native")
    entry["confidence"] = "user"
    entry["updated_at"] = datetime.now(timezone.utc).date().isoformat()
    _upsert("host_profiles", entry)
    return entry


def unset(key: str, ident: str) -> bool:
    items = [e for e in (config.get(key) or []) if isinstance(e, dict)]
    kept = [e for e in items if e.get("id") != ident.strip().lower()]
    config.set_value(key, kept)
    return len(kept) != len(items)


def llm_list() -> dict[str, Any]:
    builtin = [{"id": p["id"], "aliases": p.get("aliases", []), "modalities_in": p["modalities_in"],
                "max_video_seconds": (p.get("video") or {}).get("max_seconds"),
                "context_tokens": p.get("context_tokens"), "verified_at": p.get("verified_at"),
                "unverified": bool(p.get("unverified"))} for p in P.builtin_llm_profiles()]
    return {"builtin": builtin, "user": P.user_llm_profiles()}


def host_list() -> dict[str, Any]:
    builtin = [{"id": h["id"], "aliases": h.get("aliases", []), "image": h["can_attach_image"],
                "video": h["can_attach_video"], "video_delivery": h["video_delivery"], "audio": h["can_attach_audio"],
                "confidence": h["confidence"], "verified_at": h.get("verified_at")} for h in P.builtin_host_profiles()]
    return {"builtin": builtin, "user": P.user_host_profiles()}


def sync_openrouter(timeout: float = 30.0) -> dict[str, Any]:
    """Explicit, user-triggered download of OpenRouter's public model list (modalities + context only)."""
    req = urllib.request.Request(OPENROUTER_MODELS, headers={"User-Agent": f"vidroute/{__version__}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise RunFailed(f"无法获取 {OPENROUTER_MODELS}：{exc}", code="provider_sync_failed") from exc
    index: dict[str, Any] = {}
    for m in data.get("data", []):
        arch = m.get("architecture") or {}
        rec = {"input_modalities": arch.get("input_modalities") or [], "context_length": m.get("context_length")}
        for cand in P.normalize_model_id(m.get("id", "")):
            index.setdefault(cand, rec)
    payload = {"provider": "openrouter", "source": OPENROUTER_MODELS,
               "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "models": index}
    write_json_atomic(P.provider_cache_path(), payload)
    return {"provider": "openrouter", "models": len(data.get("data", [])), "path": str(P.provider_cache_path())}
