"""Engine adapter interface (spec §3.2).

Every local engine implements ``probe()``, ``ensure_installed()``, ``run()``
(capability-specific: ``detect`` / ``transcribe`` / ``recognize``) and
``smoke_test()``. Adding a model means adding a manifest entry and, if it
needs a new runtime, an adapter registered in :mod:`vidroute.core.engines` —
routing code never changes.
"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path
from typing import Any

from vidroute.core.context import Ctx
from vidroute.core.engines.workerclient import WorkerClient
from vidroute.core.errors import NeedsInstall
from vidroute.core.install.manifest import EnvGroup, Manifest, ModelEntry


class WorkerPool:
    """Lazily started workers shared by all engines in one pipeline run."""

    def __init__(self, ctx: Ctx) -> None:
        self.ctx = ctx
        self.clients: dict[tuple[str, str], WorkerClient] = {}

    def get(self, group: EnvGroup, worker: str, stage: str | None = None) -> WorkerClient:
        key = (group.name, worker)
        client = self.clients.get(key)
        if client is None or client.proc is None or client.proc.poll() is not None:
            client = WorkerClient(group, worker, self.ctx, stage=stage).start()
            self.clients[key] = client
        client.stage = stage or worker
        return client

    def close(self) -> None:
        for client in self.clients.values():
            client.close()
        self.clients.clear()

    def __enter__(self) -> "WorkerPool":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


class Engine:
    capability = ""
    worker: str | None = None

    def __init__(self, model: ModelEntry, manifest: Manifest) -> None:
        self.model = model
        self.manifest = manifest

    @property
    def id(self) -> str:
        return self.model.id

    @property
    def group(self) -> EnvGroup | None:
        return self.manifest.env_groups.get(self.model.env_group or "")

    def path(self, rel: str) -> str:
        return str(self.model.model_dir / rel)

    def probe(self) -> dict[str, Any]:
        st = self.model.installed_status()
        env_ok = True if self.group is None else self.group.python_exe.is_file()
        return {"id": self.id, "installed": bool(st["installed"]) and env_ok, "model": st, "env_ok": env_ok}

    def ensure_installed(self) -> None:
        info = self.probe()
        if not info["installed"]:
            raise NeedsInstall(
                f"{self.capability} engine {self.id} is not installed.",
                code="needs_install",
                hint=f"vidroute install --model {self.id}",
                data={"needs": [self.id]},
            )

    def client(self, pool: WorkerPool) -> WorkerClient:
        assert self.group is not None and self.worker is not None
        return pool.get(self.group, self.worker, stage=self.capability)

    def smoke_test(self, ctx: Ctx) -> dict[str, Any]:  # pragma: no cover - overridden
        return {"status": "skipped", "detail": "no smoke test"}


def write_wav(path: Path, seconds: float, freq: float | None = None, sr: int = 16000) -> Path:
    """Write a 16 kHz mono test WAV (silence, or a sine tone when ``freq`` is given)."""
    n = int(seconds * sr)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        if freq:
            frames = b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * freq * i / sr))) for i in range(n))
        else:
            frames = b"\x00\x00" * n
        w.writeframes(frames)
    return path
