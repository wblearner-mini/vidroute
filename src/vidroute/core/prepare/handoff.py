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
    "native-av": "附带视频本身（含音轨）",
    "native-v": "附带不含音频的视频；本地转写是唯一的语音记录",
    "frames": "关键帧（拼图形式）+ 本地转写，必要时附屏幕文字",
    "text": "只有文本：按时间合并的本地转写和屏幕文字",
    "delegate": "代你调用一个能看视频的模型，并返回文本",
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
                            "note": f"拼图 {sheet['file']}：{len(sheet['frames'])} 张关键帧，"
                                    f"{fmt_ts(sheet['start'])}–{fmt_ts(sheet['end'])}，小图标有全局时间"})
        images += 1
        tokens += est
    video_seconds = 0.0
    parts = outcome.get("native_parts") or []
    for part in parts:
        span = f"{fmt_ts(part['start'])}–{fmt_ts(part['end'])}"
        note = (f"视频（{'含' if outcome.get('includes_audio') else '不含'}音频），全局 {span}"
                if len(parts) == 1 else
                f"第 {part['id']}/{len(parts)} 部分：全局 {span}；本部分内的时间加上 {fmt_ts(part['start'])} "
                f"即为全局时间（见 segments.json）")
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
                            "note": f"音轨（{part['kbps']} kbps 单声道），全局 {fmt_ts(part['start'])}–"
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
                                 "note": "按时间合并的转写和屏幕文字（读这个）"})
        if has_t:
            text_context.append({"type": "transcript", "path": "transcript.md", "role": role if role != "none"
                                 else "reference", "attach": not has_tl, "note": "与 timeline.md 中的语音相同"})
        if has_o:
            text_context.append({"type": "ocr", "path": "ocr.md", "role": "reference", "attach": False,
                                 "note": "与 timeline.md 中的屏幕文字相同"})
    else:
        if has_t:
            text_context.append({"type": "transcript", "path": "transcript.md",
                                 "role": role if role != "none" else "reference", "attach": True})
        if has_o:
            text_context.append({"type": "ocr", "path": "ocr.md", "role": "reference", "attach": True})
        if has_tl:
            text_context.append({"type": "timeline", "path": "timeline.md", "role": "navigation", "attach": False,
                                 "note": "对齐画面、屏幕文字和语音；需要交叉核对时阅读"})
        if outcome.get("sheets"):
            text_context.append({"type": "contact_sheet_index", "path": "contact-sheets/index.md",
                                 "role": "navigation", "attach": False, "note": "小图 -> 时间 -> 帧文件"})
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


def reading_order(route: str, layout: Layout, outcome: dict[str, Any], guide_note: str = "本指南") -> list[str]:
    steps = [f"`ANALYSIS_GUIDE.md`（{guide_note}）"]
    sheets = outcome.get("sheets") or []
    role = outcome.get("transcript_role", "none")
    t_role = {"sole_source": "唯一的语音记录", "reference": "你所听到内容的参考"}.get(role, "")
    if route.startswith("native"):
        parts = outcome.get("native_parts") or []
        if len(parts) == 1:
            steps.append(f"视频 `{parts[0]['file']}`")
        elif parts:
            steps.append(f"视频分段 `{parts[0]['file']}` … `{parts[-1]['file']}`（共 {len(parts)} 段，"
                         "按顺序；`segments.json` 把其中的时间换算为全局时间）")
        if layout.file("transcript.md").exists():
            steps.append("`transcript.md`" + (f"——{t_role}" if t_role else "")
                         + ("" if outcome.get("transcript_attach") else "（仅在需要确切措辞时）"))
    elif route == "text":
        if layout.file("timeline.md").exists():
            steps.append("`timeline.md`——按时间合并的转写和屏幕文字")
    else:
        if sheets:
            steps.append(f"拼图 `contact-sheets/{sheets[0]['file']}`"
                         + (f" … `{sheets[-1]['file']}`" if len(sheets) > 1 else "")
                         + f"（共 {len(sheets)} 张；索引：`contact-sheets/index.md`）")
        for part in outcome.get("audio_parts") or []:
            steps.append(f"音频 `{part['file']}`")
        if layout.file("transcript.md").exists():
            steps.append("`transcript.md`" + (f"——{t_role}" if t_role else ""))
        if layout.file("ocr.md").exists():
            steps.append("`ocr.md`——屏幕文字")
        if layout.file("timeline.md").exists():
            steps.append("需要对齐画面和语音时读 `timeline.md`（可选）")
    return steps


def write_guide(layout: Layout, plan: dict[str, Any], outcome: dict[str, Any]) -> Path:
    route = outcome["route"]
    tmpl = data_file("references/analysis-guide.template.md").read_text(encoding="utf-8")
    hears = (route == "native-av" and bool(outcome.get("includes_audio", True))) or bool(outcome.get("audio_parts"))
    flags = {"transcript": layout.file("transcript.md").exists(), "ocr": layout.file("ocr.md").exists(),
             "hearing": hears,
             "frames": bool(outcome.get("frames")), "sheets": bool(outcome.get("sheets")),
             "audio_attachment": bool(outcome.get("audio_parts"))}
    evidence = {"frames": "（关键帧、拼图、`ocr.md`）",
                "text": "（`ocr.md` 以及 `timeline.md` 中的**屏幕文字**行）",
                "native-v": "（视频本身之中）"}.get(route, "")
    order = reading_order(route, layout, outcome)
    privacy = ("- 原始视频已在用户同意后交给 LLM 服务商；不要把它复制到别处。"
               if route.startswith("native") else
               "- 原始视频留在本机；只附带 `handoff.json` 中列出的文件。")
    values = {
        "video_name": str(plan["video"].get("name") or ""),
        "route": route,
        "route_label": ROUTE_LABELS.get(route, route),
        "duration": fmt_ts(float(plan["video"].get("duration") or 0)),
        "can_hear": "、听到" if hears else "",
        "hearing_source": "（音频在视频中）" if route == "native-av" else "（以单独的音频文件附带）",
        "can_hear_short": "或音频" if hears else "",
        "evidence_sources": evidence,
        "no_transcript_reason": outcome.get("transcript_skip_reason") or "没有转写出语音",
        "reading_order": "阅读顺序：\n\n" + "\n".join(f"{i}. {s}" for i, s in enumerate(order, 1)) + "\n",
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
             f"**路线：`{route}`**——{ROUTE_LABELS.get(route, route)}。", "",
             f"- LLM `{plan['llm']['model']}`，宿主 `{plan['host']['id']}` · 本地档位 `{plan['local']['tier']}` · "
             f"任务 `{plan['request']['task']}`",
             f"- 视频：{fmt_ts(float(video.get('duration') or 0))}，"
             + ("无音轨" if not video.get("has_audio") else f"语音占比约 {video.get('speech_ratio')}")
             + f"，文字密度 {video.get('text_density')}",
             f"- **上传原始视频：{'是' if handoff['uploads_raw_video'] else '否'}** · 上传原始音频："
             f"{'是' if handoff['uploads_raw_audio'] else '否'}", ""]
    lines += ["## 为什么选这条路线", ""]
    lines += [f"- {r}" for r in handoff["reasons"]]
    for g in handoff["degraded_from"]:
        lines.append(f"- 由 `{g['route']}` 降级：{g['why']}")
    for s in plan["delivery"].get("suggestions") or []:
        lines.append(f"- 建议：{s['why']} {s['how']}")
    lines += ["", "## 按此顺序阅读", ""]
    lines += [f"{i}. {s}" for i, s in enumerate(reading_order(route, layout, outcome, "本路线的规则"), 1)]
    lines += ["", "只附带 `handoff.json` 标记为需要附带的内容；需要细节时打开 `frames/` 中的单帧，"
              "或钻取某个时间段：", "", f"    {handoff['drill']}", ""]
    lines += ["## 输出", "", "| 步骤 | 状态 | 详情 |", "|---|---|---|"]
    for st in outcome["steps"]:
        lines.append(f"| {st['id']} | {st['status']} | {st['detail']} |")
    b = handoff["budget"]
    lines += ["", "## 预算", "",
              f"附带所有标记内容约需 {b['est_input_tokens']:,} 输入 token"
              f"（{b['images']} 张图片）——预算 {b['budget_tokens']:,}"
              + ("——**超出预算：全部附带前先询问用户**" if b["over_budget"] else "") + "。"]
    for n in handoff["needs_confirmation"]:
        lines.append(f"- 需要确认：{n['message']}")
    if warnings:
        lines += ["", "## 警告", ""]
        lines += [f"- {w['message']}" + (f"（可尝试：`{w['hint']}`）" if w.get("hint") else "") for w in warnings]
    lines += ["", "---", f"vidroute {__version__} · {handoff['created_at']} · 所有时间戳均为视频的全局时间。"]
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
        "notice": "本地模型按各自的许可使用（见 engines[].license 和 THIRD_PARTY_LICENSES.md）。"
                  "机器输出（转写、OCR）可能有错误。",
    }
    path = layout.file("manifest.json")
    write_json_atomic(path, data)
    return path
