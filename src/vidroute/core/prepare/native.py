"""Native routes (D1 ``native-av``, D2 ``native-v``): the video itself, within the model's per-request limits.

The plan already chose copy / remux / transcode and the segment boundaries
(``plan.delivery.native``). Here the files are produced and *checked*: every
file must fit ``max_file_mb``. An encode that comes out too big is redone at a
proportionally lower bitrate (up to two more passes); if it still does not
fit, :class:`DoesNotFit` tells prepare to fall back to the frames route.
``segments.json`` keeps the global offset of every part, so timestamps the
model reports inside a part can be mapped back to the whole video.
"""

from __future__ import annotations

import math
import shutil
from pathlib import Path
from typing import Any

from vidroute.core.context import Ctx
from vidroute.core.errors import RunFailed
from vidroute.core.prepare.layout import Layout, reset_dir
from vidroute.core.util import ffmpeg, proc
from vidroute.core.util.jsonio import write_json_atomic
from vidroute.core.util.timefmt import fmt_ts

SEGMENTS_SCHEMA = "vidroute/segments/1"
MAX_PASSES = 3
FIT_MARGIN = 0.97  # a file "fits" below 97 % of the limit (container overhead, MB vs MiB)


class DoesNotFit(RunFailed):
    default_code = "native_does_not_fit"


def _mb(path: Path) -> float:
    return path.stat().st_size / 1e6


def video_encoder(info: ffmpeg.FFmpegInfo, preferred: str) -> tuple[str, str]:
    """The encoder to use and its constant-quality option: libx264 (CRF), else OpenH264 or MPEG-4 Part 2."""
    if info.encoders.get(preferred) or preferred not in info.encoders:
        return preferred, "crf"
    for name in ("libopenh264", "mpeg4"):
        if info.encoders.get(name):
            return name, "q"
    return preferred, "crf"


def scale_filter(src: tuple[int, int], short_side: int) -> str | None:
    """Scale so the shorter side is ``short_side`` (even dimensions); never upscale."""
    w, h = src
    if not w or not h or min(w, h) <= short_side:
        return None
    f = short_side / float(min(w, h))
    return f"scale={max(2, int(w * f) // 2 * 2)}:{max(2, int(h * f) // 2 * 2)}"


def encode(video: Path, out: Path, start: float, end: float, tc: dict[str, Any], src: tuple[int, int],
           src_fps: float | None, ctx: Ctx, kbps_scale: float = 1.0) -> Path:
    info = ffmpeg.require()
    codec, style = video_encoder(info, tc.get("video_codec") or "libx264")
    vf = [x for x in (scale_filter(src, int(tc["height"])),) if x]
    if src_fps and src_fps > float(tc["fps"]) + 0.01:
        vf.append(f"fps={tc['fps']}")
    tmp = out.with_name(out.stem + ".tmp" + out.suffix)
    args = [info.ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    if start > 0:
        args += ["-ss", f"{start:.3f}"]
    args += ["-i", str(video), "-t", f"{end - start:.3f}", "-map", "0:v:0"]
    audio_kbps = tc.get("audio_kbps")
    args += ["-map", "0:a:0?"] if audio_kbps else ["-an"]
    args += ["-sn", "-dn"]
    if vf:
        args += ["-vf", ",".join(vf)]
    args += ["-c:v", codec, "-pix_fmt", "yuv420p"]
    if codec == "libx264":
        args += ["-preset", "veryfast"]
    kbps = tc.get("video_kbps")
    if kbps:
        k = max(60, int(kbps * kbps_scale))
        args += ["-b:v", f"{k}k", "-maxrate", f"{int(k * 1.25)}k", "-bufsize", f"{k * 2}k"]
    elif style == "crf":
        args += ["-crf", str(tc.get("crf") or 28)]
    else:
        args += ["-q:v", "5"]
    if audio_kbps:
        args += ["-c:a", tc.get("audio_codec") or "aac", "-b:a", f"{max(16, int(audio_kbps * min(1.0, kbps_scale)))}k",
                 "-ac", "1"]
    args += ["-movflags", "+faststart", str(tmp)]
    ctx.check_cancelled()
    res = proc.run(args, timeout=max(600.0, (end - start) * 3))
    if not res.ok or not tmp.exists():
        tmp.unlink(missing_ok=True)
        raise RunFailed(f"转码失败：{(res.stderr or '超时').strip()[-300:]}", code="ffmpeg_failed")
    tmp.replace(out)
    return out


def remux(video: Path, out: Path, drop_audio: bool, ctx: Ctx) -> Path:
    info = ffmpeg.require()
    tmp = out.with_name(out.stem + ".tmp" + out.suffix)
    args = [info.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(video), "-map", "0:v:0"]
    args += ["-an"] if drop_audio else ["-map", "0:a:0?"]
    args += ["-sn", "-dn", "-c", "copy", "-movflags", "+faststart", str(tmp)]
    ctx.check_cancelled()
    res = proc.run(args, timeout=1800)
    if not res.ok or not tmp.exists():
        tmp.unlink(missing_ok=True)
        raise RunFailed(f"重新封装失败：{(res.stderr or '超时').strip()[-300:]}", code="ffmpeg_failed")
    tmp.replace(out)
    return out


def produce(video: Path, layout: Layout, plan: dict[str, Any], info: dict[str, Any], overlap: float,
            ctx: Ctx) -> dict[str, Any]:
    """Write ``native/`` and ``segments.json``; returns the parts with their global spans."""
    route = plan["route"]
    nat = plan["delivery"]["native"]
    tc = nat.get("transcode")
    limit = (plan["effective"].get("video_limits") or {}).get("max_file_mb")
    v = info.get("video") or {}
    src = (int(v.get("width") or 0), int(v.get("height") or 0))
    duration = float(info["duration"] or 0)
    out_dir = reset_dir(layout.native, layout)
    parts: list[dict[str, Any]] = []
    method = "copy"
    if tc is None:
        dst = out_dir / f"video{video.suffix.lower() or '.mp4'}"
        shutil.copy2(video, dst)
        parts.append({"id": 1, "path": dst, "start": 0.0, "end": duration})
    elif tc.get("mode") == "copy":
        method = "去掉音频后重新封装" if tc.get("drop_audio") else "重新封装"
        dst = remux(video, out_dir / "video.mp4", bool(tc.get("drop_audio")), ctx)
        parts.append({"id": 1, "path": dst, "start": 0.0, "end": duration})
    else:
        segs = nat["segments"]
        method = f"H.264 {tc['height']}p@{tc['fps']}fps" + (f" {tc['video_kbps']} kbps" if tc.get("video_kbps") else "")
        for i, (s, e) in enumerate(segs, 1):
            name = "video.mp4" if len(segs) == 1 else f"part-{i:02d}.mp4"
            dst = out_dir / name
            scale = 1.0
            for attempt in range(1, MAX_PASSES + 1):
                encode(video, dst, float(s), float(e), tc, src, v.get("fps"), ctx, kbps_scale=scale)
                size = _mb(dst)
                if not limit or size <= float(limit) * FIT_MARGIN:
                    break
                if attempt == MAX_PASSES or not tc.get("video_kbps"):
                    raise DoesNotFit(f"{name} 经过 {attempt} 遍后为 {size:.1f} MB；限额为 {limit} MB")
                scale *= float(limit) * 0.9 / size
                ctx.progress("transcode", i, len(segs), f"{name}：{size:.1f} MB > {limit} MB，正在重新编码以缩小")
            parts.append({"id": i, "path": dst, "start": float(s), "end": float(e), "passes": attempt})
            ctx.progress("transcode", i, len(segs))
    for p in parts:
        size = _mb(p["path"])
        if limit and size > float(limit) * FIT_MARGIN:
            raise DoesNotFit(f"{p['path'].name} 为 {size:.1f} MB；限额为 {limit} MB")
        p.update(file=layout.rel(p["path"]), mb=round(size, 2), bytes=p["path"].stat().st_size)
    data = {
        "schema": SEGMENTS_SCHEMA,
        "route": route,
        "includes_audio": route == "native-av" and bool(info.get("has_audio")),
        "method": method,
        "max_file_mb": limit,
        "overlap_seconds": overlap if len(parts) > 1 else 0.0,
        "note": "每段内的时间从 0 开始。全局时间 = offset_sec + 段内时间。相邻分段"
                "有重叠；每个事件只报告一次，并使用全局时间。",
        "segments": [{"id": p["id"], "file": p["file"], "start": round(p["start"], 3), "end": round(p["end"], 3),
                      "offset_sec": round(p["start"], 3), "duration": round(p["end"] - p["start"], 3),
                      "mb": p["mb"]} for p in parts],
    }
    write_json_atomic(layout.file("segments.json"), data)
    return {"parts": [{k: v for k, v in p.items() if k != "path"} for p in parts], "method": method,
            "segments": data["segments"], "includes_audio": data["includes_audio"]}


def describe(parts: list[dict[str, Any]]) -> str:
    if len(parts) == 1:
        p = parts[0]
        return f"{p['file']} ({p['mb']} MB, {fmt_ts(p['end'] - p['start'])})"
    total = sum(p["mb"] for p in parts)
    return f"{len(parts)} 段，共 {total:.1f} MB，最大 {max(p['mb'] for p in parts)} MB"


def video_tokens_per_part(plan: dict[str, Any], parts: list[dict[str, Any]]) -> list[int]:
    nat = plan["delivery"]["native"]
    total = float(nat.get("est_video_tokens") or 0)
    seconds = sum(p["end"] - p["start"] for p in parts) or 1.0
    return [int(math.ceil(total * (p["end"] - p["start"]) / seconds)) for p in parts]
