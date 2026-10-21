"""Shared JSON-lines protocol for workers.

Loaded by path from each worker script (workers run with ``python -I`` and
cannot import the vidroute package). Standard library only.
"""

from __future__ import annotations

import io
import json
import os
import sys
import traceback
import wave
from typing import Any, Callable, Dict

_proto = None


def _open_protocol_stream() -> Any:
    # Keep the original stdout fd for the protocol and point fd 1 at stderr, so
    # anything a native library prints cannot corrupt the JSON stream.
    fd = os.dup(1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return io.TextIOWrapper(os.fdopen(fd, "wb", buffering=0), encoding="utf-8", newline="\n", write_through=True)


def send(obj: Dict[str, Any]) -> None:
    _proto.write(json.dumps(obj, ensure_ascii=False) + "\n")
    _proto.flush()


def progress(rid: Any, done: float, total: float, message: str | None = None) -> None:
    send({"id": rid, "progress": {"done": done, "total": total, "message": message}})


def serve(methods: Dict[str, Callable[[Dict[str, Any], Any], Any]], info: Dict[str, Any]) -> None:
    global _proto
    _proto = _open_protocol_stream()
    stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8")
    send({"event": "ready", "pid": os.getpid(), **info})
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError as exc:
            send({"id": None, "error": {"type": "BadRequest", "message": str(exc)}})
            continue
        rid = req.get("id")
        method = req.get("method")
        if method == "shutdown":
            send({"id": rid, "result": {"bye": True}})
            break
        fn = methods.get(method)
        if fn is None:
            send({"id": rid, "error": {"type": "MethodNotFound", "message": str(method)}})
            continue
        try:
            result = fn(req.get("params") or {}, rid)
        except Exception as exc:  # noqa: BLE001 - reported to the orchestrator
            send({"id": rid, "error": {"type": type(exc).__name__, "message": str(exc),
                                       "traceback": traceback.format_exc()[-3000:]}})
        else:
            send({"id": rid, "result": result})


class Wav:
    """16 kHz mono 16-bit PCM WAV reader with random access (what vidroute's ffmpeg step writes)."""

    def __init__(self, path: str) -> None:
        self.w = wave.open(path, "rb")
        self.sr = self.w.getframerate()
        self.channels = self.w.getnchannels()
        self.width = self.w.getsampwidth()
        self.frames = self.w.getnframes()
        if self.width != 2:
            raise ValueError(f"expected 16-bit PCM, got {self.width * 8}-bit")

    @property
    def duration(self) -> float:
        return self.frames / float(self.sr)

    def read(self, start: float, end: float):  # -> numpy.ndarray (float32, mono)
        import numpy as np

        a = max(0, int(round(start * self.sr)))
        b = min(self.frames, int(round(end * self.sr)))
        if b <= a:
            return np.zeros(0, dtype=np.float32)
        self.w.setpos(a)
        raw = self.w.readframes(b - a)
        data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
        if self.channels > 1:
            data = data.reshape(-1, self.channels).mean(axis=1)
        return data

    def close(self) -> None:
        self.w.close()
