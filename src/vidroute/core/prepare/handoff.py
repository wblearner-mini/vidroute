"""The agent-facing files of an output: handoff.json, SUMMARY.md, ANALYSIS_GUIDE.md, manifest.json."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from vidroute import __version__
from vidroute.core.install.manifest import Manifest
from vidroute.core.plan.route import image_tokens
from vidroute.core.prepare.layout import Layout
from vidroute.core.resources import data_file
from vidroute.core.util import ffmpeg
from vidroute.core.util.hashing import sha256_file
from vidroute.core.util.jsonio import write_json_atomic
from vidroute.core.util.timefmt import fmt_ts

HANDOFF_SCHEMA = "vidroute/handoff/1"
MANIFEST_SCHEMA = "vidroute/output-manifest/1"

ROUTE_LABELS = {
    "native-av": "the video itself, with its audio track, is attached",
    "native-v": "the video is attached without audio; the local transcript is the only record of speech",
    "frames": "keyframes (as contact sheets) + local transcript, + on-screen text when useful",
    "text": "text only: local transcript and on-screen text, merged by time",
    "delegate": "a video-capable model is called on your behalf and returns text",
}

_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯豈-﫿]")


def text_tokens(text: str) -> int:
    """Rough input-token estimate: ~1 token per CJK character, ~4 characters per token otherwise."""
    cjk = len(_CJK.findall(text))
    return int(cjk + (len(text) - cjk) / 4.0) + 1


def _file_tokens(path: Path) -> int:
    try:
        return text_tokens(path.read_text(encoding="utf-8"))
    except OSError:
        return 0


def _jpeg_size(path: Path) -> tuple[int, int] | None:
    """Width/height from a JPEG's SOF marker (no imaging library needed)."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        length = int.from_bytes(data[i + 2:i + 4], "big")
        if marker in (0xC0, 0xC1, 0xC2):
            return int.from_bytes(data[i + 7:i + 9], "big"), int.from_bytes(data[i + 5:i + 7], "big")
        i += 2 + length
    return None


# ------------------------------------------------------------------ handoff


def build_handoff(layout: Layout, plan: dict[str, Any], outcome: dict[str, Any], warnings: list[dict[str, Any]]) -> dict[str, Any]:
    route = outcome["route"]
    lim = (plan.get("effective") or {}).get("image_limits") or {}
    attachments: list[dict[str, Any]] = []
    images = 0
    tokens = 0
    for sheet in outcome.get("sheets") or []:
        p = layout.sheets / sheet["file"]
        size = _jpeg_size(p) or (1600, 700)
        est = image_tokens(size[0], size[1], lim)
        attachments.append({"type": "image", "path": layout.rel(p), "bytes": p.stat().st_size, "est_tokens": est,
                            "note": f"contact sheet {sheet['file']}: {len(sheet['frames'])} keyframes "
                                    f"{fmt_ts(sheet['start'])}–{fmt_ts(sheet['end'])}, tiles labelled with global time"})
        images += 1
        tokens += est
    video_seconds = 0.0
    parts = outcome.get("native_parts") or []
    for part in parts:
        span = f"{fmt_ts(part['start'])}–{fmt_ts(part['end'])}"
        note = (f"the video ({'with' if outcome.get('includes_audio') else 'without'} audio), global {span}"
                if len(parts) == 1 else
                f"part {part['id']}/{len(parts)}: global {span}; add {fmt_ts(part['start'])} to times inside "
                f"this part (see segments.json)")
        attachments.append({"type": "video", "path": part["file"], "bytes": part["bytes"],
                            "est_tokens": part.get("est_tokens", 0), "note": note,
                            "segment": {"id": part["id"], "start": part["start"], "end": part["end"],
                                        "offset_sec": part["start"]}})
        video_seconds += part["end"] - part["start"]
        tokens += part.get("est_tokens", 0)
    audio_seconds = 0.0
    for part in outcome.get("audio_parts") or []:
        p = layout.root / part["file"]
        attachments.append({"type": "audio", "path": layout.rel(p), "bytes": p.stat().st_size,
                            "est_tokens": part.get("est_tokens", 0),
                            "note": f"audio track ({part['kbps']} kbps mono), global {fmt_ts(part['start'])}–"
                                    f"{fmt_ts(part['end'])}",
                            "segment": {"id": part["id"], "start": part["start"], "end": part["end"],
                                        "offset_sec": part["start"]}})
        audio_seconds += part["end"] - part["start"]
        tokens += part.get("est_tokens", 0)
    role = outcome.get("transcript_role", "none")
    text_context: list[dict[str, Any]] = []
    has_t = layout.file("transcript.md").exists()
    has_o = layout.file("ocr.md").exists()
    has_tl = layout.file("timeline.md").exists()
    if route.startswith("native"):
        if has_t:
            text_context.append({"type": "transcript", "path": "transcript.md", "role": role if role != "none"
                                 else "reference", "attach": bool(outcome.get("transcript_attach"))})
    elif route == "text":
        if has_tl:
            text_context.append({"type": "timeline", "path": "timeline.md",
                                 "role": "sole_source" if role == "sole_source" else "reference", "attach": True,
                                 "note": "transcript and on-screen text merged by time (read this one)"})
        if has_t:
            text_context.append({"type": "transcript", "path": "transcript.md", "role": role if role != "none"
                                 else "reference", "attach": not has_tl, "note": "same speech as in timeline.md"})
        if has_o:
            text_context.append({"type": "ocr", "path": "ocr.md", "role": "reference", "attach": False,
                                 "note": "same on-screen text as in timeline.md"})
    else:
        if has_t:
            text_context.append({"type": "transcript", "path": "transcript.md",
                                 "role": role if role != "none" else "reference", "attach": True})
        if has_o:
            text_context.append({"type": "ocr", "path": "ocr.md", "role": "reference", "attach": True})
        if has_tl:
            text_context.append({"type": "timeline", "path": "timeline.md", "role": "navigation", "attach": False,
                                 "note": "aligns pictures, on-screen text and speech; read when you need to cross-check"})
        if outcome.get("sheets"):
            text_context.append({"type": "contact_sheet_index", "path": "contact-sheets/index.md",
                                 "role": "navigation", "attach": False, "note": "tile -> time -> frame file"})
    for item in text_context:
        item["est_tokens"] = _file_tokens(layout.root / item["path"])
        if item["attach"]:
            tokens += item["est_tokens"]
    tokens += _file_tokens(layout.file("ANALYSIS_GUIDE.md")) if layout.file("ANALYSIS_GUIDE.md").exists() else 1200
    on_demand = [{"type": "image", "path": f"frames/{k['file']}", "t": k["t"], "span": [k["start"], k["end"]]}
                 for k in outcome.get("frames") or [] if k.get("file")]
    budget_tokens = int(plan["budget"]["budget_tokens"])
    video = plan["video"]
    return {
        "schema": HANDOFF_SCHEMA,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "vidroute_version": __version__,
        "out_dir": str(layout.root),
        "video": {"name": video.get("name"), "duration": float(video.get("duration") or 0)},
        "llm": plan["llm"]["model"],
        "host": plan["host"]["id"],
        "task": plan["request"]["task"],
        "route": route,
        "reasons": list(plan["delivery"]["reasons"]) or [f"route {route}"],
        "degraded_from": list(plan["delivery"]["degraded_from"]) + list(outcome.get("degraded_from") or []),
        "uploads_raw_video": route.startswith("native"),
        "uploads_raw_audio": (route == "native-av" and bool(outcome.get("includes_audio")))
                             or bool(outcome.get("audio_parts")),
        "attachments": attachments,
        "text_context": text_context,
        "on_demand": on_demand,
        "guide": "ANALYSIS_GUIDE.md",
        "summary": "SUMMARY.md",
        "transcript_role": role,
        "budget": {"est_input_tokens": int(tokens), "images": images, "video_seconds": round(video_seconds, 1),
                   "audio_seconds": round(audio_seconds, 1), "budget_tokens": budget_tokens,
                   "over_budget": tokens > budget_tokens},
        "needs_confirmation": [n for n in plan.get("needs_confirmation") or [] if n.get("code") != "raw_video_handoff"],
        "warnings": [{"code": w["code"], "message": w["message"]} for w in warnings],
        "drill": drill_cmd(layout, float(video.get("duration") or 0)),
    }


def drill_cmd(layout: Layout, duration: float) -> str:
    """An example drill command with a range inside this video."""
    if duration >= 150:
        start, end = 80.0, 120.0
    else:
        start = duration * 0.4
        end = min(duration, start + max(2.0, duration * 0.2))
    return f'vidroute drill "{layout.root}" --range {fmt_ts(start)}-{fmt_ts(end)}'


# ------------------------------------------------------------- the guide


_IF = re.compile(r"<!-- if:(?P<cond>[^>]+?) -->\n?(?P<body>.*?)<!-- endif -->\n?", re.S)
_VAR = re.compile(r"\{\{(\w+)\}\}")


def render_template(text: str, values: dict[str, str], flags: dict[str, bool], route: str) -> str:
    text = re.sub(r"\A<!--.*?-->\n", "", text, count=1, flags=re.S)  # template header comment

    def keep(cond: str) -> bool:
        cond = cond.strip()
        neg = cond.startswith("!")
        cond = cond.lstrip("!")
        if cond.startswith("route="):
            ok = route in [r.strip() for r in cond[len("route="):].split(",")]
        elif cond.startswith("has:"):
            ok = bool(flags.get(cond[4:]))
        else:
            ok = False
        return ok != neg

    text = _IF.sub(lambda m: m.group("body") if keep(m.group("cond")) else "", text)
    text = _VAR.sub(lambda m: values.get(m.group(1), ""), text)
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


def reading_order(route: str, layout: Layout, outcome: dict[str, Any], guide_note: str = "this guide") -> list[str]:
    steps = [f"`ANALYSIS_GUIDE.md` ({guide_note})"]
    sheets = outcome.get("sheets") or []
    role = outcome.get("transcript_role", "none")
    t_role = {"sole_source": "the only record of speech", "reference": "reference for what you hear"}.get(role, "")
    if route.startswith("native"):
        parts = outcome.get("native_parts") or []
        if len(parts) == 1:
            steps.append(f"the video `{parts[0]['file']}`")
        elif parts:
            steps.append(f"the video parts `{parts[0]['file']}` … `{parts[-1]['file']}` ({len(parts)} parts, "
                         "in order; `segments.json` maps their times to global time)")
        if layout.file("transcript.md").exists():
            steps.append("`transcript.md`" + (f" — {t_role}" if t_role else "")
                         + ("" if outcome.get("transcript_attach") else " (only if you need exact wording)"))
    elif route == "text":
        if layout.file("timeline.md").exists():
            steps.append("`timeline.md` — transcript and on-screen text merged by time")
    else:
        if sheets:
            steps.append(f"the contact sheets `contact-sheets/{sheets[0]['file']}`"
                         + (f" … `{sheets[-1]['file']}`" if len(sheets) > 1 else "")
                         + f" ({len(sheets)} image(s); index: `contact-sheets/index.md`)")
        for part in outcome.get("audio_parts") or []:
            steps.append(f"the audio `{part['file']}`")
        if layout.file("transcript.md").exists():
            steps.append("`transcript.md`" + (f" — {t_role}" if t_role else ""))
        if layout.file("ocr.md").exists():
            steps.append("`ocr.md` — on-screen text")
        if layout.file("timeline.md").exists():
            steps.append("`timeline.md` when you need to line up pictures and speech (optional)")
    return steps


def write_guide(layout: Layout, plan: dict[str, Any], outcome: dict[str, Any]) -> Path:
    route = outcome["route"]
    tmpl = data_file("references/analysis-guide.template.md").read_text(encoding="utf-8")
    hears = (route == "native-av" and bool(outcome.get("includes_audio", True))) or bool(outcome.get("audio_parts"))
    flags = {"transcript": layout.file("transcript.md").exists(), "ocr": layout.file("ocr.md").exists(),
             "hearing": hears,
             "frames": bool(outcome.get("frames")), "sheets": bool(outcome.get("sheets")),
             "audio_attachment": bool(outcome.get("audio_parts"))}
    evidence = {"frames": " (keyframes, contact sheets, `ocr.md`)",
                "text": " (`ocr.md` and the **On screen** lines of `timeline.md`)",
                "native-v": " (in the video itself)"}.get(route, "")
    order = reading_order(route, layout, outcome)
    privacy = ("- The raw video was handed to the LLM provider with the user's consent; do not copy it elsewhere."
               if route.startswith("native") else
               "- The raw video stayed on this machine; only the files listed in `handoff.json` are attached.")
    values = {
        "video_name": str(plan["video"].get("name") or ""),
        "route": route,
        "route_label": ROUTE_LABELS.get(route, route),
        "duration": fmt_ts(float(plan["video"].get("duration") or 0)),
        "can_hear": " and hear" if hears else "",
        "hearing_source": " (it is in the video)" if route == "native-av" else " (attached as a separate audio file)",
        "can_hear_short": " or the audio" if hears else "",
        "evidence_sources": evidence,
        "no_transcript_reason": outcome.get("transcript_skip_reason") or "no speech was transcribed",
        "reading_order": "Reading order:\n\n" + "\n".join(f"{i}. {s}" for i, s in enumerate(order, 1)) + "\n",
        "drill_cmd": drill_cmd(layout, float(plan["video"].get("duration") or 0)),
        "privacy_note": privacy,
    }
    path = layout.file("ANALYSIS_GUIDE.md")
    path.write_text(render_template(tmpl, values, flags, route), encoding="utf-8")
    return path


# ------------------------------------------------------------------ summary


def write_summary(layout: Layout, plan: dict[str, Any], outcome: dict[str, Any], handoff: dict[str, Any],
                  warnings: list[dict[str, Any]]) -> Path:
    route = outcome["route"]
    video = plan["video"]
    lines = [f"# vidroute — {video.get('name')}", "",
             f"**Route: `{route}`** — {ROUTE_LABELS.get(route, route)}.", "",
             f"- LLM `{plan['llm']['model']}` on host `{plan['host']['id']}` · local tier `{plan['local']['tier']}` · "
             f"task `{plan['request']['task']}`",
             f"- Video: {fmt_ts(float(video.get('duration') or 0))}, "
             + ("no audio track" if not video.get("has_audio") else f"speech ratio ~{video.get('speech_ratio')}")
             + f", text density {video.get('text_density')}",
             f"- **Uploads raw video: {'YES' if handoff['uploads_raw_video'] else 'no'}** · uploads raw audio: "
             f"{'yes' if handoff['uploads_raw_audio'] else 'no'}", ""]
    lines += ["## Why this route", ""]
    lines += [f"- {r}" for r in handoff["reasons"]]
    for g in handoff["degraded_from"]:
        lines.append(f"- degraded from `{g['route']}`: {g['why']}")
    for s in plan["delivery"].get("suggestions") or []:
        lines.append(f"- suggestion: {s['why']} {s['how']}")
    lines += ["", "## Read in this order", ""]
    lines += [f"{i}. {s}" for i, s in enumerate(reading_order(route, layout, outcome, "rules for this route"), 1)]
    lines += ["", "Attach only what `handoff.json` marks for attaching; open single frames from `frames/` or drill "
              "into a time range when you need detail:", "", f"    {handoff['drill']}", ""]
    lines += ["## Outputs", "", "| step | status | details |", "|---|---|---|"]
    for st in outcome["steps"]:
        lines.append(f"| {st['id']} | {st['status']} | {st['detail']} |")
    b = handoff["budget"]
    lines += ["", "## Budget", "",
              f"~{b['est_input_tokens']:,} input tokens if you attach what is marked "
              f"({b['images']} image(s)) — budget {b['budget_tokens']:,}"
              + (" — **over budget: ask the user before attaching everything**" if b["over_budget"] else "") + "."]
    for n in handoff["needs_confirmation"]:
        lines.append(f"- needs confirmation: {n['message']}")
    if warnings:
        lines += ["", "## Warnings", ""]
        lines += [f"- {w['message']}" + (f" (try: `{w['hint']}`)" if w.get("hint") else "") for w in warnings]
    lines += ["", "---", f"vidroute {__version__} · {handoff['created_at']} · all timestamps are global video time."]
    path = layout.file("SUMMARY.md")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ----------------------------------------------------------------- manifest


def write_manifest(layout: Layout, plan: dict[str, Any], outcome: dict[str, Any], manifest: Manifest) -> Path:
    files = []
    for p in sorted(layout.root.rglob("*")):
        rel = p.relative_to(layout.root)
        if not p.is_file() or rel.parts[0] in (".work", "drill") or rel.as_posix() == "manifest.json":
            continue
        files.append({"path": rel.as_posix(), "bytes": p.stat().st_size, "sha256": sha256_file(p)})
    engines = []
    for capability, model_id in sorted((outcome.get("engines") or {}).items()):
        if not model_id:
            continue
        entry: dict[str, Any] = {"capability": capability, "id": model_id}
        if outcome.get("fake_engines") and capability in ("asr", "ocr", "vad"):
            entry["fake"] = True
        if model_id in manifest.models:
            m = manifest.models[model_id]
            entry.update(license=m.license.spdx, license_name=m.license.name, license_url=m.license.url)
        engines.append(entry)
    info = ffmpeg.require()
    data = {
        "schema": MANIFEST_SCHEMA,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "vidroute_version": __version__,
        "video": {k: plan["video"].get(k) for k in ("name", "duration", "size_mb")},
        "route": outcome["route"],
        "engines": engines,
        "tools": {"ffmpeg": info.version if info.found else None},
        "timings_seconds": outcome.get("timings") or {},
        "files": files,
        "notice": "Local models are used under their own licenses (see engines[].license and THIRD_PARTY_LICENSES.md). "
                  "Machine outputs (transcript, OCR) may contain errors.",
    }
    path = layout.file("manifest.json")
    write_json_atomic(path, data)
    return path
