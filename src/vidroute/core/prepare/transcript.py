"""Speech -> transcript with global timestamps (R1–R3, R6).

Embedded text subtitles win when they cover enough of the detected speech;
otherwise VAD segments are packed into <= 25 s chunks and transcribed in
batches. Every batch is cached in ``.work/asr`` so an interrupted run resumes.
With ``language=auto`` the first ~30 s of speech decide the language and thus
the ASR chain (zh/yue -> "zh" selector, everything else -> "multi").
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from vidroute.core.context import Ctx
from vidroute.core.engines import WorkerPool, get_engine
from vidroute.core.errors import NeedsInstall, VidrouteError
from vidroute.core.install.manifest import Manifest
from vidroute.core.plan.local import asr_candidates, first_installed
from vidroute.core.prepare import media
from vidroute.core.prepare.layout import Layout
from vidroute.core.util.jsonio import read_json, write_json_atomic
from vidroute.core.util.timefmt import fmt_ts

BATCH_SECONDS = 300.0  # cache granularity for resumable ASR
LID_SECONDS = 30.0
NON_SPEECH_LANGS = {"nospeech", "", "unk"}
ISO639_2 = {"eng": "en", "chi": "zh", "zho": "zh", "cmn": "zh", "jpn": "ja", "kor": "ko", "fre": "fr", "fra": "fr",
            "ger": "de", "deu": "de", "spa": "es", "rus": "ru", "por": "pt", "ita": "it", "ara": "ar", "hin": "hi",
            "und": ""}


def normalize_lang(code: str | None) -> str:
    """Two-letter codes where container tags use ISO 639-2 (``eng`` -> ``en``)."""
    c = (code or "").strip().lower()
    return ISO639_2.get(c, c)


def make_chunks(speech: list[list[float]], duration: float, max_len: float = 25.0, merge_gap: float = 0.6,
                pad: float = 0.15, min_len: float = 0.3) -> list[list[float]]:
    chunks: list[list[float]] = []
    for s, e in sorted(speech):
        s, e = max(0.0, s - pad), min(duration, e + pad)
        if chunks and s - chunks[-1][1] <= merge_gap and e - chunks[-1][0] <= max_len:
            chunks[-1][1] = max(chunks[-1][1], e)
            continue
        while e - s > max_len:
            chunks.append([s, s + max_len])
            s += max_len
        if e - s >= min_len:
            chunks.append([s, e])
    return [[round(s, 3), round(e, 3)] for s, e in chunks]


class AsrCache:
    def __init__(self, layout: Layout, engine_id: str, language: str) -> None:
        self.path = layout.work / "asr" / f"{engine_id}-{language or 'auto'}.json"
        self.data: dict[str, Any] = read_json(self.path, default={}) or {}

    @staticmethod
    def key(chunk: list[float]) -> str:
        return f"{chunk[0]:.3f}-{chunk[1]:.3f}"

    def get(self, chunk: list[float]) -> dict[str, Any] | None:
        return self.data.get(self.key(chunk))

    def put_many(self, segs: list[dict[str, Any]]) -> None:
        for seg in segs:
            self.data[self.key([seg["start"], seg["end"]])] = seg
        write_json_atomic(self.path, self.data, indent=None)


def _transcribe(engine_id: str, manifest: Manifest, ctx: Ctx, pool: WorkerPool, layout: Layout, wav: Path,
                chunks: list[list[float]], language: str | None, total_hint: int | None = None) -> list[dict[str, Any]]:
    engine = get_engine(engine_id, manifest)
    cache = AsrCache(layout, engine_id, language or "auto")
    out: list[dict[str, Any]] = []
    todo = [c for c in chunks if cache.get(c) is None]
    done = len(chunks) - len(todo)
    total = total_hint or len(chunks)
    if done:
        ctx.progress("asr", done, total, f"resuming: {done} chunk(s) cached")
    batch: list[list[float]] = []
    secs = 0.0
    for c in todo + [None]:  # type: ignore[list-item]
        if c is not None:
            batch.append(c)
            secs += c[1] - c[0]
        if batch and (c is None or secs >= BATCH_SECONDS):
            ctx.check_cancelled()
            segs = engine.transcribe(ctx, pool, wav, batch, language)
            cache.put_many(segs)
            done += len(batch)
            ctx.progress("asr", done, total)
            batch, secs = [], 0.0
    for c in chunks:
        seg = cache.get(c)
        if seg:
            out.append(seg)
    return out


def detect_language(segments: list[dict[str, Any]]) -> str | None:
    weights: Counter[str] = Counter()
    for s in segments:
        lang = (s.get("lang") or "").lower()
        if lang and lang not in NON_SPEECH_LANGS and s.get("text"):
            weights[lang] += s["end"] - s["start"]
    return weights.most_common(1)[0][0] if weights else None


def build_transcript(
    video: Path,
    layout: Layout,
    probe: dict[str, Any],
    rules: dict[str, Any],
    manifest: Manifest,
    tier: str,
    language: str,
    force_asr: bool,
    ctx: Ctx,
    pool: WorkerPool,
    required: bool = True,
) -> dict[str, Any]:
    """Transcript info: segments (global time) plus source/engine/language and notes for SUMMARY.md.

    ``required`` is False when the plan expected no speech (R2): finding speech
    without an installed ASR engine then only warns instead of failing.
    """
    loc = rules["local"]
    duration = float(probe["duration"] or 0)
    info: dict[str, Any] = {"segments": [], "source": None, "engine": None, "language": None, "degraded_from": [],
                            "notes": [], "vad": None}

    # R1: embedded text subtitles
    cues: list[dict[str, Any]] = []
    subs = probe.get("text_subtitles") or []
    if subs:
        track = subs[0]
        try:
            cues = media.extract_subtitles(video, track["sub_index"], layout.work / f"sub{track['sub_index']}.srt")
            for c in cues:
                c.update(source="embedded_subtitle", engine=f"subtitle:{track['codec']}",
                         lang=normalize_lang(track.get("language")))
        except VidrouteError as exc:
            ctx.warn("subtitle_extract_failed", exc.message)

    def use_cues(note: str) -> dict[str, Any]:
        info.update(segments=cues, source="embedded_subtitle" if cues else None,
                    engine=cues[0]["engine"] if cues else None,
                    language=(cues[0].get("lang") or None) if cues else None)
        info["notes"].append(note)
        return info

    if not probe.get("has_audio"):
        return use_cues("no audio track")

    wav = layout.audio_wav
    if not wav.exists() or abs(media.wav_duration(wav) - duration) > 2.0:
        ctx.progress("audio", None, None, "extracting 16 kHz audio")
        media.extract_wav(video, wav, ctx)

    # R2 + VAD
    vad_id = first_installed(loc["vad_chain"], manifest) or "vad-energy"
    vad = get_engine(vad_id, manifest)
    ctx.progress("vad", None, None, vad_id)
    speech = vad.detect(ctx, pool, wav, max_speech=float(loc["asr_chunk_max_seconds"]))
    speech_seconds = sum(e - s for s, e in speech)
    info.update(vad=vad_id, speech_seconds=round(speech_seconds, 1), speech_segments=len(speech))
    write_json_atomic(layout.work / "speech.json", {"engine": vad_id, "segments": speech})

    cov = 0.0
    if cues:
        cov = media.coverage(cues, speech)
        info["subtitle_coverage"] = round(cov, 3)
        if cov >= float(loc["embedded_subtitle_min_coverage"]) and not force_asr:
            return use_cues(f"embedded subtitles cover {cov:.0%} of the detected speech: used instead of ASR (R1)")
        info["notes"].append(f"embedded subtitles cover only {cov:.0%} of the speech: running ASR as well")

    ratio = speech_seconds / max(duration, 1e-6)
    if speech_seconds < 0.5 or (ratio < float(loc["speech_ratio_skip_asr"]) and not force_asr):
        return use_cues(f"no speech detected (R2: {speech_seconds:.1f} s of speech, ratio {ratio:.3f})")

    chunks = make_chunks(speech, duration, max_len=float(loc["asr_chunk_max_seconds"]))
    lang = None if language in ("", "auto") else language
    candidates = asr_candidates(lang or "auto", tier, rules, manifest)
    if not candidates:
        hint = "vidroute install --capability asr"
        if cues:
            ctx.warn("asr_missing", f"Subtitles cover only {cov:.0%} of the speech and no ASR engine is installed: "
                                    "using the subtitles alone.", hint)
            return use_cues("no ASR engine installed: subtitles only")
        if not required:
            ctx.warn("asr_missing", "Speech was found, but no ASR engine is installed: no transcript.", hint)
            info["notes"].append("speech found but no ASR engine installed")
            return info
        raise NeedsInstall("No ASR engine is installed, so the speech cannot be transcribed.", hint=hint,
                           data={"needs": ["asr"]})
    engine_id = candidates[0]

    # Language identification on the first ~30 s of speech (R3).
    if lang is None:
        first, acc = [], 0.0
        for c in chunks:
            first.append(c)
            acc += c[1] - c[0]
            if acc >= float(loc.get("language_probe_seconds", LID_SECONDS)):
                break
        probe_segs = _transcribe(engine_id, manifest, ctx, pool, layout, wav, first, None, total_hint=len(chunks))
        detected = detect_language(probe_segs)
        info["language_detected"] = detected
        if detected:
            better = asr_candidates(detected, tier, rules, manifest)
            if better and better[0] != engine_id:
                info["notes"].append(f"language '{detected}' detected: ASR {engine_id} -> {better[0]} (R3)")
                engine_id, lang, candidates = better[0], detected, better
            elif not better:
                ctx.warn("asr_language_unsupported",
                         f"Detected language '{detected}' is not covered by an installed ASR engine; "
                         "the transcript may be poor.", "vidroute install --model asr-whisper-small-int8")

    # Transcribe, falling back along the chain on failure (engine A -> B, large -> small).
    segments: list[dict[str, Any]] = []
    while True:
        try:
            segments = _transcribe(engine_id, manifest, ctx, pool, layout, wav, chunks, lang)
            break
        except VidrouteError as exc:
            if exc.code == "cancelled":
                raise
            info["degraded_from"].append({"engine": engine_id, "why": exc.message[:300]})
            idx = candidates.index(engine_id) if engine_id in candidates else len(candidates)
            if idx + 1 >= len(candidates):
                raise
            ctx.warn("asr_engine_failed", f"{engine_id} failed ({exc.message[:120]}); trying {candidates[idx + 1]}.")
            engine_id = candidates[idx + 1]

    segments = [s for s in segments if s.get("text") and (s.get("lang") or "").lower() != "nospeech"]
    for s in segments:
        s["source"] = "asr"
    if cues:  # keep subtitle cues where ASR has nothing (partial coverage)
        segments = merge_sources(segments, cues)
    info.update(segments=segments, source="asr+embedded_subtitle" if cues else "asr", engine=engine_id,
                language=lang or detect_language(segments))
    return info


def merge_sources(asr: list[dict[str, Any]], cues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = list(asr)
    for c in cues:
        if not any(min(c["end"], a["end"]) - max(c["start"], a["start"]) > 0.3 for a in asr):
            out.append(c)
    return sorted(out, key=lambda s: (s["start"], s["end"]))


def write_outputs(layout: Layout, info: dict[str, Any], role: str) -> list[Path]:
    segs = info["segments"]
    files = []
    tj = layout.file("transcript.json")
    write_json_atomic(tj, {
        "role": role,
        "note": "Local transcript: reference only, may contain recognition errors. Timestamps are global (seconds).",
        "source": info.get("source"), "engine": info.get("engine"), "language": info.get("language"),
        "segments": [{k: s.get(k) for k in ("start", "end", "text", "lang", "source", "engine")} for s in segs],
    })
    files.append(tj)
    srt = layout.file("transcript.srt")
    srt.write_text(media.format_srt(segs), encoding="utf-8")
    files.append(srt)
    md = layout.file("transcript.md")
    head = {
        "sole_source": "This transcript is the ONLY record of what was said (the LLM cannot hear the audio). "
                       "It is machine-generated: proofread names, terms and numbers against what is visible on screen.",
        "reference": "Reference transcript with global timestamps; the LLM can hear the audio, which wins on conflicts.",
    }.get(role, "Machine transcript (reference only).")
    lines = ["# Transcript", "", f"> {head}", f"> source: {info.get('source')} · engine: {info.get('engine')} · "
             f"language: {info.get('language') or 'unknown'}", ""]
    for s in segs:
        lines.append(f"[{fmt_ts(s['start'])}–{fmt_ts(s['end'])}] {s['text']}")
    if not segs:
        lines.append("_(no speech transcribed)_")
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    files.append(md)
    return files
