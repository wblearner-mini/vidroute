"""``vidroute prepare`` / ``vidroute run``: turn a plan into what the agent hands its LLM.

Local routes (D3 ``frames``, D4 ``text``): transcript (R1–R3), keyframes (R5),
contact sheets, OCR (R4), audio attachment (when the model can listen and the
user agreed), timeline, then ``ANALYSIS_GUIDE.md``, ``handoff.json``,
``SUMMARY.md`` and ``manifest.json``.

Every step stores the fingerprint of its inputs in ``.work/steps.json``; a
re-run skips finished steps and ASR resumes batch by batch. Routes that hand
the raw video to the LLM provider refuse to run without
``accept_raw_video_handoff`` (exit code 3).
"""

from __future__ import annotations

import os
import shutil
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from vidroute.core import paths
from vidroute.core.context import Ctx
from vidroute.core.engines import WorkerPool, fake_mode, get_engine
from vidroute.core.errors import (
    NeedsConfirmation,
    NeedsInstall,
    NotFoundError,
    RunFailed,
    UsageError,
    VidrouteError,
)
from vidroute.core.install.manifest import MARKER, Manifest, load_manifest
from vidroute.core.plan import PLAN_SCHEMA, ROUTES, make_plan
from vidroute.core.plan.local import asr_candidates, first_installed, installed
from vidroute.core.plan.rules import load_rules
from vidroute.core.prepare import handoff as ho
from vidroute.core.prepare import keyframes as kf
from vidroute.core.prepare import media, native
from vidroute.core.prepare import ocr as ocr_mod
from vidroute.core.prepare import timeline
from vidroute.core.prepare import transcript as tr
from vidroute.core.prepare.layout import Layout, StepState, fingerprint, layout_for, remove_dir, reset_dir
from vidroute.core.probe import probe as run_probe
from vidroute.core.util.filelock import exclusive
from vidroute.core.util.jsonio import read_json, write_json_atomic
from vidroute.core.util.timefmt import fmt_ts

PIPELINE_VERSION = 2  # bump to invalidate cached steps after an incompatible change


def _same_file(a: str | Path, b: Path) -> bool:
    return os.path.normcase(str(Path(a).expanduser().resolve())) == os.path.normcase(str(b))


def load_plan(path: str | Path, video: Path) -> dict[str, Any]:
    p = Path(path).expanduser()
    data = read_json(p)
    if not isinstance(data, dict) or data.get("schema") != PLAN_SCHEMA:
        raise UsageError(f"{p} 不是 vidroute 计划（schema {PLAN_SCHEMA}）", hint="vidroute plan <video> --llm <id> --out <dir>")
    if not _same_file(data["request"]["video"], video):
        raise UsageError(f"{p} 是为 {data['request']['video']} 生成的，不是 {video}",
                         hint=f'vidroute plan "{video}" --llm <id>')
    return data


def _plan_for(video: Path, layout: Layout, plan_path: str | None, route: str, replan: bool, kw: dict[str, Any],
              ctx: Ctx) -> tuple[dict[str, Any], str]:
    data, source = None, "new"
    if plan_path:
        data, source = load_plan(plan_path, video), str(plan_path)
    elif not replan and kw.get("llm") is None and kw.get("host") is None and layout.file("plan.json").exists():
        try:
            data, source = load_plan(layout.file("plan.json"), video), "out_dir"
        except UsageError:
            data = None
    if data is not None:
        req = data["request"]
        prefer = "privacy" if kw.get("prefer") == "privacy" else req["prefer"]
        # Replan v0.1.0 privacy plans that still included a source audio attachment.
        privacy_replan = prefer == "privacy" and (req["prefer"] != "privacy" or data["delivery"]["uploads_raw_audio"])
        if route in ("auto", data["route"]) and not privacy_replan:
            if source == "out_dir":
                ctx.warn("plan_reused", f"使用 {layout.root} 中的计划（LLM {req['llm']}，任务 "
                                        f"{req['task']}）；传入 --llm 可重新计划。")
            return data, source
        kw = {"llm": req["llm"], "host": req["host"], "task": req["task"], "prefer": prefer,
              "language": req["language"], "modalities": req.get("modalities"),
              "audio_in_video": req.get("audio_in_video"), "ocr": req.get("ocr", "auto"),
              "force_asr": bool(req.get("force_asr")), "diarize": req.get("diarize", "auto")}
    plan = make_plan(video, kw.get("llm"), kw.get("host"), kw.get("task", "summary"), kw.get("prefer", "quality"),
                     kw.get("language", "auto"), kw.get("modalities"), kw.get("audio_in_video"), kw.get("ocr", "auto"),
                     route, bool(kw.get("force_asr")), ctx, diarize=kw.get("diarize", "auto"))
    return plan, "new"


def prepare(
    video: str | os.PathLike[str],
    plan: str | None = None,
    route: str = "auto",
    out: str | None = None,
    accept_raw_video_handoff: bool = False,
    llm: str | None = None,
    host: str | None = None,
    task: str = "summary",
    prefer: str = "quality",
    language: str = "auto",
    modalities: str | None = None,
    audio_in_video: str | None = None,
    ocr: str = "auto",
    force_asr: bool = False,
    replan: bool = False,
    ctx: Ctx | None = None,
    diarize: str = "auto",
) -> dict[str, Any]:
    ctx = ctx or Ctx()
    started = time.monotonic()
    path = Path(video).expanduser()
    if not path.exists():
        raise NotFoundError(f"找不到视频：{path}")
    if not path.is_file():
        raise UsageError(f"不是文件：{path}")
    path = path.resolve()
    if route not in ROUTES:
        raise UsageError(f"--route 必须是 {', '.join(ROUTES)} 之一（实际为 {route!r}）")
    if plan and not out:  # a plan written by `plan --out DIR` lives in DIR/<slug>/: keep the outputs next to it
        plan_dir = Path(plan).expanduser().resolve().parent
        if plan_dir.name == paths.video_slug(path):
            out = str(plan_dir.parent)
    layout = layout_for(path, out)
    _warn_broken_models(ctx)
    kw = {"llm": llm, "host": host, "task": task, "prefer": prefer, "language": language, "modalities": modalities,
          "audio_in_video": audio_in_video, "ocr": ocr, "force_asr": force_asr, "diarize": diarize}
    failed = False
    try:
        with exclusive(layout.work / "lock", ctx, f"另一个 vidroute 进程正在准备 {layout.root.name}"):
            try:
                return _prepare_in(path, layout, plan, route, replan, kw, accept_raw_video_handoff, started, ctx)
            except BaseException:
                failed = True
                raise
    finally:
        if failed:  # only the run that held the lock: a waiter must not touch the folder another run is filling
            _discard_if_empty(layout)


def _discard_if_empty(layout: Layout) -> None:
    """A run that failed before producing anything (say, an unreadable file) leaves no folder behind."""
    try:
        if any(p.is_file() and p != layout.work / "lock" for p in layout.root.rglob("*")):
            return
        shutil.rmtree(layout.root, ignore_errors=True)
    except OSError:
        pass


def _prepare_in(path: Path, layout: Layout, plan: str | None, route: str, replan: bool, kw: dict[str, Any],
                accept_raw_video_handoff: bool, started: float, ctx: Ctx) -> dict[str, Any]:
    the_plan, source = _plan_for(path, layout, plan, route, replan, kw, ctx)
    if accept_raw_video_handoff:  # consent given: the planner's "ask the user first" warning is settled
        ctx.warnings[:] = [w for w in ctx.warnings if w.get("code") != "raw_video_handoff"]
    plan_path = layout.file("plan.json")
    write_json_atomic(plan_path, the_plan)
    chosen = the_plan["route"]
    if chosen.startswith("native") and not accept_raw_video_handoff:
        raise NeedsConfirmation(
            f"路线 {chosen} 会把原始视频交给 LLM 服务商（{the_plan['llm']['model']}）。"
            "请先告知用户并征得同意。",
            code="raw_video_handoff",
            hint=f'vidroute prepare "{path}" --out "{layout.root.parent}" --accept-raw-video-handoff',
            data={"route": chosen, "plan_path": str(plan_path), "uploads_raw_video": True,
                  "alternative": f'vidroute prepare "{path}" --out "{layout.root.parent}" --route frames'},
        )
    if chosen.startswith("native"):
        try:
            result = _prepare_native(path, layout, the_plan, ctx)
        except native.DoesNotFit as exc:
            # Fall back along the chain (native -> frames): plan again with the route forced, keep the reason.
            ctx.warn("native_does_not_fit", f"{exc.message}：改用关键帧 + 转写。")
            req = the_plan["request"]
            the_plan = make_plan(path, req["llm"], req["host"], req["task"], req["prefer"], req["language"],
                                 req.get("modalities"), req.get("audio_in_video"), req.get("ocr", "auto"), "frames",
                                 bool(req.get("force_asr")), ctx, diarize=req.get("diarize", "auto"))
            the_plan["delivery"]["degraded_from"] = [
                {"route": chosen, "why": f"准备好的文件超出限额：{exc.message}"}] + [
                g for g in the_plan["delivery"]["degraded_from"] if g["route"] != chosen]
            the_plan["route_changed_by_prepare"] = True
            write_json_atomic(plan_path, the_plan)
            remove_dir(layout.native, layout)
            layout.file("segments.json").unlink(missing_ok=True)
            result = _prepare_local(path, layout, the_plan, accept_raw_video_handoff, ctx)
    elif chosen in ("frames", "text"):
        for stale in (layout.native,):
            remove_dir(stale, layout)
        layout.file("segments.json").unlink(missing_ok=True)
        result = _prepare_local(path, layout, the_plan, accept_raw_video_handoff, ctx)
    else:
        raise UsageError(f"路线 {chosen!r} 无法在本地准备。")
    result.update(plan_path=str(plan_path), plan_source=source, elapsed_seconds=round(time.monotonic() - started, 1))
    return result


def _warn_broken_models(ctx: Ctx) -> None:
    """A model that was installed and is now damaged would otherwise just be replaced by the next engine."""
    if fake_mode():
        return
    for model in load_manifest().models.values():
        if model.builtin or not (model.model_dir / MARKER).exists() or model.installed_status()["installed"]:
            continue
        ctx.warn("model_damaged", f"模型 {model.id} 已损坏（文件缺失或大小不对）：vidroute 不会使用它，"
                                  "结果可能来自备用引擎。",
                 f"vidroute install --model {model.id} --force --yes")


# ------------------------------------------------------------------- steps


def _label(engine_id: str) -> str:
    """Engine name as shown in outputs; fake test engines can never pass for real ones."""
    return f"fake-{engine_id}" if fake_mode() else engine_id


def _step(outcome: dict[str, Any], sid: str, status: str, detail: str) -> None:
    outcome["steps"].append({"id": sid, "status": status, "detail": detail})


def _plan_step(plan: dict[str, Any], sid: str) -> dict[str, Any]:
    return next((s for s in plan["steps"] if s["id"] == sid), {})


def _files_exist(layout: Layout, rels: list[str]) -> bool:
    return all((layout.root / r).exists() for r in rels)


def _transcript(video: Path, layout: Layout, plan: dict[str, Any], info: dict[str, Any], rules: dict[str, Any],
                manifest: Manifest, state: StepState, role_if_any: str, ctx: Ctx, pool: WorkerPool,
                outcome: dict[str, Any], asr_allowed: bool = True) -> list[dict[str, Any]]:
    req = plan["request"]
    tier = plan["local"]["tier"]
    language = req.get("language") or "auto"
    force_asr = bool(req.get("force_asr"))
    loc = rules["local"]
    required = force_asr or float(info.get("speech_ratio") or 0) >= float(loc["speech_ratio_skip_asr"])
    diarize_step = _plan_step(plan, "diarize")
    diarizer = None
    if diarize_step.get("status") == "planned" and asr_allowed:
        def diarizer(wav: Path) -> list[dict[str, Any]]:
            try:
                # Costly on long videos, and ASR's resume cache is keyed by chunks cut at these turns: keep them.
                key = [diarize_step["engine"], wav.stat().st_size]
                saved = read_json(layout.work / "turns.json", default={}) or {}
                if saved.get("key") == key:
                    return saved["turns"]
                ctx.progress("diarize", None, None, diarize_step["engine"])
                turns = get_engine(diarize_step["engine"], manifest).diarize(ctx, pool, wav)
                write_json_atomic(layout.work / "turns.json", {"key": key, "turns": turns})
                return turns
            except VidrouteError as exc:
                if exc.code == "cancelled":
                    raise
                ctx.warn("diarize_failed", f"已跳过说话人标签：{exc.message[:160]}")
                return []
    fp = fingerprint("transcript", PIPELINE_VERSION, layout.root.name, language, force_asr, role_if_any, asr_allowed,
                     diarizer is not None,
                     asr_candidates(language, tier, rules, manifest), first_installed(loc["vad_chain"], manifest),
                     loc)
    prev = state.get("transcript")
    if state.done("transcript", fp) and _files_exist(layout, prev.get("files", [])):
        tinfo = dict(prev["info"])
        data = read_json(layout.file("transcript.json"), default={}) or {}
        tinfo["segments"] = data.get("segments", []) if prev.get("files") else []
        status = "cached"
    else:
        if (asr_allowed and required and not info.get("text_subtitles") and info.get("has_audio")
                and not asr_candidates(language, tier, rules, manifest)):
            raise NeedsInstall("没有安装 ASR 引擎，无法转写语音。",
                               hint="vidroute install --capability asr", data={"needs": ["asr"]})
        tinfo = tr.build_transcript(video, layout, info, rules, manifest, tier, language, force_asr, ctx, pool,
                                    required=required, subtitles_only=not asr_allowed, diarizer=diarizer)
        if tinfo.get("engine") and not str(tinfo["engine"]).startswith("subtitle:"):
            tinfo["engine"] = _label(tinfo["engine"])
        for name in ("transcript.json", "transcript.srt", "transcript.md"):
            layout.file(name).unlink(missing_ok=True)
        files = tr.write_outputs(layout, tinfo, role_if_any) if tinfo["segments"] else []
        state.mark("transcript", fp, info={k: v for k, v in tinfo.items() if k != "segments"},
                   files=[layout.rel(f) for f in files])
        status = "done"
    segs = tinfo["segments"]
    outcome["engines"]["vad"] = tinfo.get("vad")
    if tinfo.get("engine") and not str(tinfo["engine"]).startswith("subtitle:"):
        outcome["engines"]["asr"] = str(tinfo["engine"]).removeprefix("fake-")
    if segs:
        speech = f"，语音 {fmt_ts(tinfo['speech_seconds'])}" if tinfo.get("speech_seconds") else ""
        detail = (f"{len(segs)} 个片段，来自 {tinfo['source']}（{tinfo['engine']}），语言 "
                  f"{tinfo.get('language') or '未知'}{speech}")
        _step(outcome, "transcript", status, detail + "".join(f"; {n}" for n in tinfo.get("notes", [])))
    else:
        reason = "；".join(tinfo.get("notes") or []) or "没有语音"
        outcome["transcript_skip_reason"] = reason
        _step(outcome, "transcript", "skipped", reason)
    for d in tinfo.get("degraded_from") or []:
        outcome["notes"].append(f"ASR {d['engine']} 失败：{d['why'][:120]}")
    return segs


def _keyframes(video: Path, layout: Layout, plan: dict[str, Any], info: dict[str, Any], rules: dict[str, Any],
               state: StepState, want_thumbs: bool, want_ocr_copies: bool, ctx: Ctx,
               outcome: dict[str, Any]) -> tuple[list[kf.Keyframe], str]:
    f = rules["frames"]
    duration = float(info["duration"] or 0)
    frames_plan = plan["delivery"].get("frames") or {}
    target = int(frames_plan.get("target_frames") or
                 max(1, min(int(f["max_frames"]), int(duration // float(f["base_interval_seconds"])) + 1)))
    v = info.get("video") or {}
    src = (int(v.get("width") or 0), int(v.get("height") or 0))
    fp = fingerprint("keyframes", PIPELINE_VERSION, layout.root.name, target, f, want_thumbs, want_ocr_copies)
    work = layout.work / "keyframes.json"
    if state.done("keyframes", fp) and work.exists():
        data = read_json(work)
        frames = [kf.Keyframe(**k) for k in data["frames"]]
        if all((layout.frames / k.file).exists() for k in frames if k.file):
            _report_omitted(layout, data["stats"].get("omitted") or [], target, ctx)
            _step(outcome, "keyframes", "cached", data["detail"])
            return frames, fp
    reset_dir(layout.frames, layout)
    thumbs = reset_dir(layout.work / "thumbs", layout) if want_thumbs else None
    ocr_dir = reset_dir(layout.work / "ocr-frames", layout) if want_ocr_copies else None
    size = kf.small_size(src[0], src[1])
    cands, meta = kf.sample(video, duration, size, rules, ctx)
    frames, stats = kf.select(cands, size, rules, target, 0.0, duration)
    grab = kf.grab_all(video, frames, layout.frames, thumbs, ocr_dir, rules, src, ctx)
    if grab["failed"]:
        ctx.warn("frames_failed", f"有 {len(grab['failed'])} 张关键帧无法提取。")
    frames = [k for k in frames if k.file]
    if not frames:
        raise RunFailed("无法从视频中提取任何关键帧。", code="no_frames")
    detail = (f"{len(frames)} 帧（预算 {target}），来自 {stats['unique_shots']} 个不同镜头"
              + (f"，合并了 {stats['repeats_folded']} 次重复" if stats["repeats_folded"] else "")
              + f"；{meta['candidates']} 个候选（{meta['method']}）")
    if not grab["labels"] and want_thumbs:
        detail += "；小图没有标签（ffmpeg drawtext 不可用）"
        ctx.warn("no_tile_labels", "这里的 ffmpeg 无法绘制文字：拼图小图上没有时间戳"
                                   "（见 contact-sheets/index.md）。")
    if stats["omitted"]:
        detail += f"；漏掉了 {len(stats['omitted'])} 个较长镜头（omitted-shots.md）"
    _report_omitted(layout, stats["omitted"], target, ctx)
    write_json_atomic(work, {"meta": meta, "stats": stats, "grab": grab, "detail": detail,
                             "frames": [asdict(k) for k in frames]})
    state.mark("keyframes", fp, labels=grab["labels"])
    _step(outcome, "keyframes", "done", detail)
    return frames, fp


def _report_omitted(layout: Layout, omitted: list[list[float]], target: int, ctx: Ctx) -> None:
    """The frame budget left out shots that stayed on screen: say so, and say how to get them."""
    path = layout.file("omitted-shots.md")
    path.unlink(missing_ok=True)
    if not omitted:
        return
    start, end = omitted[0]
    drill = f'vidroute drill "{layout.root}" --range {fmt_ts(start)}-{fmt_ts(end)} --ocr'
    lines = ["# 没有关键帧的镜头", "",
             f"{target} 帧的预算漏掉了下面 {len(omitted)} 个镜头，每个都在屏幕上停留了好几秒。"
             "关键帧中**没有**它们。在对这些时间段下任何结论之前，或者问题涉及它们时，"
             "请用下面的命令查看：", "", f"    {drill}", ""]
    lines += [f"- {fmt_ts(s)}–{fmt_ts(e)}" for s, e in omitted]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ctx.warn("frames_omitted", f"有 {len(omitted)} 个在屏幕上停留过的镜头没有关键帧（预算 {target} 帧）；"
                               "这些帧并没有涵盖全部内容：见 omitted-shots.md。", drill)


def _sheets(layout: Layout, frames: list[kf.Keyframe], rules: dict[str, Any], state: StepState, kf_fp: str,
            ctx: Ctx, outcome: dict[str, Any]) -> list[dict[str, Any]]:
    fp = fingerprint("sheets", kf_fp, rules["frames"]["contact_sheet"])
    prev = state.get("sheets")
    if state.done("sheets", fp) and _files_exist(layout, [f"contact-sheets/{s['file']}" for s in prev.get("sheets", [])]):
        _step(outcome, "contact_sheets", "cached", prev["detail"])
        return prev["sheets"]
    reset_dir(layout.sheets, layout)
    sheets = kf.contact_sheets(frames, layout.work / "thumbs", layout.sheets, rules, ctx)
    labels = bool(state.get("keyframes").get("labels", True))
    kf.write_index(frames, sheets, layout.sheets / "index.md", labels)
    cs = rules["frames"]["contact_sheet"]
    detail = f"{len(sheets)} 张拼图，每张最多 {cs['cols']}x{cs['rows']} 格"
    state.mark("sheets", fp, sheets=sheets, detail=detail)
    _step(outcome, "contact_sheets", "done", detail)
    return sheets


def _ocr(layout: Layout, plan: dict[str, Any], frames: list[kf.Keyframe], rules: dict[str, Any], manifest: Manifest,
         state: StepState, kf_fp: str, ctx: Ctx, pool: WorkerPool, outcome: dict[str, Any]) -> list[dict[str, Any]]:
    step = _plan_step(plan, "ocr")
    if step.get("status") not in ("planned", "needs_install"):
        for name in ("ocr.json", "ocr.md"):
            layout.file(name).unlink(missing_ok=True)
        _step(outcome, "ocr", "skipped", step.get("reason") or "未计划")
        return []
    engine_id = step.get("engine") if step.get("engine") and installed(step["engine"], manifest) else \
        first_installed(rules["local"]["ocr_chain"], manifest)
    if engine_id is None:
        for name in ("ocr.json", "ocr.md"):
            layout.file(name).unlink(missing_ok=True)
        ctx.warn("ocr_missing", "屏幕文字会有帮助，但没有安装 OCR 引擎。",
                 "vidroute install --capability ocr")
        _step(outcome, "ocr", "skipped", "未安装 OCR 引擎（可选）：vidroute install --capability ocr")
        return []
    fp = fingerprint("ocr", kf_fp, engine_id)
    if state.done("ocr", fp) and _files_exist(layout, ["ocr.json", "ocr.md"]):
        results = (read_json(layout.file("ocr.json"), default={}) or {}).get("frames", [])
        status = "cached"
    else:
        results = ocr_mod.run_ocr(frames, layout, engine_id, manifest, ctx, pool)
        ocr_mod.write_outputs(layout, results, _label(engine_id))
        state.mark("ocr", fp)
        status = "done"
    outcome["engines"]["ocr"] = engine_id
    n_lines = sum(len(r["lines"]) for r in results)
    _step(outcome, "ocr", status, f"{len(results)} 帧，{n_lines} 行文字（{_label(engine_id)}）")
    return results


def _audio_parts(video: Path, layout: Layout, plan: dict[str, Any], info: dict[str, Any], rules: dict[str, Any],
                 accepted: bool, ctx: Ctx, outcome: dict[str, Any]) -> list[dict[str, Any]]:
    aa = plan["delivery"].get("audio_attachment") or {}
    if not aa.get("enabled"):
        return []
    if not accepted:
        ctx.warn("audio_not_attached", "模型能听音轨，但附带音轨会把原始音频交给 LLM 服务商："
                                       "请先询问用户。",
                 "加 --accept-raw-video-handoff 重新运行 prepare 即可附带音频")
        _step(outcome, "audio_attachment", "skipped", "需要用户同意（--accept-raw-video-handoff）")
        return []
    duration = float(info["duration"] or 0)
    n = max(1, int(aa.get("parts") or 1))
    overlap = float(rules["delivery"]["segment_overlap_seconds"]) if n > 1 else 0.0
    per = duration / n
    rate = float(aa.get("est_tokens") or 0) / max(duration, 1e-6)
    parts = []
    out_dir = reset_dir(layout.root / "audio" / "attach", layout)
    for i in range(n):
        ctx.check_cancelled()
        start = max(0.0, i * per - (overlap if i else 0.0))
        end = min(duration, (i + 1) * per)
        p = media.encode_audio(video, out_dir / f"part-{i + 1:02d}", ctx, start, end, int(aa["kbps"]),
                               int(aa.get("sample_rate") or 16000))
        parts.append({"id": i + 1, "file": layout.rel(p), "start": round(start, 3), "end": round(end, 3),
                      "kbps": aa["kbps"], "est_tokens": int(rate * (end - start))})
        ctx.progress("audio_attachment", i + 1, n)
    _step(outcome, "audio_attachment", "done", f"{n} 段，{aa['kbps']} kbps 单声道")
    return parts


def _prepare_local(video: Path, layout: Layout, plan: dict[str, Any], accepted: bool, ctx: Ctx) -> dict[str, Any]:
    rules = load_rules()
    manifest = load_manifest()
    info = run_probe(video, ctx)
    route = plan["route"]
    state = StepState(layout)
    outcome: dict[str, Any] = {"route": route, "steps": [], "engines": {}, "timings": {}, "notes": [],
                               "frames": [], "sheets": [], "audio_parts": [], "degraded_from": [],
                               "fake_engines": fake_mode()}
    aa = plan["delivery"].get("audio_attachment") or {}
    role_if_any = "reference" if (aa.get("enabled") and accepted and route == "frames") else "sole_source"
    ocr_step = _plan_step(plan, "ocr")
    want_ocr = ocr_step.get("status") in ("planned", "needs_install")
    frames: list[kf.Keyframe] = []
    segments: list[dict[str, Any]] = []
    ocr_results: list[dict[str, Any]] = []

    def timed(name: str, fn: Any, *args: Any) -> Any:
        ctx.check_cancelled()
        t0 = time.monotonic()
        value = fn(*args)
        outcome["timings"][name] = round(time.monotonic() - t0, 2)
        return value

    with WorkerPool(ctx) as pool:
        segments = timed("transcript", _transcript, video, layout, plan, info, rules, manifest, state, role_if_any,
                         ctx, pool, outcome)
        if info.get("has_video"):
            frames, kf_fp = timed("keyframes", _keyframes, video, layout, plan, info, rules, state,
                                  route == "frames", want_ocr, ctx, outcome)
            outcome["engines"]["scene"] = "scene-ffmpeg"
            if route == "frames":
                outcome["sheets"] = timed("contact_sheets", _sheets, layout, frames, rules, state, kf_fp, ctx, outcome)
            else:
                remove_dir(layout.sheets, layout)
                _step(outcome, "contact_sheets", "skipped", "text 路线：不附带画面")
            ocr_results = timed("ocr", _ocr, layout, plan, frames, rules, manifest, state, kf_fp, ctx, pool, outcome)
        else:
            for stale in (layout.frames, layout.sheets):
                remove_dir(stale, layout)
            for name in ("ocr.json", "ocr.md"):
                layout.file(name).unlink(missing_ok=True)
            _step(outcome, "keyframes", "skipped", "没有视频流")
        remove_dir(layout.root / "audio" / "attach", layout)
        if route == "frames":
            outcome["audio_parts"] = timed("audio_attachment", _audio_parts, video, layout, plan, info, rules,
                                           accepted, ctx, outcome)
    outcome["frames"] = [asdict(k) for k in frames]
    outcome["transcript_role"] = ("reference" if outcome["audio_parts"] else "sole_source") if segments else "none"

    secs = timeline.build(frames, float(info["duration"] or 0), segments, ocr_results)
    ocr_id = outcome["engines"].get("ocr")
    text = timeline.render(secs, str(info.get("name")), route, outcome["transcript_role"],
                           _label(ocr_id) if ocr_id else None, outcome["sheets"])
    layout.file("timeline.md").write_text(text, encoding="utf-8")
    _step(outcome, "timeline", "done", f"{sum(1 for s in secs if s.frame or s.said)} 个时间段")
    return _finish(layout, plan, outcome, manifest, ctx)


def _finish(layout: Layout, plan: dict[str, Any], outcome: dict[str, Any], manifest: Manifest,
            ctx: Ctx) -> dict[str, Any]:
    """Guide, handoff, summary and manifest — the same for every route."""
    route = outcome["route"]
    for note in outcome["notes"]:
        ctx.warn("note", note)
    ho.write_guide(layout, plan, outcome)
    handoff = ho.build_handoff(layout, plan, outcome, ctx.warnings)
    write_json_atomic(layout.file("handoff.json"), handoff)
    ho.write_summary(layout, plan, outcome, handoff, ctx.warnings)
    ho.write_manifest(layout, plan, outcome, manifest)
    return {
        "out_dir": str(layout.root),
        "route": route,
        "summary_path": str(layout.file("SUMMARY.md")),
        "guide_path": str(layout.file("ANALYSIS_GUIDE.md")),
        "handoff_path": str(layout.file("handoff.json")),
        "handoff": handoff,
        "steps": outcome["steps"],
        "timings": outcome["timings"],
    }


def _prepare_native(video: Path, layout: Layout, plan: dict[str, Any], ctx: Ctx) -> dict[str, Any]:
    """D1/D2: the (transcoded, maybe segmented) video, plus the local transcript when the plan asks for it."""
    rules = load_rules()
    manifest = load_manifest()
    info = run_probe(video, ctx)
    route = plan["route"]
    state = StepState(layout)
    outcome: dict[str, Any] = {"route": route, "steps": [], "engines": {}, "timings": {}, "notes": [],
                               "frames": [], "sheets": [], "audio_parts": [], "degraded_from": [],
                               "fake_engines": fake_mode()}
    for stale in (layout.frames, layout.sheets, layout.root / "audio" / "attach"):
        remove_dir(stale, layout)
    for name in ("ocr.json", "ocr.md", "timeline.md"):
        layout.file(name).unlink(missing_ok=True)
    t0 = time.monotonic()
    nat = native.produce(video, layout, plan, info, float(rules["delivery"]["segment_overlap_seconds"]), ctx)
    outcome["timings"]["native"] = round(time.monotonic() - t0, 2)
    tokens = native.video_tokens_per_part(plan, nat["parts"])
    outcome["native_parts"] = [{**p, "est_tokens": t} for p, t in zip(nat["parts"], tokens)]
    outcome["includes_audio"] = nat["includes_audio"]
    _step(outcome, "native_video", "done", f"{nat['method']}: {native.describe(nat['parts'])}"
          + ("" if nat["includes_audio"] else "；附件中没有音轨"))
    asr_step = _plan_step(plan, "asr")
    role = "reference" if route == "native-av" else "sole_source"
    segments: list[dict[str, Any]] = []
    asr_allowed = asr_step.get("status") in ("planned", "needs_install")
    if asr_allowed or info.get("text_subtitles"):
        with WorkerPool(ctx) as pool:
            t0 = time.monotonic()
            segments = _transcript(video, layout, plan, info, rules, manifest, state, role, ctx, pool, outcome,
                                   asr_allowed=asr_allowed)
            outcome["timings"]["transcript"] = round(time.monotonic() - t0, 2)
    else:
        for name in ("transcript.json", "transcript.srt", "transcript.md"):
            layout.file(name).unlink(missing_ok=True)
        reason = asr_step.get("reason") or "不需要"
        outcome["transcript_skip_reason"] = reason
        _step(outcome, "transcript", "skipped", reason)
    outcome["transcript_role"] = role if segments else "none"
    outcome["transcript_attach"] = bool(segments) and (route == "native-v" or
                                                       bool(plan["delivery"]["transcript"].get("attach")))
    return _finish(layout, plan, outcome, manifest, ctx)


def run(video: str | os.PathLike[str], llm: str | None = None, **kwargs: Any) -> dict[str, Any]:
    """``vidroute run``: plan from scratch, then prepare."""
    return prepare(video, llm=llm, replan=True, **kwargs)
