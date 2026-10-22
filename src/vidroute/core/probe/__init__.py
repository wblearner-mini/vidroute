"""``vidroute probe``: fast video facts for routing (target < 10 s).

ffprobe for streams/duration/size/embedded subtitles; VAD on a few sampled
windows for ``speech_ratio``; 8 tiny grayscale frames for ``visual_change`` and
``text_density``. Results are cached per (path, size, mtime).
"""

from __future__ import annotations

import os
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
from pathlib import Path
from typing import Any

from vidroute.core import paths
from vidroute.core.context import Ctx
from vidroute.core.errors import NotFoundError, UsageError
from vidroute.core.probe import frames as fr
from vidroute.core.util import ffmpeg, proc
from vidroute.core.util.jsonio import read_json, write_json_atomic

PROBE_VERSION = 3
TEXT_SUB_CODECS = {"subrip", "srt", "ass", "ssa", "mov_text", "webvtt", "text", "microdvd", "subviewer", "jacosub", "sami"}
IMAGE_SUB_CODECS = {"hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle", "xsub"}
SAMPLE_FRAMES = 8
SPEECH_WINDOWS = 6
SPEECH_WINDOW_SECONDS = 20.0


def _ratio(text: str | None) -> float | None:
    if not text or text in ("0/0", "N/A"):
        return None
    try:
        val = float(Fraction(text))
    except (ValueError, ZeroDivisionError):
        return None
    return round(val, 3) if val > 0 else None


def parse_ffprobe(data: dict[str, Any], path: Path) -> dict[str, Any]:
    fmt = data.get("format") or {}
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not (s.get("disposition") or {}).get("attached_pic")), None)
    audios = [s for s in streams if s.get("codec_type") == "audio"]
    subs = [s for s in streams if s.get("codec_type") == "subtitle"]
    durations = [float(fmt["duration"])] if fmt.get("duration") not in (None, "N/A") else []
    durations += [float(s["duration"]) for s in streams if s.get("duration") not in (None, "N/A")]
    duration = max(durations) if durations else 0.0
    size = int(fmt.get("size") or path.stat().st_size)
    v: dict[str, Any] | None = None
    if video:
        w, h = int(video.get("width") or 0), int(video.get("height") or 0)
        rotation = 0
        for side in video.get("side_data_list") or []:
            if "rotation" in side:
                rotation = int(side["rotation"])
        tags_rot = (video.get("tags") or {}).get("rotate")
        if tags_rot:
            rotation = int(tags_rot)
        if abs(rotation) in (90, 270):
            w, h = h, w
        v = {
            "codec": video.get("codec_name"),
            "width": w,
            "height": h,
            "fps": _ratio(video.get("avg_frame_rate")) or _ratio(video.get("r_frame_rate")),
            "pix_fmt": video.get("pix_fmt"),
            "rotation": rotation,
            "bit_rate": int(video["bit_rate"]) if str(video.get("bit_rate", "")).isdigit() else None,
        }
    tracks = []
    for i, s in enumerate(subs):
        codec = s.get("codec_name") or ""
        tags = s.get("tags") or {}
        tracks.append({
            "index": s.get("index"),
            "sub_index": i,
            "codec": codec,
            "language": tags.get("language"),
            "title": tags.get("title"),
            "text_based": codec in TEXT_SUB_CODECS,
            "image_based": codec in IMAGE_SUB_CODECS,
        })
    return {
        "path": str(path),
        "name": path.name,
        "duration": round(duration, 3),
        "size_bytes": size,
        "size_mb": round(size / 1e6, 2),
        "format": fmt.get("format_name"),
        "bit_rate": int(fmt["bit_rate"]) if str(fmt.get("bit_rate", "")).isdigit() else None,
        "video": v,
        "has_video": v is not None,
        "has_audio": bool(audios),
        "audio": [{"codec": a.get("codec_name"), "channels": a.get("channels"),
                   "sample_rate": int(a["sample_rate"]) if str(a.get("sample_rate", "")).isdigit() else None,
                   "language": (a.get("tags") or {}).get("language")} for a in audios],
        "subtitles": tracks,
        "text_subtitles": [t for t in tracks if t["text_based"]],
        "chapters": len(data.get("chapters") or []),
    }


def sample_times(duration: float, n: int = SAMPLE_FRAMES) -> list[float]:
    if duration <= 0:
        return [0.0]
    return [round(duration * (i + 0.5) / n, 3) for i in range(n)]


def grab_gray(ffmpeg_exe: str, video: Path, t: float, w: int = fr.W, h: int = fr.H) -> bytes:
    """One letterboxed grayscale frame at ``t`` as raw bytes (w*h)."""
    vf = f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,format=gray"
    code, out, _ = proc.run_bytes([ffmpeg_exe, "-hide_banner", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", str(video),
                                   "-frames:v", "1", "-vf", vf, "-f", "rawvideo", "-"], timeout=30)
    return out if code == 0 else b""


def _ocr_engine():
    from vidroute.core.engines import get_engine

    for mid in ("ocr-ppocrv6-small", "ocr-ppocrv6-tiny"):
        try:
            eng = get_engine(mid)
        except Exception:  # noqa: BLE001
            continue
        if eng.probe()["installed"]:
            return eng
    return None


def picture_stats(video: Path, duration: float, ctx: Ctx | None = None) -> dict[str, Any]:
    """visual_change from tiny gray frames; text_density from OCR detection when installed."""
    ctx = ctx or Ctx()
    info = ffmpeg.require()
    times = sample_times(duration)
    with ThreadPoolExecutor(max_workers=min(8, len(times))) as pool:
        frames = list(pool.map(lambda t: grab_gray(info.ffmpeg, video, t), times))
    stats = fr.frame_stats(frames)
    stats["sample_times"] = times
    stats["method"] = "heuristic:block-contrast-edges"
    engine = _ocr_engine()
    if engine is not None:
        try:
            from vidroute.core.engines import WorkerPool

            with tempfile.TemporaryDirectory(prefix="vidroute-probe-") as tmp:
                jpgs = []
                for i, t in enumerate(times):
                    out = Path(tmp) / f"f{i}.jpg"
                    res = proc.run([info.ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", f"{t:.3f}", "-i",
                                    str(video), "-frames:v", "1", "-vf", "scale=640:-2", "-q:v", "4", str(out)],
                                   timeout=30)
                    if res.ok and out.exists():
                        jpgs.append(out)
                with WorkerPool(ctx) as wp:
                    det = engine.detect_text(ctx, wp, jpgs)
            ocr_stats = fr.text_stats_from_boxes(det)
            stats.update(text_density=ocr_stats["text_density"], text_area=ocr_stats["text_area"],
                         method=f"ocr-detect:{engine.id}", heuristic_text_density=stats["text_density"])
        except Exception as exc:  # noqa: BLE001 - fall back to the heuristic
            ctx.warn("ocr_probe_failed", f"OCR text detection failed during probe ({exc}); using the heuristic.")
    return stats


def speech_windows(duration: float) -> list[list[float]]:
    if duration <= SPEECH_WINDOWS * SPEECH_WINDOW_SECONDS * 1.5:
        return [[0.0, round(duration, 3)]]
    wins = []
    for i in range(SPEECH_WINDOWS):
        center = duration * (i + 0.5) / SPEECH_WINDOWS
        s = max(0.0, center - SPEECH_WINDOW_SECONDS / 2)
        wins.append([round(s, 3), round(min(duration, s + SPEECH_WINDOW_SECONDS), 3)])
    return wins


def _extract_windows_wav(video: Path, windows: list[list[float]], out: Path) -> list[list[float]]:
    """Write the windows back to back into one 16 kHz mono WAV; returns their offsets in that file."""
    info = ffmpeg.require()
    args = [info.ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    for s, e in windows:
        args += ["-ss", f"{s:.3f}", "-t", f"{e - s:.3f}", "-i", str(video)]
    n = len(windows)
    graph = "".join(f"[{i}:a:0]" for i in range(n)) + f"concat=n={n}:v=0:a=1,aresample=16000,aformat=channel_layouts=mono[a]"
    args += ["-filter_complex", graph, "-map", "[a]", "-c:a", "pcm_s16le", str(out)]
    res = proc.run(args, timeout=120)
    if not res.ok:
        raise RuntimeError((res.stderr or "ffmpeg failed")[-300:])
    offsets = []
    cursor = 0.0
    for s, e in windows:
        offsets.append([round(cursor, 3), round(cursor + (e - s), 3)])
        cursor += e - s
    return offsets


def speech_stats(video: Path, duration: float, has_audio: bool, ctx: Ctx) -> dict[str, Any]:
    if not has_audio or duration <= 0:
        return {"speech_ratio": 0.0, "method": "no-audio", "windows": []}
    windows = speech_windows(duration)
    covered = sum(e - s for s, e in windows)
    from vidroute.core.engines import WorkerPool, get_engine

    vad = None
    try:
        silero = get_engine("vad-silero")
        if silero.probe()["installed"]:
            vad = silero
    except Exception:  # noqa: BLE001 - fall back to energy VAD
        vad = None
    if vad is not None:
        try:
            with tempfile.TemporaryDirectory(prefix="vidroute-probe-") as tmp:
                wav = Path(tmp) / "windows.wav"
                _extract_windows_wav(video, windows, wav)
                with WorkerPool(ctx) as pool:
                    segs = vad.detect(ctx, pool, wav)
            speech = sum(e - s for s, e in segs)
            return {"speech_ratio": round(min(1.0, speech / covered), 3) if covered else 0.0,
                    "method": "silero-vad", "windows": windows, "speech_seconds_sampled": round(speech, 2)}
        except Exception as exc:  # noqa: BLE001
            ctx.warn("vad_probe_failed", f"Silero VAD failed during probe ({exc}); using energy detection.")
    from vidroute.core.engines.vad import parse_silencedetect

    info = ffmpeg.require()
    sound = 0.0
    for s, e in windows:
        res = proc.run([info.ffmpeg, "-hide_banner", "-nostats", "-ss", f"{s:.3f}", "-t", f"{e - s:.3f}", "-i", str(video),
                        "-vn", "-af", "silencedetect=noise=-35dB:d=0.4", "-f", "null", "-"], timeout=60)
        sound += sum(b - a for a, b in parse_silencedetect(res.stderr, e - s))
    return {"speech_ratio": round(min(1.0, sound / covered), 3) if covered else 0.0,
            "method": "energy (any sound counts; install vad-silero for real speech detection)",
            "windows": windows}


def _cache_path(video: Path) -> Path:
    return paths.cache_dir() / "probe" / f"{paths.video_slug(video)}.json"


def probe(video: str | os.PathLike[str], ctx: Ctx | None = None, refresh: bool = False) -> dict[str, Any]:
    ctx = ctx or Ctx()
    path = Path(video).expanduser()
    if not path.exists():
        raise NotFoundError(f"Video not found: {path}")
    if not path.is_file():
        raise UsageError(f"Not a file: {path}")
    path = path.resolve()
    cache = _cache_path(path)
    if not refresh:
        cached = read_json(cache)
        if isinstance(cached, dict) and cached.get("probe_version") == PROBE_VERSION:
            cached["cached"] = True
            return cached
    t0 = time.monotonic()
    ctx.progress("probe", None, None, "reading streams")
    info = parse_ffprobe(ffmpeg.ffprobe_json(path), path)
    if not info["has_video"] and not info["has_audio"]:
        raise UsageError(f"{path.name} has neither video nor audio streams")
    duration = info["duration"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        pic = pool.submit(picture_stats, path, duration, ctx) if info["has_video"] else None
        ctx.progress("probe", None, None, "sampling speech")
        speech = speech_stats(path, duration, info["has_audio"], ctx)
        pictures = pic.result() if pic else {"frames": 0, "visual_change": 0.0, "text_density": 0.0, "text_area": 0.0}
    info.update({
        "speech_ratio": speech["speech_ratio"],
        "speech": speech,
        "text_density": pictures["text_density"],
        "visual_change": pictures["visual_change"],
        "pictures": pictures,
        "probe_seconds": round(time.monotonic() - t0, 2),
        "probe_version": PROBE_VERSION,
        "cached": False,
    })
    write_json_atomic(cache, info)
    return info
