"""Speech recognition engines (all return segment-level text in global time)."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from vidroute.core import state
from vidroute.core.context import Ctx
from vidroute.core.engines.base import Engine, WorkerPool, write_wav
from vidroute.core.util.limits import engine_threads

SENSEVOICE_LANGS = {"zh", "en", "ja", "ko", "yue"}


class AsrEngine(Engine):
    capability = "asr"
    kind = ""

    def load_params(self, language: str | None) -> dict[str, Any]:  # pragma: no cover - overridden
        raise NotImplementedError

    def transcribe(
        self, ctx: Ctx, pool: WorkerPool, wav: Path, segments: list[list[float]], language: str | None = None,
        initial_prompt: str | None = None,
    ) -> list[dict[str, Any]]:
        if not segments:
            return []
        client = self.client(pool)
        key = client.call("asr_load", {"num_threads": engine_threads(), **self.load_params(language)})["key"]
        params: dict[str, Any] = {"key": key, "wav": str(wav), "segments": segments}
        if initial_prompt:
            params["initial_prompt"] = initial_prompt
        res = client.call("asr", params)
        for seg in res["segments"]:
            seg["engine"] = self.id
        return res["segments"]

    def _test_wavs(self) -> list[Path]:
        sv = self.manifest.model("asr-sensevoice-small-int8").model_dir / "test_wavs"
        return [p for p in (sv / "zh.wav", sv / "en.wav") if p.exists()]

    def smoke_test(self, ctx: Ctx) -> dict[str, Any]:
        with WorkerPool(ctx) as pool, tempfile.TemporaryDirectory() as tmp:
            wavs = self._test_wavs()
            if not wavs:
                silent = write_wav(Path(tmp) / "silence.wav", 2.0)
                self.transcribe(ctx, pool, silent, [[0.0, 2.0]])
                return {"status": "passed", "detail": "模型已加载，并解码了 2 秒静音"}
            texts = []
            for wav in wavs:
                import wave

                with wave.open(str(wav), "rb") as w:
                    dur = w.getnframes() / float(w.getframerate())
                out = self.transcribe(ctx, pool, wav, [[0.0, round(dur, 3)]])
                texts.append(out[0]["text"] if out else "")
            ok = all(t.strip() for t in texts)
            return {"status": "passed" if ok else "failed", "detail": " | ".join(t[:40] for t in texts)}


class SherpaSenseVoice(AsrEngine):
    worker = "sherpa"
    kind = "sense_voice"

    def load_params(self, language: str | None) -> dict[str, Any]:
        lang = (language or "auto").lower()
        lang = lang if lang in SENSEVOICE_LANGS else "auto"
        return {"kind": self.kind, "model": self.path("model.int8.onnx"), "tokens": self.path("tokens.txt"),
                "language": lang, "use_itn": True}


class SherpaWhisper(AsrEngine):
    worker = "sherpa"
    kind = "whisper"

    def load_params(self, language: str | None) -> dict[str, Any]:
        prefix = self.model.whisper_prefix or "small"
        lang = "" if not language or language == "auto" else language.lower().split("-")[0]
        return {"kind": self.kind, "encoder": self.path(f"{prefix}-encoder.int8.onnx"),
                "decoder": self.path(f"{prefix}-decoder.int8.onnx"), "tokens": self.path(f"{prefix}-tokens.txt"),
                "language": lang}


class FasterWhisper(AsrEngine):
    worker = "whisper"
    kind = "faster_whisper"

    def device(self) -> tuple[str, str]:
        """CUDA only after a recorded, passed smoke test; otherwise CPU int8 (automatic fallback)."""
        smoke = (state.load().get("gpu_smoke") or {}).get("cuda") or {}
        if smoke.get("status") == "passed":
            return "cuda", "float16"
        return "cpu", "int8"

    def load_params(self, language: str | None) -> dict[str, Any]:
        device, compute = self.device()
        return {"model_dir": str(self.model.model_dir), "device": device, "compute_type": compute}

    def cuda_smoke(self, ctx: Ctx) -> dict[str, Any]:
        """Prove CUDA works end to end; record the outcome for doctor's tier decision."""
        from datetime import datetime, timezone

        result: dict[str, Any]
        try:
            with WorkerPool(ctx) as pool:
                client = self.client(pool)
                info = client.call("cuda_check")
                if not info.get("cuda_devices"):
                    raise RuntimeError("CTranslate2 看不到任何 CUDA 设备")
                key = client.call("asr_load", {"model_dir": str(self.model.model_dir), "device": "cuda",
                                                "compute_type": "float16"})["key"]
                wavs = self._test_wavs()
                with tempfile.TemporaryDirectory() as tmp:
                    wav = wavs[0] if wavs else write_wav(Path(tmp) / "s.wav", 2.0)
                    client.call("asr", {"key": key, "wav": str(wav), "segments": [[0.0, 2.0]]})
            result = {"status": "passed", "detail": f"{info['cuda_devices']} 个 CUDA 设备"}
        except Exception as exc:  # noqa: BLE001 - any failure means "use CPU"
            result = {"status": "failed", "reason": f"{type(exc).__name__}: {str(exc)[:300]}"}
        result["at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        data = state.load()
        data.setdefault("gpu_smoke", {})["cuda"] = result
        state.save(data)
        return result

    def smoke_test(self, ctx: Ctx) -> dict[str, Any]:
        if self.model.requires_gpu == "cuda":
            res = self.cuda_smoke(ctx)
            if res["status"] != "passed":
                return {"status": "fallback_cpu", "detail": f"GPU 冒烟测试失败，将使用 CPU：{res.get('reason')}"}
            return res
        return super().smoke_test(ctx)
