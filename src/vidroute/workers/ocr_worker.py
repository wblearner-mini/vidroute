"""vidroute worker for the ``ocr`` environment: PP-OCR via RapidOCR on onnxruntime.

Self-contained: runs as ``<env python> -I ocr_worker.py``. Models are always
passed as explicit local paths, so RapidOCR never downloads anything itself.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

_spec = importlib.util.spec_from_file_location("_vidroute_protocol", Path(__file__).with_name("_protocol.py"))
P = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(P)  # type: ignore[union-attr]

ENGINES: dict = {}


def m_ping(p, rid):
    import onnxruntime

    return {"python": sys.version.split()[0], "onnxruntime": onnxruntime.__version__}


def m_ocr_load(p, rid):
    key = json.dumps(p, sort_keys=True)
    if key in ENGINES:
        return {"key": key, "cached": True}
    from rapidocr import RapidOCR

    for k in ("det", "rec"):
        if not Path(p[k]).is_file():
            raise FileNotFoundError(f"{k} model not found: {p[k]}")
    params = {
        "Global.use_cls": False,
        "Global.log_level": "error",
        "Global.text_score": float(p.get("text_score", 0.5)),
        "Det.model_path": p["det"],
        "Rec.model_path": p["rec"],
    }
    threads = p.get("num_threads")
    if threads:
        params["EngineConfig.onnxruntime.intra_op_num_threads"] = int(threads)
    t0 = time.monotonic()
    ENGINES[key] = RapidOCR(params=params)
    return {"key": key, "cached": False, "load_seconds": round(time.monotonic() - t0, 2)}


def m_ocr(p, rid):
    eng = ENGINES[p["key"]]
    images = p["images"]
    min_score = float(p.get("min_score", 0.5))
    results = []
    for i, img in enumerate(images):
        r = eng(img)
        lines = []
        boxes = r.boxes if r.boxes is not None else []
        for text, score, box in zip(r.txts or (), r.scores or (), boxes):
            if float(score) < min_score or not str(text).strip():
                continue
            pts = [[int(round(x)), int(round(y))] for x, y in box.tolist()]
            lines.append({"text": str(text), "score": round(float(score), 4), "box": pts})
        results.append({"image": img, "lines": lines})
        P.progress(rid, i + 1, len(images), "ocr")
    return {"results": results}


def m_render_text(p, rid):
    """Render a test image (used by the OCR smoke test)."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (int(p.get("width", 900)), int(p.get("height", 160))), "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=int(p.get("size", 56)))
    draw.text((30, 40), p["text"], fill="black", font=font)
    img.save(p["out"])
    return {"out": p["out"]}


if __name__ == "__main__":
    P.serve({"ping": m_ping, "ocr_load": m_ocr_load, "ocr": m_ocr, "render_text": m_render_text},
            {"worker": "ocr", "python": sys.version.split()[0]})
