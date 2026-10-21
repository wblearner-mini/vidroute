"""vidroute worker for the ``sherpa`` environment: Silero VAD and sherpa-onnx ASR.

Self-contained: runs as ``<env python> -I sherpa_worker.py``.
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

import numpy as np  # noqa: E402
import sherpa_onnx as so  # noqa: E402

RECOGNIZERS: dict = {}
SR = 16000


def _clean_tag(tag) -> str:
    tag = (tag or "").strip()
    return tag[2:-2] if tag.startswith("<|") and tag.endswith("|>") else tag


def m_ping(p, rid):
    return {"python": sys.version.split()[0], "sherpa_onnx": getattr(so, "__version__", "?"), "numpy": np.__version__}


def _make_vad(p):
    cfg = so.VadModelConfig()
    cfg.silero_vad.model = p["model"]
    cfg.silero_vad.threshold = float(p.get("threshold", 0.5))
    cfg.silero_vad.min_silence_duration = float(p.get("min_silence", 0.4))
    cfg.silero_vad.min_speech_duration = float(p.get("min_speech", 0.25))
    cfg.silero_vad.max_speech_duration = float(p.get("max_speech", 20.0))
    cfg.sample_rate = SR
    cfg.num_threads = int(p.get("num_threads", 1))
    return cfg, so.VoiceActivityDetector(cfg, buffer_size_in_seconds=max(60, int(cfg.silero_vad.max_speech_duration * 3)))


def m_vad(p, rid):
    """Detect speech. ``windows`` (list of [start, end]) restricts work to parts of the file."""
    wav = P.Wav(p["wav"])
    if wav.sr != SR:
        raise ValueError(f"expected {SR} Hz audio, got {wav.sr}")
    windows = p.get("windows") or [[0.0, wav.duration]]
    cfg, vad = _make_vad(p)
    ws = cfg.silero_vad.window_size
    block = SR * 30  # read 30 s at a time
    segments = []
    total = sum(max(0.0, min(e, wav.duration) - s) for s, e in windows)
    done = 0.0
    last = 0.0
    for w_start, w_end in windows:
        w_end = min(w_end, wav.duration)
        if w_end <= w_start:
            continue
        vad.reset()
        pos = w_start
        carry = np.zeros(0, dtype=np.float32)
        fed = 0  # samples fed for this window

        def drain():
            while not vad.empty():
                seg = vad.front
                s = w_start + seg.start / SR
                segments.append([round(s, 3), round(s + len(seg.samples) / SR, 3)])
                vad.pop()

        while pos < w_end:
            nxt = min(w_end, pos + block / SR)
            data = np.concatenate([carry, wav.read(pos, nxt)])
            n = (len(data) // ws) * ws
            for i in range(0, n, ws):
                vad.accept_waveform(data[i:i + ws])
            fed += n
            carry = data[n:]
            drain()
            done += nxt - pos
            pos = nxt
            now = time.monotonic()
            if now - last > 0.5:
                P.progress(rid, round(done, 1), round(total, 1), "vad")
                last = now
        vad.flush()
        drain()
    wav.close()
    segments.sort()
    return {"segments": segments, "duration": wav.duration, "windows": windows}


def m_asr_load(p, rid):
    key = json.dumps(p, sort_keys=True)
    if key in RECOGNIZERS:
        return {"key": key, "cached": True}
    kind = p["kind"]
    threads = int(p.get("num_threads", max(1, min(8, (os.cpu_count() or 2) // 2))))
    t0 = time.monotonic()
    if kind == "sense_voice":
        rec = so.OfflineRecognizer.from_sense_voice(
            model=p["model"], tokens=p["tokens"], num_threads=threads, use_itn=bool(p.get("use_itn", True)),
            language=p.get("language") or "auto", provider="cpu",
        )
    elif kind == "whisper":
        rec = so.OfflineRecognizer.from_whisper(
            encoder=p["encoder"], decoder=p["decoder"], tokens=p["tokens"], language=p.get("language") or "",
            task="transcribe", num_threads=threads, provider="cpu",
        )
    else:
        raise ValueError(f"unknown ASR kind {kind!r}")
    RECOGNIZERS[key] = (kind, rec)
    return {"key": key, "cached": False, "load_seconds": round(time.monotonic() - t0, 2)}


def m_asr(p, rid):
    kind, rec = RECOGNIZERS[p["key"]]
    wav = P.Wav(p["wav"])
    segs = p["segments"]
    batch = int(p.get("batch", 8 if kind == "sense_voice" else 1))
    out = []
    for i in range(0, len(segs), batch):
        chunk = segs[i:i + batch]
        streams = []
        for s, e in chunk:
            st = rec.create_stream()
            audio = wav.read(s, e)
            if kind == "whisper":  # whisper needs a little tail padding to finish the last word
                audio = np.concatenate([audio, np.zeros(int(0.3 * SR), dtype=np.float32)])
            st.accept_waveform(SR, audio)
            streams.append(st)
        rec.decode_streams(streams)
        for (s, e), st in zip(chunk, streams):
            r = st.result
            out.append({
                "start": s, "end": e, "text": (r.text or "").strip(),
                "lang": _clean_tag(getattr(r, "lang", "")),
                "event": _clean_tag(getattr(r, "event", "")),
                "emotion": _clean_tag(getattr(r, "emotion", "")),
            })
        P.progress(rid, min(i + batch, len(segs)), len(segs), "asr")
    wav.close()
    return {"segments": out}


if __name__ == "__main__":
    P.serve({"ping": m_ping, "vad": m_vad, "asr_load": m_asr_load, "asr": m_asr},
            {"worker": "sherpa", "python": sys.version.split()[0]})
