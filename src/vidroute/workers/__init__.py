"""Worker scripts executed inside isolated environments.

Each worker is a self-contained script (it must not import vidroute, which is
not installed in the worker environment). It speaks newline-delimited JSON on
stdin/stdout:

    -> {"id": 1, "method": "asr", "params": {...}}
    <- {"id": 1, "progress": {"done": 3, "total": 10}}
    <- {"id": 1, "result": {...}}            or {"id": 1, "error": {...}}

The first line a worker prints is ``{"event": "ready", ...}``. Anything native
libraries print goes to stderr, never to the protocol stream.
"""

from pathlib import Path

WORKERS_DIR = Path(__file__).resolve().parent


def script(name: str) -> Path:
    return WORKERS_DIR / f"{name}_worker.py"
