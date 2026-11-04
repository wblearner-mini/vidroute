"""``vidroute drill <out-dir> --range START-END``: more detail for one stretch of time.

Works on an existing output folder (it reads ``plan.json`` for the video and
settings) and writes ``drill/<start>-<end>/`` next to the main outputs:
denser keyframes (change-based, or a fixed ``--fps``) with a contact sheet,
optionally a fresh transcript (``--asr``) and on-screen text (``--ocr``) for
the range. Everything uses global timestamps, like the main outputs.
"""

from __future__ import annotations

import math
from dataclasses import asdict
from pathlib import Path
from typing import Any

from vidroute.core.context import Ctx
from vidroute.core.engines import WorkerPool, fake_mode, get_engine
from vidroute.core.errors import NeedsInstall, NotFoundError, UsageError
from vidroute.core.install.manifest import load_manifest
from vidroute.core.plan import PLAN_SCHEMA
from vidroute.core.plan.local import asr_candidates, first_installed
from vidroute.core.plan.rules import load_rules
from vidroute.core.prepare import keyframes as kf
from vidroute.core.prepare import media
from vidroute.core.prepare import ocr as ocr_mod
from vidroute.core.prepare import timeline
from vidroute.core.prepare import transcript as tr
from vidroute.core.prepare.layout import Layout, reset_dir
from vidroute.core.probe import probe as run_probe
from vidroute.core.util.filelock import exclusive
from vidroute.core.util.jsonio import read_json, write_json_atomic
from vidroute.core.util.timefmt import fmt_compact, fmt_ts, parse_range

DRILL_SCHEMA = "vidroute/drill/1"
MAX_FPS_FRAMES = 120


def _label(engine_id: str) -> str:
    return f"fake-{engine_id}" if fake_mode() else engine_id


def _open(out_dir: str | Path) -> tuple[Layout, dict[str, Any], Path]:
    root = Path(out_dir).expanduser().resolve()
    plan = read_json(root / "plan.json")
    if not isinstance(plan, dict) or plan.get("schema") != PLAN_SCHEMA:
        raise UsageError(f"{root} is not a vidroute output folder (no plan.json)",
                         hint="vidroute prepare <video> --out <dir>, then drill <dir>/<slug>")
    video = Path(plan["request"]["video"])
    if not video.is_file():
        raise NotFoundError(f"The video of this output folder is gone: {video}")
    return Layout(root), plan, video


def _ranged_transcript(video: Path, main: Layout, sub: Layout, plan: dict[str, Any], start: float, end: float,
                       rules: dict[str, Any], ctx: Ctx, pool: WorkerPool) -> dict[str, Any]:
    manifest = load_manifest()
    loc = rules["local"]
    info = run_probe(video, ctx)
    if not info.get("has_audio"):
        return {"segments": [], "note": "no audio track"}
    wav = main.audio_wav
    if not wav.exists() or abs(media.wav_duration(wav) - float(info["duration"] or 0)) > 2.0:
        media.extract_wav(video, wav, ctx)
    language = (read_json(main.file("transcript.json"), default={}) or {}).get("language") or \
        plan["request"].get("language") or "auto"
    candidates = asr_candidates(language, plan["local"]["tier"], rules, manifest)
    if not candidates:
        raise NeedsInstall("No ASR engine is installed.", hint="vidroute install --capability asr")
    vad_id = first_installed(loc["vad_chain"], manifest) or "vad-energy"
    speech = get_engine(vad_id, manifest).detect(ctx, pool, wav, windows=[[start, end]],
                                                 max_speech=float(loc["asr_chunk_max_seconds"]))
    speech = [[max(s, start), min(e, end)] for s, e in speech if e > start and s < end]
    if not speech:
        return {"segments": [], "note": f"no speech between {fmt_ts(start)} and {fmt_ts(end)}"}
    chunks = [c for c in tr.make_chunks(speech, end, max_len=float(loc["asr_chunk_max_seconds"])) if c[0] >= start - 0.5]
    lang = None if language == "auto" else language
    segs = tr._transcribe(candidates[0], manifest, ctx, pool, sub, wav, chunks, lang)
    segs = [dict(s, source="asr") for s in segs if s.get("text") and (s.get("lang") or "").lower() != "nospeech"]
    return {"segments": segs, "engine": _label(candidates[0]), "language": lang or tr.detect_language(segs),
            "source": "asr", "vad": vad_id}


def drill(out_dir: str | Path, range_text: str, fps: float | None = None, asr: bool = False, ocr: bool = False,
          max_frames: int | None = None, ctx: Ctx | None = None) -> dict[str, Any]:
    ctx = ctx or Ctx()
    main, plan, video = _open(out_dir)
    with exclusive(main.work / "lock", ctx, f"{main.root.name} is being prepared by another vidroute run"):
        return _drill(main, plan, video, range_text, fps, asr, ocr, max_frames, ctx)


def _drill(main: Layout, plan: dict[str, Any], video: Path, range_text: str, fps: float | None, asr: bool, ocr: bool,
           max_frames: int | None, ctx: Ctx) -> dict[str, Any]:
    rules = load_rules()
    info = run_probe(video, ctx)
    duration = float(info["duration"] or 0)
    start, end = parse_range(range_text)
    if start >= duration:
        raise UsageError(f"The range starts after the end of the video ({fmt_ts(duration)})")
    end = min(end, duration)
    if fps is not None and not (0 < fps <= 10):
        raise UsageError("--fps must be between 0 and 10")
    if max_frames is not None and max_frames < 1:
        raise UsageError("--max-frames must be at least 1")
    name = f"{fmt_compact(start)}-{fmt_compact(end)}" + (f"-{fps:g}fps" if fps else "")
    sub = Layout(reset_dir(main.root / "drill" / name, main)).ensure()
    lim = (plan.get("effective") or {}).get("image_limits") or {}
    cap = int(min(max_frames or (MAX_FPS_FRAMES if fps else 24), lim.get("max_images") or MAX_FPS_FRAMES))
    v = info.get("video") or {}
    src = (int(v.get("width") or 0), int(v.get("height") or 0))
    frames: list[kf.Keyframe] = []
    sheets: list[dict[str, Any]] = []
    meta: dict[str, Any] = {}
    if info.get("has_video"):
        if fps:
            n = min(cap, max(1, math.ceil((end - start) * fps)))
            step = (end - start) / n
            frames = [kf.Keyframe(i + 1, round(start + (i + 0.5) * step, 3), round(start + i * step, 3),
                                  round(start + (i + 1) * step, 3), 0.0, []) for i in range(n)]
            meta = {"method": f"every {step:.2f} s", "candidates": n}
        else:
            size = kf.small_size(*src)
            cands, meta = kf.sample(video, duration, size, rules, ctx, start, end)
            frames, stats = kf.select(cands, size, rules, cap, start, end)
            meta.update(stats)
        thumbs = sub.work / "thumbs"
        ocr_dir = sub.work / "ocr-frames" if ocr else None
        grab = kf.grab_all(video, frames, sub.frames, thumbs, ocr_dir, rules, src, ctx)
        frames = [k for k in frames if k.file]
        sheets = kf.contact_sheets(frames, thumbs, sub.sheets, rules, ctx)
        kf.write_index(frames, sheets, sub.sheets / "index.md", grab["labels"])
    segments: list[dict[str, Any]] = []
    ocr_results: list[dict[str, Any]] = []
    t_info: dict[str, Any] = {}
    ocr_id = None
    with WorkerPool(ctx) as pool:
        if asr:
            t_info = _ranged_transcript(video, main, sub, plan, start, end, rules, ctx, pool)
            segments = t_info["segments"]
            if segments:
                tr.write_outputs(sub, t_info, "sole_source" if not plan["route"] == "native-av" else "reference")
        if ocr and frames:
            manifest = load_manifest()
            ocr_id = first_installed(rules["local"]["ocr_chain"], manifest)
            if ocr_id is None:
                raise NeedsInstall("No OCR engine is installed.", hint="vidroute install --capability ocr")
            ocr_results = ocr_mod.run_ocr(frames, sub, ocr_id, manifest, ctx, pool)
            ocr_mod.write_outputs(sub, ocr_results, _label(ocr_id))
    if not segments:  # fall back to the main transcript for this range, so the timeline still has speech
        main_t = read_json(main.file("transcript.json"), default={}) or {}
        segments = [s for s in main_t.get("segments", []) if start <= s["start"] < end]
    secs = timeline.build(frames, end, segments, ocr_results)
    secs = [s for s in secs if s.end > start]
    text = timeline.render(secs, f"{info.get('name')} {fmt_ts(start)}–{fmt_ts(end)}", plan["route"],
                           "sole_source", _label(ocr_id) if ocr_id else None, sheets)
    sub.file("timeline.md").write_text(text, encoding="utf-8")

    def rel(p: Path) -> str:
        return main.rel(p)

    attachments = [{"type": "image", "path": rel(sub.sheets / s["file"]),
                    "note": f"{len(s['frames'])} frames {fmt_ts(s['start'])}–{fmt_ts(s['end'])}, labelled with global time"}
                   for s in sheets]
    text_context = [{"type": t, "path": rel(sub.file(f)), "attach": True}
                    for t, f in (("transcript", "transcript.md"), ("ocr", "ocr.md")) if sub.file(f).exists()]
    text_context.append({"type": "timeline", "path": rel(sub.file("timeline.md")), "attach": not text_context})
    result = {
        "schema": DRILL_SCHEMA,
        "range": [round(start, 3), round(end, 3)],
        "dir": rel(sub.root),
        "frames": [dict(asdict(k), path=rel(sub.frames / k.file)) for k in frames],
        "sampling": meta,
        "attachments": attachments,
        "text_context": text_context,
        "transcript": {k: t_info.get(k) for k in ("engine", "language", "note")} if asr else None,
        "note": "All times are global. Paths are relative to the main output folder.",
    }
    write_json_atomic(sub.file("drill.json"), result)
    return {**result, "out_dir": str(main.root), "drill_dir": str(sub.root)}
