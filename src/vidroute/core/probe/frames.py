"""Cheap picture statistics on tiny grayscale frames (pure Python, no numpy).

* ``visual_change``: mean absolute difference between consecutive sampled
  frames (0 = static, 1 = everything changes).
* ``text_density``: share of sampled frames that show text, using a block
  heuristic — a 16x16 block looks like text when it has strong contrast, a
  moderate density of sharp horizontal edges, and a bimodal histogram
  (glyph colour vs. background). It is an approximation used only to decide
  whether OCR is worth running (R4); OCR itself is the source of truth.
* dHash for near-duplicate detection.
"""

from __future__ import annotations

from typing import Iterable

W, H = 320, 180
BLOCK = 16
FRAME_TEXT_MIN_AREA = 0.04  # a frame "has text" when >= 4% of blocks look like text


def is_text_block(px: bytes, x0: int, y0: int, width: int = W) -> bool:
    vals = []
    edges = 0
    for y in range(y0, y0 + BLOCK):
        row = px[y * width + x0: y * width + x0 + BLOCK]
        vals.extend(row)
        prev = row[0]
        for v in row[1:]:
            if abs(v - prev) > 40:
                edges += 1
            prev = v
    lo, hi = min(vals), max(vals)
    contrast = hi - lo
    if contrast < 70:
        return False
    density = edges / (BLOCK * (BLOCK - 1))
    if density < 0.08 or density > 0.55:
        return False
    low_cut = lo + contrast * 0.3
    high_cut = hi - contrast * 0.3
    extreme = sum(1 for v in vals if v <= low_cut or v >= high_cut)
    return extreme / len(vals) >= 0.8


def text_area(px: bytes, width: int = W, height: int = H) -> float:
    blocks = 0
    texty = 0
    for y0 in range(0, height - BLOCK + 1, BLOCK):
        for x0 in range(0, width - BLOCK + 1, BLOCK):
            blocks += 1
            if is_text_block(px, x0, y0, width):
                texty += 1
    return texty / blocks if blocks else 0.0


def mean_abs_diff(a: bytes, b: bytes, step: int = 4) -> float:
    n = min(len(a), len(b))
    if n == 0:
        return 0.0
    total = 0
    count = 0
    for i in range(0, n, step):
        total += abs(a[i] - b[i])
        count += 1
    return total / (count * 255.0)


def dhash(px: bytes, width: int = W, height: int = H, size: int = 8) -> int:
    """Difference hash: area-average down to (size+1) x size, then compare horizontal neighbours."""
    cols = size + 1
    cells = []
    for gy in range(size):
        y0, y1 = gy * height // size, (gy + 1) * height // size
        row = []
        for gx in range(cols):
            x0, x1 = gx * width // cols, (gx + 1) * width // cols
            total = 0
            for y in range(y0, y1):
                base = y * width
                total += sum(px[base + x0: base + x1])
            row.append(total / max(1, (y1 - y0) * (x1 - x0)))
        cells.append(row)
    bits = 0
    for row in cells:
        for a, b in zip(row, row[1:]):
            bits = (bits << 1) | (1 if a > b else 0)
    return bits


def dhash_small(px: bytes, size: int = 8) -> int:
    """dHash of a frame already scaled to (size+1) x size by ffmpeg (``scale=9:8,format=gray``)."""
    return dhash(px, size + 1, size, size)


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def frame_stats(frames: Iterable[bytes], width: int = W, height: int = H) -> dict[str, float | int]:
    frames = [f for f in frames if f and len(f) >= width * height]
    if not frames:
        return {"frames": 0, "visual_change": 0.0, "text_density": 0.0, "text_area": 0.0}
    areas = [text_area(f, width, height) for f in frames]
    diffs = [mean_abs_diff(a, b) for a, b in zip(frames, frames[1:])]
    with_text = sum(1 for a in areas if a >= FRAME_TEXT_MIN_AREA)
    return {
        "frames": len(frames),
        "visual_change": round(sum(diffs) / len(diffs), 4) if diffs else 0.0,
        "static_pairs": sum(1 for d in diffs if d < 0.02),
        "text_density": round(with_text / len(frames), 3),
        "text_area": round(sum(areas) / len(areas), 4),
    }


def text_stats_from_boxes(results: list[dict]) -> dict[str, float | int]:
    """Text presence from OCR detection boxes; drops boxes that cannot be a text line."""
    frames_with_text = 0
    areas = []
    for r in results:
        w, h = r.get("width") or 1, r.get("height") or 1
        area = 0.0
        for x0, y0, x1, y1 in r.get("boxes") or []:
            bw, bh = x1 - x0, y1 - y0
            if bh <= 0 or bw <= 0 or bh > 0.2 * h or bw < 0.02 * w or bw * bh > 0.3 * w * h:
                continue  # whole-frame blobs, slivers and specks are not lines of text
            area += bw * bh / float(w * h)
        areas.append(min(1.0, area))
        if area > 0:
            frames_with_text += 1
    n = len(results)
    return {
        "frames": n,
        "text_density": round(frames_with_text / n, 3) if n else 0.0,
        "text_area": round(sum(areas) / n, 4) if n else 0.0,
    }
