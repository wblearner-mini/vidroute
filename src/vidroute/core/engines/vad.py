"""Voice activity detection engines."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any

from vidroute.core.context import Ctx
from vidroute.core.engines.base import Engine, WorkerPool, write_wav
from vidroute.core.util import ffmpeg, proc


class SileroVad(Engine):
    capability = "vad"
    worker = "sherpa"

    def detect(
        self,
        ctx: Ctx,
        pool: WorkerPool,
        wav: Path,
        windows: list[list[float]] | None = None,
        threshold: float = 0.5,
        min_silence: float = 0.4,
        min_speech: float = 0.25,
        max_speech: float = 20.0,
    ) -> list[list[float]]:
        res = self.client(pool).call(
            "vad",
            {"model": self.path("silero_vad.onnx"), "wav": str(wav), "windows": windows, "threshold": threshold,
             "min_silence": min_silence, "min_speech": min_speech, "max_speech": max_speech},
        )
        return res["segments"]

    def smoke_test(self, ctx: Ctx) -> dict[str, Any]:
        sample = self.manifest.model("asr-sensevoice-small-int8").model_dir / "test_wavs" / "zh.wav"
        with WorkerPool(ctx) as pool, tempfile.TemporaryDirectory() as tmp:
            if sample.exists():
                segs = self.detect(ctx, pool, sample)
                ok = len(segs) >= 1
                return {"status": "passed" if ok else "failed", "detail": f"测试语音中有 {len(segs)} 个语音片段"}
            silence = write_wav(Path(tmp) / "silence.wav", 2.0)
            segs = self.detect(ctx, pool, silence)
            return {"status": "passed" if not segs else "failed", "detail": "在 2 秒静音上运行"}


_SILENCE_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
_SILENCE_END = re.compile(r"silence_end:\s*(-?[\d.]+)")


MIN_SOUND_SECONDS = 0.25  # shorter bursts are clicks / encoder transients, not speech (cf. Silero min_speech)


def parse_silencedetect(stderr: str, duration: float, offset: float = 0.0,
                        min_span: float = MIN_SOUND_SECONDS) -> list[list[float]]:
    """Turn ffmpeg silencedetect output into non-silent spans (global time), dropping spans < ``min_span``."""
    silences: list[list[float]] = []
    start = None
    for line in stderr.splitlines():
        m = _SILENCE_START.search(line)
        if m:
            start = max(0.0, float(m.group(1)))
            continue
        m = _SILENCE_END.search(line)
        if m and start is not None:
            silences.append([start, float(m.group(1))])
            start = None
    if start is not None:
        silences.append([start, duration])
    spans = []
    cursor = 0.0
    for s, e in silences:
        if s - cursor >= min_span:
            spans.append([round(offset + cursor, 3), round(offset + s, 3)])
        cursor = max(cursor, e)
    if duration - cursor >= min_span:
        spans.append([round(offset + cursor, 3), round(offset + duration, 3)])
    return spans


class EnergyVad(Engine):
    """Fallback: anything louder than -35 dB for 0.4 s is "sound" (music included)."""

    capability = "vad"
    worker = None

    def probe(self) -> dict[str, Any]:
        found = ffmpeg.find_tool("ffmpeg") is not None
        return {"id": self.id, "installed": found, "builtin": True}

    def detect(
        self,
        ctx: Ctx,
        pool: WorkerPool | None,
        wav: Path,
        windows: list[list[float]] | None = None,
        noise_db: float = -35.0,
        min_silence: float = 0.4,
        **_: Any,
    ) -> list[list[float]]:
        info = ffmpeg.require()
        if not windows:
            dur = _wav_duration(wav)
            windows = [[0.0, dur]]
        spans: list[list[float]] = []
        for start, end in windows:
            ctx.check_cancelled()
            res = proc.run(
                [info.ffmpeg, "-hide_banner", "-nostats", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}",
                 "-i", str(wav), "-af", f"silencedetect=noise={noise_db}dB:d={min_silence}", "-f", "null", "-"],
                timeout=max(60.0, (end - start) * 0.5),
            )
            spans.extend(parse_silencedetect(res.stderr, end - start, offset=start))
        return spans

    def smoke_test(self, ctx: Ctx) -> dict[str, Any]:
        return {"status": "passed" if self.probe()["installed"] else "failed", "detail": "ffmpeg silencedetect"}


def _wav_duration(path: Path) -> float:
    import wave

    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate())
