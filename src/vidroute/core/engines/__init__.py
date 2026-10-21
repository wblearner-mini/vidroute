"""Engine registry: manifest ``engine`` name -> adapter class.

Set ``VIDROUTE_FAKE_ENGINES=1`` to replace model-backed engines with
deterministic fakes (used by offline integration tests and CI; never in
normal use).
"""

from __future__ import annotations

import os

from vidroute.core.engines.asr import FasterWhisper, SherpaSenseVoice, SherpaWhisper
from vidroute.core.engines.base import Engine, WorkerPool
from vidroute.core.engines.ocr import RapidOcr
from vidroute.core.engines.scene import FfmpegScene
from vidroute.core.engines.vad import EnergyVad, SileroVad
from vidroute.core.install.manifest import Manifest, load_manifest

ENGINE_CLASSES: dict[str, type[Engine]] = {
    "sherpa-vad-silero": SileroVad,
    "ffmpeg-silencedetect": EnergyVad,
    "sherpa-asr-sense-voice": SherpaSenseVoice,
    "sherpa-asr-whisper": SherpaWhisper,
    "faster-whisper": FasterWhisper,
    "rapidocr": RapidOcr,
    "ffmpeg-scene": FfmpegScene,
}


def fake_mode() -> bool:
    return os.environ.get("VIDROUTE_FAKE_ENGINES") == "1"


def supported(model_id: str, manifest: Manifest | None = None) -> bool:
    manifest = manifest or load_manifest()
    return manifest.model(model_id).engine in ENGINE_CLASSES


def get_engine(model_id: str, manifest: Manifest | None = None) -> Engine:
    manifest = manifest or load_manifest()
    model = manifest.model(model_id)
    if fake_mode():
        from vidroute.core.engines import fake

        cls = fake.FAKE_CLASSES.get(model.capability)
        if cls is not None and not model.builtin:
            return cls(model, manifest)
    cls = ENGINE_CLASSES.get(model.engine)
    if cls is None:
        from vidroute.core.errors import NeedsInstall

        raise NeedsInstall(f"Engine {model.engine!r} for {model_id} is not available in this version.",
                           code="engine_unavailable", hint="vidroute models list")
    return cls(model, manifest)


__all__ = ["ENGINE_CLASSES", "Engine", "WorkerPool", "fake_mode", "get_engine", "supported"]
