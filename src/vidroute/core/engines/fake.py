"""Deterministic stand-ins for model-backed engines (``VIDROUTE_FAKE_ENGINES=1``).

They let the full pipeline run offline in CI without downloading models:
VAD falls back to ffmpeg energy detection (real), ASR returns labelled
placeholder text, OCR returns a placeholder line per frame. Output files say
``engine: fake-*`` so fake results can never be mistaken for real ones.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from vidroute.core.context import Ctx
from vidroute.core.engines.base import Engine, WorkerPool
from vidroute.core.engines.vad import EnergyVad
from vidroute.core.util.timefmt import fmt_ts


class FakeVad(EnergyVad):
    def probe(self) -> dict[str, Any]:
        return {"id": self.id, "installed": True, "fake": True}


class FakeAsr(Engine):
    capability = "asr"

    def probe(self) -> dict[str, Any]:
        return {"id": self.id, "installed": True, "fake": True}

    def transcribe(self, ctx: Ctx, pool: WorkerPool, wav: Path, segments: list[list[float]],
                   language: str | None = None, initial_prompt: str | None = None) -> list[dict[str, Any]]:
        out = []
        for i, (s, e) in enumerate(segments):
            ctx.progress("asr", i + 1, len(segments))
            out.append({"start": s, "end": e, "text": f"[fake transcript {fmt_ts(s)}-{fmt_ts(e)}]",
                        "lang": language if language and language != "auto" else "en",
                        "event": "Speech", "emotion": "", "engine": f"fake-{self.id}"})
        return out

    def smoke_test(self, ctx: Ctx) -> dict[str, Any]:
        return {"status": "passed", "detail": "fake engine"}


class FakeOcr(Engine):
    capability = "ocr"

    def probe(self) -> dict[str, Any]:
        return {"id": self.id, "installed": True, "fake": True}

    def recognize(self, ctx: Ctx, pool: WorkerPool, images: list[Path], min_score: float = 0.5) -> list[dict[str, Any]]:
        return [{"image": str(p), "lines": [{"text": f"[fake ocr {Path(p).stem}]", "score": 1.0,
                                             "box": [[0, 0], [10, 0], [10, 10], [0, 10]]}]} for p in images]

    def detect_text(self, ctx: Ctx, pool: WorkerPool, images: list[Path]) -> list[dict[str, Any]]:
        return [{"image": str(p), "width": 640, "height": 360, "boxes": [[40, 300, 600, 340]]} for p in images]

    def smoke_test(self, ctx: Ctx) -> dict[str, Any]:
        return {"status": "passed", "detail": "fake engine"}


FAKE_CLASSES: dict[str, type[Engine]] = {"asr": FakeAsr, "ocr": FakeOcr, "vad": FakeVad}
