"""Keyframes (R5): one cheap decoding pass, change-based shots, dHash dedup, a frame budget.

1. Candidates: tiny grayscale thumbnails (64 px on the long side) from one
   ffmpeg pass. Keyframes-only decoding is tried first (very fast); when the
   video's keyframes are too sparse the whole stream is decoded at a rate that
   keeps candidates under ``max_candidates`` (at most 4 per second).
2. Shots: a candidate starts a new shot when any 4x4 cell of its thumbnail
   differs visibly (``change_threshold``) from the previous candidate or from
   the shot's first candidate. This catches hard cuts, slide changes, bullet
   builds and slow drifts alike, while compression noise does not count.
3. Dedup: a shot that shows a picture already seen (no visible cell change)
   is folded into the earlier shot (A-B-A slide decks, returning to the same
   screen). Each candidate carries a perceptual signature — the mean of every
   cell — whose largest difference is a lower bound of the cell change, so it
   rules out most pairs cheaply without ever missing a true twin (a dHash
   would: on flat pictures its bits are decided by noise).
4. Budget: at most ``target`` frames. The timeline is split into ``target``
   equal bins (the "grid"); every bin keeps its longest-on-screen shot and
   spare budget goes to the next longest shots. Frames closer than
   ``min_interval_seconds`` are merged.
5. The chosen moments are grabbed at full resolution in parallel, together
   with labelled thumbnails for the contact sheets and, for OCR, a sharper
   copy.
"""

from __future__ import annotations

import math
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from vidroute.core.context import Ctx
from vidroute.core.errors import RunFailed
from vidroute.core.prepare import media
from vidroute.core.util import ffmpeg, proc
from vidroute.core.util.timefmt import fmt_compact, fmt_ts

SMALL = 64  # long side of the analysis thumbnails
CELL = 4  # change detection compares CELL x CELL cells of a thumbnail
MAX_RATE = 4.0  # candidate frames per second, at most
_PTS = re.compile(r"pts_time:\s*(-?[\d.]+)")


@dataclass
class Candidate:
    t: float
    px: bytes
    sig: bytes = b""  # cell means: the perceptual signature


@dataclass
class Shot:
    first: int  # index of the first candidate of the shot
    last: int
    start: float
    end: float = 0.0
    change: float = 0.0  # cell difference to the previous shot (0-255)
    repeats: list[list[float]] = field(default_factory=list)  # later spans that show the same picture
    t: float = 0.0  # grab time: the middle candidate, away from fades and motion blur at the edges

    @property
    def length(self) -> float:
        return max(0.0, self.end - self.start) + sum(e - s for s, e in self.repeats)


def small_size(width: int | None, height: int | None) -> tuple[int, int]:
    """Analysis thumbnail size: 64 px on the long side, multiples of CELL, aspect kept."""
    w, h = width or 16, height or 9
    if w >= h:
        return SMALL, max(CELL * 4, round(SMALL * h / w / CELL) * CELL)
    return max(CELL * 4, round(SMALL * w / h / CELL) * CELL), SMALL


def signature(px: bytes, w: int, h: int) -> bytes:
    """Mean of every CELL x CELL cell (0-255)."""
    out = bytearray()
    for cy in range(0, h - CELL + 1, CELL):
        for cx in range(0, w - CELL + 1, CELL):
            total = 0
            for y in range(cy, cy + CELL):
                o = y * w + cx
                total += sum(px[o:o + CELL])
            out.append(total // (CELL * CELL))
    return bytes(out)


def sig_gap(a: bytes, b: bytes) -> int:
    """Largest cell-mean difference; never exceeds :func:`cell_change` (|mean(a-b)| <= mean|a-b|) up to rounding."""
    return max((abs(x - y) for x, y in zip(a, b)), default=0)


def cell_change(a: bytes, b: bytes, w: int, h: int) -> float:
    """Largest mean absolute difference of any CELL x CELL cell (0-255)."""
    best = 0
    for cy in range(0, h - CELL + 1, CELL):
        for cx in range(0, w - CELL + 1, CELL):
            total = 0
            for y in range(cy, cy + CELL):
                o = y * w + cx
                for i in range(o, o + CELL):
                    d = a[i] - b[i]
                    total += d if d >= 0 else -d
            if total > best:
                best = total
    return best / (CELL * CELL)


# ------------------------------------------------------------------ sampling


def _decode(video: Path, pre: list[str], vf: str, size: tuple[int, int], ctx: Ctx, start: float,
            span: float, timeout: float, stage: str, limit: int | None = None) -> tuple[list[Candidate], bool]:
    """Stream tiny gray frames out of ffmpeg; times come from showinfo (global seconds).

    Returns the candidates and whether reading stopped at ``limit`` frames.
    """
    info = ffmpeg.require()
    w, h = size
    frame_bytes = w * h
    args = [info.ffmpeg, "-hide_banner", "-nostats", "-loglevel", "info", *pre]
    if start > 0:
        args += ["-ss", f"{start:.3f}"]
    args += ["-i", str(video)]
    if span > 0:
        args += ["-t", f"{span:.3f}"]
    args += ["-map", "0:v:0", "-an", "-sn", "-dn", "-vf", vf, "-fps_mode", "passthrough", "-f", "rawvideo",
             "-pix_fmt", "gray", "-"]
    p = proc.popen(args)
    err_lines: list[str] = []

    def drain() -> None:
        assert p.stderr is not None
        for raw in iter(p.stderr.readline, b""):
            err_lines.append(raw.decode("utf-8", "replace"))

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    frames: list[bytes] = []
    stop = ""
    deadline = time.monotonic() + timeout
    try:
        assert p.stdout is not None
        while True:
            buf = p.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            frames.append(buf)
            if limit is not None and len(frames) >= limit:
                stop = "limit"
                break
            if len(frames) % 25 == 0:
                if ctx.cancelled:
                    stop = "cancelled"
                    break
                if time.monotonic() > deadline:
                    stop = "timeout"
                    break
                ctx.progress(stage, None, None, f"{len(frames)} candidate frames")
    finally:
        if stop:
            p.kill()
        p.wait()
        reader.join(timeout=5)
    ctx.check_cancelled()
    if stop == "timeout":
        raise RunFailed(f"Frame sampling timed out after {timeout:.0f} s", code="ffmpeg_timeout")
    err = "".join(err_lines)
    if p.returncode != 0 and not frames and not stop:
        raise RunFailed(f"Frame sampling failed: {err.strip()[-300:]}", code="ffmpeg_failed")
    times = [float(m.group(1)) for m in _PTS.finditer(err)][:len(frames)]
    if len(times) != len(frames):  # showinfo should print one line per frame; fall back to an even grid
        step = span / max(1, len(frames))
        times = [i * step for i in range(len(frames))]
    return [Candidate(round(start + t, 3), px, signature(px, w, h)) for t, px in zip(times, frames)], stop == "limit"


def sample(video: Path, duration: float, size: tuple[int, int], rules: dict[str, Any], ctx: Ctx,
           start: float = 0.0, end: float | None = None) -> tuple[list[Candidate], dict[str, Any]]:
    """Candidate thumbnails for [start, end): keyframes when dense enough, else a decoded grid."""
    f = rules["frames"]
    max_c = int(f["max_candidates"])
    end = duration if end is None else min(end, duration)
    span = max(0.0, end - start)
    want = max(1.0 / MAX_RATE, span / max(1, max_c))  # seconds between candidates
    w, h = size
    base_vf = f"scale={w}:{h}:flags=area,format=gray,showinfo"
    timeout = max(300.0, span * 1.5)
    # Keyframes only: very fast. Stops early when a decoder ignores the request or every frame is a keyframe.
    kf, truncated = _decode(video, ["-skip_frame", "nokey"], base_vf, size, ctx, start, span, timeout, "keyframes",
                            limit=4 * max_c)
    gaps = [b.t - a.t for a, b in zip(kf, kf[1:])]
    if kf:
        gaps += [kf[0].t - start, end - kf[-1].t]
    long_ok = float(f["max_interval_seconds"]) if span > 1800 else 0.0
    if kf and not truncated and max(gaps) <= max(2.5 * want, 2.0, long_ok):
        if len(kf) > 3 * max_c:  # keep at most ~3x max_candidates, evenly in time
            stride = len(kf) / float(3 * max_c)
            kf = [kf[int(i * stride)] for i in range(3 * max_c)]
        return kf, {"method": "keyframes", "candidates": len(kf), "interval": round(span / max(1, len(kf)), 2)}
    rate = 1.0 / want
    vf = f"fps={rate:.6f}," + base_vf
    cands, _ = _decode(video, ["-skip_loop_filter", "all"], vf, size, ctx, start, span, timeout, "keyframes")
    if not cands:
        raise RunFailed("No video frames could be decoded", code="no_frames")
    return cands, {"method": f"decoded@{rate:.3g}fps", "candidates": len(cands), "interval": round(want, 2),
                   "keyframes_seen": len(kf), "keyframes_truncated": truncated}


# --------------------------------------------------------------------- shots


def _change(a: Candidate, b: Candidate, size: tuple[int, int], threshold: float) -> float:
    gap = sig_gap(a.sig, b.sig)
    if gap > threshold + 1:  # the signature alone proves a visible change (+1: integer rounding)
        return float(gap)
    return cell_change(a.px, b.px, size[0], size[1])


def shots_from(cands: list[Candidate], size: tuple[int, int], threshold: float, end: float) -> list[Shot]:
    shots: list[Shot] = []
    for i, c in enumerate(cands):
        if not shots:
            shots.append(Shot(i, i, c.t, change=255.0))
            continue
        cur = shots[-1]
        d_prev = _change(c, cands[i - 1], size, threshold)
        d_first = d_prev if cur.first == i - 1 else _change(c, cands[cur.first], size, threshold)
        if max(d_prev, d_first) > threshold:
            cur.end = c.t
            shots.append(Shot(i, i, c.t, change=round(max(d_prev, d_first), 1)))
        else:
            cur.last = i
    if shots:
        shots[-1].end = max(end, cands[shots[-1].last].t)
    for s in shots:
        s.t = cands[rep_index(s)].t
    return shots


def dedupe(shots: list[Shot], cands: list[Candidate], size: tuple[int, int], threshold: float) -> list[Shot]:
    """Fold shots that repeat an earlier picture into that earlier shot."""
    kept: list[Shot] = []
    for s in shots:
        rep = cands[rep_index(s)]
        twin = None
        for k in kept:
            if _change(rep, cands[rep_index(k)], size, threshold) <= threshold:
                twin = k
                break
        if twin is None:
            kept.append(s)
        else:
            twin.repeats.append([s.start, s.end])
    return kept


def rep_index(s: Shot) -> int:
    return (s.first + s.last) // 2


def choose(shots: list[Shot], target: int, start: float, end: float, min_gap: float) -> list[Shot]:
    """Coverage first (the longest shot of each of ``target`` equal time bins), then the longest of the rest.

    Picks are taken greedily, longest first, so the budget is filled even when
    two candidates fall closer than ``min_gap`` (the shorter one is skipped).
    """
    span = max(1e-6, end - start)
    by_bin: dict[int, Shot] = {}
    for s in shots:
        b = min(target - 1, max(0, int((s.start - start) / span * target)))
        if b not in by_bin or s.length > by_bin[b].length:
            by_bin[b] = s
    winners = sorted(by_bin.values(), key=lambda s: -s.length)
    rest = sorted((s for s in shots if not any(s is w for w in winners)), key=lambda s: -s.length)
    picked: list[Shot] = []
    for s in winners + rest:
        if len(picked) >= target:
            break
        if all(abs(s.t - p.t) >= min_gap for p in picked):
            picked.append(s)
    return sorted(picked, key=lambda s: s.t)


@dataclass
class Keyframe:
    index: int
    t: float  # grab time (global seconds)
    start: float
    end: float
    change: float
    repeats: list[list[float]]
    file: str = ""
    thumb: str = ""
    ocr_file: str = ""


def select(cands: list[Candidate], size: tuple[int, int], rules: dict[str, Any], target: int, start: float,
           end: float) -> tuple[list[Keyframe], dict[str, Any]]:
    f = rules["frames"]
    threshold = float(f["change_threshold"])
    shots = shots_from(cands, size, threshold, end)
    unique = dedupe(shots, cands, size, threshold)
    chosen = choose(unique, max(1, target), start, end, float(f["min_interval_seconds"]))
    frames = [Keyframe(i + 1, s.t, round(s.start, 3), round(s.end, 3), s.change,
                       [[round(a, 3), round(b, 3)] for a, b in s.repeats]) for i, s in enumerate(chosen)]
    stats = {"shots": len(shots), "unique_shots": len(unique), "selected": len(frames), "target": target,
             "repeats_folded": len(shots) - len(unique)}
    return frames, stats


# ------------------------------------------------------------------ grabbing


def frame_name(k: Keyframe) -> str:
    return f"{k.index:03d}_{fmt_compact(k.t)}.jpg"


def grab_all(video: Path, frames: list[Keyframe], out_dir: Path, thumbs_dir: Path | None, ocr_dir: Path | None,
             rules: dict[str, Any], src_size: tuple[int, int], ctx: Ctx, label: bool = True) -> dict[str, Any]:
    """Full-resolution JPEGs (+ labelled thumbnails, + sharper OCR copies) for the chosen moments."""
    f = rules["frames"]
    cs = f["contact_sheet"]
    max_side = int(f["frame_max_side_px"])
    ocr_side = int(f["ocr_frame_max_side_px"])
    sw, sh = src_size
    src = src_size if sw and sh else None
    tile = int(cs["tile_width"])
    thumb_box = (tile, tile)  # fit inside a square: landscape 400xN, portrait Nx400
    font = media.drawtext_font() if (label and cs.get("label", True) and thumbs_dir is not None) else None
    use_ocr_copy = ocr_dir is not None and max(sw, sh) > max_side
    out_dir.mkdir(parents=True, exist_ok=True)
    state = {"labels": font is not None, "failed": []}

    def one(k: Keyframe) -> None:
        if ctx.cancelled:
            return
        name = frame_name(k)
        outs = [media.FrameOut(out_dir / name, max_side=max_side, qscale=int(f["jpeg_qscale"]))]
        if thumbs_dir is not None:
            outs.append(media.FrameOut(thumbs_dir / f"{k.index:04d}.jpg", box=thumb_box, qscale=4,
                                       label=fmt_ts(k.t) if state["labels"] else None))
        if use_ocr_copy:
            assert ocr_dir is not None
            outs.append(media.FrameOut(ocr_dir / name, max_side=ocr_side, qscale=2))
        ok = media.grab(video, k.t, outs, font=font if state["labels"] else None, src=src)
        if not ok and state["labels"] and len(outs) > 1:  # drawtext trouble: retry once without labels
            for o in outs:
                o.label = None
            ok = media.grab(video, k.t, outs, font=None, src=src)
            if ok:
                state["labels"] = False
        if not ok:  # a seek can land past the last frame: step back a little
            ok = media.grab(video, max(0.0, k.t - 0.5), outs, font=font if state["labels"] else None, src=src)
        if ok:
            k.file = name
            k.thumb = outs[1].path.name if thumbs_dir is not None else ""
            k.ocr_file = name if use_ocr_copy else ""
        else:
            state["failed"].append(k.index)

    workers = max(1, min(8, (os.cpu_count() or 2)))
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for _ in pool.map(one, frames):
            done += 1
            ctx.progress("frames", done, len(frames))
    ctx.check_cancelled()
    return {"labels": state["labels"], "failed": state["failed"], "ocr_copies": use_ocr_copy}


# ------------------------------------------------------------- contact sheets


def contact_sheets(frames: list[Keyframe], thumbs_dir: Path, out_dir: Path, rules: dict[str, Any],
                   ctx: Ctx) -> list[dict[str, Any]]:
    """Tile labelled thumbnails into sheets (cols x rows); the last sheet shrinks to fit."""
    info = ffmpeg.require()
    cs = rules["frames"]["contact_sheet"]
    cols, rows = int(cs["cols"]), int(cs["rows"])
    per = cols * rows
    usable = [k for k in frames if k.thumb and (thumbs_dir / k.thumb).exists()]
    out_dir.mkdir(parents=True, exist_ok=True)
    sheets: list[dict[str, Any]] = []
    n_sheets = math.ceil(len(usable) / per) if usable else 0
    for si in range(n_sheets):
        ctx.check_cancelled()
        group = usable[si * per:(si + 1) * per]
        c = min(cols, len(group))
        r = math.ceil(len(group) / c)
        work = thumbs_dir / f"sheet{si + 1:02d}"
        work.mkdir(parents=True, exist_ok=True)
        for old in work.glob("*.jpg"):
            old.unlink()
        for j, k in enumerate(group, 1):
            target = work / f"{j:02d}.jpg"
            target.write_bytes((thumbs_dir / k.thumb).read_bytes())
        name = f"sheet-{si + 1:02d}.jpg"
        res = proc.run([info.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-framerate", "1", "-start_number", "1",
                        "-i", str(work / "%02d.jpg"), "-vf", f"tile={c}x{r}:padding=6:margin=6:color=white",
                        "-frames:v", "1", "-q:v", "3", str(out_dir / name)], timeout=120)
        if not res.ok:
            raise RunFailed(f"Contact sheet failed: {res.stderr.strip()[-300:]}", code="ffmpeg_failed")
        sheets.append({"file": name, "frames": [k.index for k in group], "grid": [c, r],
                       "start": group[0].t, "end": group[-1].t,
                       "tiles": [{"tile": j, "row": (j - 1) // c + 1, "col": (j - 1) % c + 1, "frame": k.index,
                                  "t": k.t, "span": [k.start, k.end]} for j, k in enumerate(group, 1)]})
        ctx.progress("contact_sheets", si + 1, n_sheets)
    return sheets


def write_index(frames: list[Keyframe], sheets: list[dict[str, Any]], path: Path, labels: bool) -> None:
    lines = ["# Contact sheets", "",
             "Tiles are read left to right, top to bottom. "
             + ("Each tile is labelled with its global timestamp. " if labels else "Tiles carry no labels: use the table. ")
             + "Full-resolution frames are in `frames/`; open one when a tile is too small to read.", ""]
    by_index = {k.index: k for k in frames}
    for s in sheets:
        lines += [f"## {s['file']}  ({fmt_ts(s['start'])}–{fmt_ts(s['end'])}, {len(s['frames'])} frames)", "",
                  "| tile | time | on screen | frame |", "|---|---|---|---|"]
        for t in s["tiles"]:
            k = by_index[t["frame"]]
            span = f"{fmt_ts(k.start)}–{fmt_ts(k.end)}"
            if k.repeats:
                span += " (again " + ", ".join(f"{fmt_ts(a)}–{fmt_ts(b)}" for a, b in k.repeats[:3]) + ")"
            lines.append(f"| {t['tile']} (r{t['row']}c{t['col']}) | {fmt_ts(k.t)} | {span} | frames/{k.file} |")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
