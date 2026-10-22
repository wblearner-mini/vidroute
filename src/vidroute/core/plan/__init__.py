"""``vidroute plan``: llm resolve + probe + doctor + rules -> an explainable two-layer plan.

The plan is pure data (``plan.json``, schema ``vidroute/plan/1``): the route and
why, every degradation, each local step with reason / alternatives / estimated
seconds / estimated input tokens / whether it uploads raw video, and what needs
the user's confirmation or an install.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from vidroute import __version__
from vidroute.core import config
from vidroute.core.context import Ctx
from vidroute.core.errors import UsageError
from vidroute.core.install.manifest import load_manifest
from vidroute.core.llm.resolve import resolve
from vidroute.core.plan import local, route
from vidroute.core.plan.rules import budget_tokens, load_rules
from vidroute.core.probe import probe as run_probe

TASKS = ("minutes", "summary", "qa", "transcript")
PREFERS = ("quality", "privacy", "cost")
ROUTES = ("auto", "native-av", "native-v", "frames", "text")
OCR_MODES = ("auto", "on", "off")
PLAN_SCHEMA = "vidroute/plan/1"


def _check(name: str, value: str, allowed: tuple[str, ...]) -> str:
    if value not in allowed:
        raise UsageError(f"--{name} must be one of {', '.join(allowed)} (got {value!r})")
    return value


def make_plan(
    video: str | Path,
    llm: str | None,
    host: str | None = None,
    task: str = "summary",
    prefer: str = "quality",
    language: str = "auto",
    modalities: str | list[str] | None = None,
    audio_in_video: str | bool | None = None,
    ocr: str = "auto",
    route_override: str = "auto",
    force_asr: bool = False,
    ctx: Ctx | None = None,
) -> dict[str, Any]:
    ctx = ctx or Ctx()
    _check("task", task, TASKS)
    _check("prefer", prefer, PREFERS)
    _check("ocr", ocr, OCR_MODES)
    _check("route", route_override, ROUTES)
    language = (language or "auto").lower()
    rules = load_rules()
    manifest = load_manifest()
    resolution = resolve(llm, host, modalities, audio_in_video, ctx)
    info = run_probe(video, ctx)
    from vidroute.core import doctor

    tier = doctor.run(refresh=False, offline=True, ctx=Ctx())["tier"]
    budget = budget_tokens(resolution["effective"]["context_tokens"], rules)
    density = float(info.get("text_density") or 0)
    ocr_frames = 0
    if ocr == "on" or (ocr == "auto" and density >= rules["local"]["ocr_text_density_threshold"]):
        ocr_frames = min(rules["frames"]["max_frames"], int(float(info["duration"] or 0) // 4) + 1)
    decision = route.decide(info, resolution, rules, task, prefer, bool(config.get("allow_raw_video_handoff")),
                            budget, ocr_frames=ocr_frames, forced=route_override)
    steps, needs = local.plan_steps(info, decision, tier, rules, manifest, task, language, ocr, force_asr)
    for n in needs:
        if n.get("optional"):
            ctx.warn("optional_install", f"{n['capability']}: {n['why']}", n["hint"])
        else:
            ctx.warn("needs_install", f"{n['capability']}: {n['why']}", n["hint"])
    if decision["uploads_raw_video"]:
        ctx.warn("raw_video_handoff", "This route hands the raw video to the LLM provider: ask the user first.",
                 "vidroute prepare ... --accept-raw-video-handoff")
    if not info.get("has_audio"):
        ctx.warn("no_audio", "The video has no audio track.")
    elif float(info.get("speech_ratio") or 0) < rules["local"]["speech_ratio_skip_asr"]:
        ctx.warn("no_speech", "No speech detected in the sampled audio.")
    est_seconds = sum(s["est_seconds"] for s in steps)
    return {
        "schema": PLAN_SCHEMA,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "vidroute_version": __version__,
        "request": {"video": str(Path(video).expanduser().resolve()), "llm": resolution["llm"]["model"],
                    "host": resolution["host"]["id"], "task": task, "prefer": prefer, "language": language,
                    "ocr": ocr, "route": route_override, "force_asr": force_asr},
        "video": {k: info.get(k) for k in ("path", "name", "duration", "size_mb", "format", "video", "has_audio",
                                           "speech_ratio", "text_density", "visual_change", "text_subtitles",
                                           "probe_seconds")},
        "llm": {k: resolution["llm"][k] for k in ("model", "matched", "modalities_in", "context_tokens", "unverified",
                                                  "verified_at", "sources")},
        "host": {k: resolution["host"].get(k) for k in ("id", "requested", "source", "can_attach_image",
                                                         "can_attach_video", "can_attach_audio", "video_delivery",
                                                         "max_attachment_mb", "confidence")},
        "effective": resolution["effective"],
        "local": {"tier": tier},
        "route": decision["route"],
        "delivery": decision,
        "steps": steps,
        "budget": decision["budget"],
        "uploads_raw_video": decision["uploads_raw_video"],
        "needs_confirmation": decision["needs_confirmation"],
        "needs_install": needs,
        "est_seconds": round(est_seconds, 1),
    }


def summarize(plan: dict[str, Any]) -> list[str]:
    """Short human explanation of a plan (also used in SUMMARY.md)."""
    d = plan["delivery"]
    lines = [f"Route: {plan['route']}  (LLM {plan['llm']['model']} on host {plan['host']['id']}, tier {plan['local']['tier']})"]
    lines += [f"  - {r}" for r in d["reasons"]]
    for g in d["degraded_from"]:
        lines.append(f"  - degraded from {g['route']}: {g['why']}")
    for s in d.get("suggestions") or []:
        lines.append(f"  - suggestion ({s['route']}): {s['why']} {s['how']}")
    lines.append(f"  - uploads raw video: {'YES' if plan['uploads_raw_video'] else 'no'}; "
                 f"estimated input ~{plan['budget']['est_input_tokens']:,} tokens (budget {plan['budget']['budget_tokens']:,})")
    return lines
