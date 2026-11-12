"""Speaker diarization: who speaks when, as voice groups (never identities)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from vidroute.core.context import Ctx
from vidroute.core.engines.base import Engine, WorkerPool
from vidroute.core.util.limits import engine_threads


class SherpaDiarize(Engine):
    capability = "diarize"
    worker = "sherpa"

    def diarize(self, ctx: Ctx, pool: WorkerPool, wav: Path, num_speakers: int | None = None) -> list[dict[str, Any]]:
        """Turns ``{"start", "end", "speaker"}`` in file time, sorted by start. ``speaker`` is an arbitrary number."""
        params = {"segmentation": self.path("segmentation.onnx"), "embedding": self.path("embedding.onnx"),
                  "wav": str(wav), "num_speakers": num_speakers, "num_threads": engine_threads()}
        return self.client(pool).call("diarize", params)["turns"]

    def smoke_test(self, ctx: Ctx) -> dict[str, Any]:
        sample = self.manifest.model("asr-sensevoice-small-int8").model_dir / "test_wavs" / "zh.wav"
        if not sample.exists():
            return {"status": "skipped", "detail": "没有可用于测试的示例录音"}
        with WorkerPool(ctx) as pool:
            turns = self.diarize(ctx, pool, sample)
        return {"status": "passed" if turns else "failed", "detail": f"示例录音中有 {len(turns)} 段发言"}
