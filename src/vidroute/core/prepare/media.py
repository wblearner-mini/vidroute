"""ffmpeg operations used by prepare/drill: audio extraction, subtitle extraction, frame grabs."""

from __future__ import annotations

import re
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vidroute.core.context import Ctx
from vidroute.core.errors import RunFailed
from vidroute.core.util import ffmpeg, proc
from vidroute.core.util.timefmt import fmt_srt, fmt_ts


def extract_wav(video: Path, out: Path, ctx: Ctx, start: float | None = None, end: float | None = None,
                timeout: float = 7200.0) -> Path:
    """16 kHz mono 16-bit PCM WAV (what VAD/ASR workers expect). Times are global seconds."""
    info = ffmpeg.require()
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp.wav")
    args = [info.ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    if start:
        args += ["-ss", f"{start:.3f}"]
    args += ["-i", str(video)]
    if end is not None:
        args += ["-t", f"{end - (start or 0.0):.3f}"]
    args += ["-vn", "-sn", "-dn", "-map", "0:a:0", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(tmp)]
    ctx.check_cancelled()
    res = proc.run(args, timeout=timeout)
    if not res.ok:
        tmp.unlink(missing_ok=True)
        raise RunFailed(f"Audio extraction failed: {(res.stderr or 'timed out').strip()[-300:]}", code="ffmpeg_failed")
    tmp.replace(out)
    return out


def encode_audio(video: Path, out_stem: Path, ctx: Ctx, start: float, end: float, kbps: int,
                 sample_rate: int = 16000) -> Path:
    """Mono MP3 (AAC/.m4a when ffmpeg lacks libmp3lame) of [start, end) for LLMs that can listen."""
    info = ffmpeg.require()
    mp3 = bool(info.encoders.get("libmp3lame"))
    out = out_stem.with_suffix(".mp3" if mp3 else ".m4a")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.stem + ".tmp" + out.suffix)
    codec = ["-c:a", "libmp3lame"] if mp3 else ["-c:a", "aac"]
    args = [info.ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    if start:
        args += ["-ss", f"{start:.3f}"]
    args += ["-i", str(video), "-t", f"{end - start:.3f}", "-vn", "-sn", "-dn", "-map", "0:a:0", "-ac", "1",
             "-ar", str(sample_rate), *codec, "-b:a", f"{kbps}k", str(tmp)]
    ctx.check_cancelled()
    res = proc.run(args, timeout=max(600.0, (end - start) * 0.5))
    if not res.ok:
        tmp.unlink(missing_ok=True)
        raise RunFailed(f"Audio encoding failed: {(res.stderr or 'timed out').strip()[-300:]}", code="ffmpeg_failed")
    tmp.replace(out)
    return out


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


# ---------------------------------------------------------------- subtitles

_SRT_TIME = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")
_TAGS = re.compile(r"<[^>]+>|\{\\[^}]*\}")


def parse_srt(text: str) -> list[dict[str, Any]]:
    cues = []
    for block in re.split(r"\r?\n\s*\r?\n", text.strip()):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        for i, ln in enumerate(lines):
            m = _SRT_TIME.search(ln)
            if m:
                g = [int(x) for x in m.groups()]
                start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 10 ** len(m.group(4))
                end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 10 ** len(m.group(8))
                body = " ".join(_TAGS.sub("", x).strip() for x in lines[i + 1:]).strip()
                if body:
                    cues.append({"start": round(start, 3), "end": round(end, 3), "text": body})
                break
    return cues


def format_srt(segments: list[dict[str, Any]]) -> str:
    out = []
    for i, s in enumerate(segments, 1):
        out.append(f"{i}\n{fmt_srt(s['start'])} --> {fmt_srt(s['end'])}\n{s['text']}\n")
    return "\n".join(out)


def extract_subtitles(video: Path, sub_index: int, out_srt: Path) -> list[dict[str, Any]]:
    """Extract the n-th subtitle stream as SRT and parse it (text-based tracks only)."""
    info = ffmpeg.require()
    out_srt.parent.mkdir(parents=True, exist_ok=True)
    res = proc.run([info.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(video),
                    "-map", f"0:s:{sub_index}", "-c:s", "srt", str(out_srt)], timeout=600)
    if not res.ok or not out_srt.exists():
        raise RunFailed(f"Could not extract subtitle track {sub_index}: {(res.stderr or '').strip()[-200:]}",
                        code="subtitle_failed")
    return parse_srt(out_srt.read_text(encoding="utf-8", errors="replace"))


def coverage(cues: list[dict[str, Any]], speech: list[list[float]]) -> float:
    """Share of detected speech time covered by subtitle cues (1.0 when there is no speech)."""
    total = sum(e - s for s, e in speech)
    if total <= 0:
        return 1.0 if cues else 0.0
    covered = 0.0
    for s, e in speech:
        for c in cues:
            lo, hi = max(s, c["start"]), min(e, c["end"])
            if hi > lo:
                covered += hi - lo
    return min(1.0, covered / total)


# ------------------------------------------------------------------- frames


@dataclass
class FrameOut:
    """One JPEG written by :func:`grab`: capped at ``max_side`` (never upscaled) or fitted into ``box``."""

    path: Path
    max_side: int | None = None
    box: tuple[int, int] | None = None
    qscale: int = 3
    label: str | None = None


def fit(width: int, height: int, box_w: int, box_h: int, upscale: bool = False) -> tuple[int, int]:
    """Largest even size with the same aspect that fits the box."""
    scale = min(box_w / width, box_h / height)
    if not upscale:
        scale = min(1.0, scale)
    return max(2, int(width * scale) // 2 * 2), max(2, int(height * scale) // 2 * 2)


def _scale_filter(o: FrameOut, src: tuple[int, int] | None) -> str:
    if o.box is not None:
        if src:
            w, h = fit(src[0], src[1], o.box[0], o.box[1], upscale=True)
            return f"scale={w}:{h}"
        return f"scale={o.box[0]}:{o.box[1]}:force_original_aspect_ratio=decrease"
    side = o.max_side or 1280
    if src:
        w, h = fit(src[0], src[1], side, side)
        return f"scale={w}:{h}"
    return f"scale='if(gt(iw,ih),min({side},iw),-2)':'if(gt(iw,ih),-2,min({side},ih))'"


def grab(video: Path, t: float, outs: list[FrameOut], font: str | None = None,
         src: tuple[int, int] | None = None, timeout: float = 60.0) -> bool:
    """Decode the frame at global time ``t`` once and write every requested JPEG from it."""
    info = ffmpeg.require()
    args = [info.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{max(0.0, t):.3f}", "-i", str(video)]
    for o in outs:
        o.path.parent.mkdir(parents=True, exist_ok=True)
        vf = _scale_filter(o, src)
        if o.label is not None and font is not None:
            text = o.label.replace("\\", "\\\\").replace(":", "\\:").replace("'", "")
            vf += (f",drawtext={font}text='{text}':fontsize=22:fontcolor=white:box=1:boxcolor=black@0.65:"
                   "boxborderw=5:x=6:y=h-th-10")
        args += ["-map", "0:v:0", "-frames:v", "1", "-vf", vf, "-q:v", str(o.qscale), str(o.path)]
    res = proc.run(args, timeout=timeout)
    return res.ok and all(o.path.exists() and o.path.stat().st_size > 0 for o in outs)


def drawtext_font() -> str | None:
    """A drawtext font argument usable on this machine, or None (labels are then skipped)."""
    import os
    import platform

    info = ffmpeg.require()
    if not info.filters.get("drawtext"):
        return None
    candidates = {
        "Windows": [os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", n) for n in ("arial.ttf", "segoeui.ttf")],
        "Darwin": ["/System/Library/Fonts/Supplemental/Arial.ttf", "/Library/Fonts/Arial.ttf",
                   "/System/Library/Fonts/Helvetica.ttc"],
    }.get(platform.system(), ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans.ttf",
                              "/usr/share/fonts/TTF/DejaVuSans.ttf",
                              "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"])
    for c in candidates:
        if os.path.exists(c):
            return "fontfile='" + c.replace("\\", "/").replace(":", "\\:") + "':"
    return "" if platform.system() == "Linux" else None  # Linux: fontconfig default font


def ts_label(t: float) -> str:
    return fmt_ts(t)
