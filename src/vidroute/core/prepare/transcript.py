"""Speech -> transcript with global timestamps (R1–R3, R6).

Embedded text subtitles win when they cover enough of the detected speech;
otherwise VAD segments are packed into <= 25 s chunks and transcribed in
batches. Every batch is cached in ``.work/asr`` so an interrupted run resumes.
With ``language=auto`` the first ~30 s of speech decide the language and thus
the ASR chain (zh/yue -> "zh" selector, everything else -> "multi").
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
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

BATCH_SECONDS = 60.0  # cache granularity for resumable ASR
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
        ctx.progress("asr", done, total, f"续跑：已缓存 {done} 个音频块")
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
    subtitles_only: bool = False,
    diarizer: Callable[[Path], list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Transcript info: segments (global time) plus source/engine/language and notes for SUMMARY.md.

    ``required`` is False when the plan expected no speech (R2): finding speech
    without an installed ASR engine then only warns instead of failing.
    ``subtitles_only`` extracts embedded subtitles but never runs VAD/ASR (the
    model hears the audio itself and no verbatim text was requested).
    ``diarizer`` returns speaker turns for the WAV; chunks are cut at speaker
    changes before ASR, so one segment holds one speaker.
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
        return use_cues("没有音轨")
    if subtitles_only:
        return use_cues("只用内嵌字幕：模型自己能听到音频")

    wav = layout.audio_wav
    if not wav.exists() or abs(media.wav_duration(wav) - duration) > 2.0:
        ctx.progress("audio", None, None, "正在提取 16 kHz 音频")
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
            return use_cues(f"内嵌字幕覆盖了检测到的语音的 {cov:.0%}：用它代替 ASR（R1）")
        info["notes"].append(f"内嵌字幕只覆盖了语音的 {cov:.0%}：同时运行 ASR")

    ratio = speech_seconds / max(duration, 1e-6)
    forced_blind = force_asr and speech_seconds < 0.5
    if forced_blind:  # the user insists: voice detection found nothing, so ASR hears the whole track
        ctx.warn("forced_asr_no_speech", "--force-asr：人声检测没有发现语音，因此会转写整条音轨；"
                                         "如果确实没有语音，结果可能有错误或凭空生成的文字。")
        speech = [[0.0, duration]]
    elif speech_seconds < 0.5 or (ratio < float(loc["speech_ratio_skip_asr"]) and not force_asr):
        return use_cues(f"未检测到语音（R2：语音 {speech_seconds:.1f} 秒，占比 {ratio:.3f}）")

    chunks = make_chunks(speech, duration, max_len=float(loc["asr_chunk_max_seconds"]))
    turns = diarizer(wav) if diarizer else []
    chunks = split_at_turns(chunks, turns)
    lang = None if language in ("", "auto") else language
    candidates = asr_candidates(lang or "auto", tier, rules, manifest)
    if not candidates:
        hint = "vidroute install --capability asr"
        if cues:
            ctx.warn("asr_missing", f"字幕只覆盖了语音的 {cov:.0%}，且没有安装 ASR 引擎："
                                    "只使用字幕。", hint)
            return use_cues("未安装 ASR 引擎：只用字幕")
        if not required:
            ctx.warn("asr_missing", "发现了语音，但没有安装 ASR 引擎：没有转写。", hint)
            info["notes"].append("发现了语音，但未安装 ASR 引擎")
            return info
        raise NeedsInstall("没有安装 ASR 引擎，无法转写语音。", hint=hint,
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
                info["notes"].append(f"检测到语言 '{detected}'：ASR {engine_id} -> {better[0]}（R3）")
                engine_id, lang, candidates = better[0], detected, better
            elif not better:
                ctx.warn("asr_language_unsupported",
                         f"检测到的语言 '{detected}' 不在已安装 ASR 引擎的支持范围内；"
                         "转写质量可能较差。", "vidroute install --model asr-whisper-small-int8")

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
            ctx.warn("asr_engine_failed", f"{engine_id} 失败（{exc.message[:120]}）；改试 {candidates[idx + 1]}。")
            engine_id = candidates[idx + 1]

    segments = [s for s in segments if s.get("text") and (s.get("lang") or "").lower() != "nospeech"]
    if forced_blind:  # music alone tends to decode into lone punctuation
        segments = [s for s in segments if any(ch.isalnum() for ch in s["text"])]
    for s in segments:
        s["source"] = "asr"
    speakers = label_speakers(segments, turns)
    if turns:
        info["speakers"] = speakers
        info["notes"].append(f"标出了 {speakers} 位说话人" if speakers > 1 else
                             "只检测到一位说话人：不加说话人标签")
    if cues:  # keep subtitle cues where ASR has nothing (partial coverage)
        segments = merge_sources(segments, cues)
    info.update(segments=segments, source="asr+embedded_subtitle" if cues else "asr", engine=engine_id,
                language=lang or detect_language(segments))
    return info


def split_at_turns(chunks: list[list[float]], turns: list[dict[str, Any]], min_len: float = 0.5) -> list[list[float]]:
    """Cut ASR chunks where the speaker changes; pieces shorter than ``min_len`` stay with their neighbour."""
    cuts = [round(b["start"], 3) for a, b in zip(turns, turns[1:]) if a["speaker"] != b["speaker"]]
    out: list[list[float]] = []
    for start, end in chunks:
        cur = start
        for c in cuts:
            if cur + min_len <= c <= end - min_len:
                out.append([cur, c])
                cur = c
        out.append([cur, end])
    return out


def label_speakers(segments: list[dict[str, Any]], turns: list[dict[str, Any]]) -> int:
    """Set ``speaker`` on each segment from the turn it overlaps most ("Speaker 1" speaks first).

    Returns the number of speakers heard in the segments. One speaker gets no
    label: it would only add noise to every line.
    """
    names: dict[Any, str] = {}
    picks: list[tuple[dict[str, Any], str]] = []
    for seg in sorted(segments, key=lambda s: s["start"]):
        overlap: dict[Any, float] = {}
        for t in turns:
            shared = min(seg["end"], t["end"]) - max(seg["start"], t["start"])
            if shared > 0:
                overlap[t["speaker"]] = overlap.get(t["speaker"], 0.0) + shared
        if overlap:
            who = max(overlap, key=lambda k: overlap[k])
            picks.append((seg, names.setdefault(who, f"Speaker {len(names) + 1}")))
    if len(names) > 1:
        for seg, name in picks:
            seg["speaker"] = name
    return len(names)


def spoken(seg: dict[str, Any]) -> str:
    """Segment text with its speaker label, when it has one."""
    return f"{seg['speaker']}: {seg['text']}" if seg.get("speaker") else seg["text"]


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
        "note": "本地转写：仅供参考，可能有识别错误。时间戳为全局时间（秒）。",
        "source": info.get("source"), "engine": info.get("engine"), "language": info.get("language"),
        "speakers": info.get("speakers"),
        "segments": [{k: s.get(k) for k in ("start", "end", "text", "lang", "source", "engine", "speaker")
                      if k != "speaker" or s.get(k)} for s in segs],
    })
    files.append(tj)
    srt = layout.file("transcript.srt")
    srt.write_text(media.format_srt([{**s, "text": spoken(s)} for s in segs]), encoding="utf-8")
    files.append(srt)
    md = layout.file("transcript.md")
    head = {
        "sole_source": "这份转写是说话内容的**唯一**记录（LLM 听不到音频）。"
                       "它由机器生成：请对照屏幕上可见的内容校对人名、术语和数字。",
        "reference": "带全局时间戳的参考转写；LLM 能听到音频，有冲突时以音频为准。",
    }.get(role, "机器转写（仅供参考）。")
    lines = ["# 转写", "", f"> {head}", f"> 来源：{info.get('source')} · 引擎：{info.get('engine')} · "
             f"语言：{info.get('language') or '未知'}"]
    if (info.get("speakers") or 0) > 1:
        lines.append("> Speaker 1、2……是机器划分的声音分组，按首次出现的顺序编号：可能分错，"
                     "也不说明这个人是谁。")
    lines.append("")
    for s in segs:
        lines.append(f"[{fmt_ts(s['start'])}–{fmt_ts(s['end'])}] {spoken(s)}")
    if not segs:
        lines.append("_（没有转写出语音）_")
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    files.append(md)
    return files
