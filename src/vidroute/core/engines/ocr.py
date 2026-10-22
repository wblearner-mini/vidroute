"""OCR engines."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from vidroute.core.context import Ctx
from vidroute.core.engines.base import Engine, WorkerPool


class RapidOcr(Engine):
    capability = "ocr"
    worker = "ocr"

    def _files(self) -> tuple[str, str]:
        det = next(f.path for f in self.model.files if "_det_" in f.path)
        rec = next(f.path for f in self.model.files if "_rec_" in f.path)
        return self.path(det), self.path(rec)

    def recognize(self, ctx: Ctx, pool: WorkerPool, images: list[Path], min_score: float = 0.5) -> list[dict[str, Any]]:
        if not images:
            return []
        client = self.client(pool)
        det, rec = self._files()
        key = client.call("ocr_load", {"det": det, "rec": rec})["key"]
        res = client.call("ocr", {"key": key, "images": [str(p) for p in images], "min_score": min_score})
        return res["results"]

    def detect_text(self, ctx: Ctx, pool: WorkerPool, images: list[Path]) -> list[dict[str, Any]]:
        """Bounding rectangles of text lines (detection only, no recognition)."""
        if not images:
            return []
        client = self.client(pool)
        det, rec = self._files()
        key = client.call("ocr_load", {"det": det, "rec": rec})["key"]
        return client.call("detect", {"key": key, "images": [str(p) for p in images]})["results"]

    def smoke_test(self, ctx: Ctx) -> dict[str, Any]:
        with WorkerPool(ctx) as pool, tempfile.TemporaryDirectory() as tmp:
            img = Path(tmp) / "smoke.png"
            self.client(pool).call("render_text", {"out": str(img), "text": "VIDROUTE 2026"})
            res = self.recognize(ctx, pool, [img])
            text = " ".join(line["text"] for line in res[0]["lines"]) if res else ""
            ok = "VIDROUTE" in text.upper().replace(" ", "")
            return {"status": "passed" if ok else "failed", "detail": f"read: {text!r}"}
