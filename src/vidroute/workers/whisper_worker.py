"""vidroute worker for the ``whisper`` environment: faster-whisper (CTranslate2), CPU or CUDA.

Self-contained: runs as ``<env python> -I whisper_worker.py``. On Windows the
CUDA 12/cuDNN 9 DLLs shipped in the nvidia-* wheels are added to the DLL search
path before CTranslate2 is imported (Linux uses LD_LIBRARY_PATH set by the
orchestrator).
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from pathlib import Path

_spec = importlib.util.spec_from_file_location("_vidroute_protocol", Path(__file__).with_name("_protocol.py"))
P = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(P)  # type: ignore[union-attr]


def _add_nvidia_dll_dirs() -> list:
    added = []
    if os.name != "nt":
        return added
    for base in sys.path:
        root = Path(base) / "nvidia"
        if not root.is_dir():
            continue
        for sub in ("cublas", "cudnn", "cuda_runtime", "cuda_nvrtc"):
            d = root / sub / "bin"
            if d.is_dir():
                os.add_dll_directory(str(d))
                os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
                added.append(str(d))
    return added


DLL_DIRS = _add_nvidia_dll_dirs()
MODELS: dict = {}


def m_ping(p, rid):
    import ctranslate2

    return {"python": sys.version.split()[0], "ctranslate2": ctranslate2.__version__, "dll_dirs": DLL_DIRS}


def m_cuda_check(p, rid):
    import ctranslate2

    n = ctranslate2.get_cuda_device_count()
    types = sorted(ctranslate2.get_supported_compute_types("cuda")) if n else []
    return {"cuda_devices": n, "compute_types": types, "ctranslate2": ctranslate2.__version__}


def m_asr_load(p, rid):
    key = json.dumps(p, sort_keys=True)
    if key in MODELS:
        return {"key": key, "cached": True}
    from faster_whisper import WhisperModel

    t0 = time.monotonic()
    MODELS[key] = WhisperModel(
        p["model_dir"], device=p.get("device", "cpu"), compute_type=p.get("compute_type", "int8"),
        cpu_threads=int(p.get("num_threads", 4)), local_files_only=True,
    )
    return {"key": key, "cached": False, "load_seconds": round(time.monotonic() - t0, 2)}


def m_asr(p, rid):
    model = MODELS[p["key"]]
    wav = P.Wav(p["wav"])
    language = p.get("language") or None
    out = []
    segs = p["segments"]
    for i, (s, e) in enumerate(segs):
        audio = wav.read(s, e)
        parts, info = model.transcribe(
            audio, language=language, beam_size=int(p.get("beam_size", 1)), vad_filter=False,
            condition_on_previous_text=False, initial_prompt=p.get("initial_prompt") or None,
        )
        text = "".join(part.text for part in parts).strip()
        out.append({"start": s, "end": e, "text": text, "lang": info.language or "", "event": "", "emotion": ""})
        P.progress(rid, i + 1, len(segs), "asr")
    wav.close()
    return {"segments": out}


if __name__ == "__main__":
    P.serve({"ping": m_ping, "cuda_check": m_cuda_check, "asr_load": m_asr_load, "asr": m_asr},
            {"worker": "whisper", "python": sys.version.split()[0]})
