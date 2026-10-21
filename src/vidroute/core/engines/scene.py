"""Scene-change detection with ffmpeg (no model, no Python dependency)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from vidroute.core.context import Ctx
from vidroute.core.engines.base import Engine, WorkerPool
from vidroute.core.util import ffmpeg, proc

_PTS = re.compile(r"pts_time:\s*([\d.]+)")


def parse_showinfo_times(stderr: str) -> list[float]:
    return [round(float(m.group(1)), 3) for m in _PTS.finditer(stderr)]


class FfmpegScene(Engine):
    capability = "scene"
    worker = None

    def probe(self) -> dict[str, Any]:
        return {"id": self.id, "installed": ffmpeg.find_tool("ffmpeg") is not None, "builtin": True}

    def detect(
        self,
        ctx: Ctx,
        pool: WorkerPool | None,
        video: Path,
        threshold: float = 0.3,
        sample_fps: float = 4.0,
        keyframes_only: bool = False,
        start: float | None = None,
        end: float | None = None,
        timeout: float = 3600.0,
    ) -> list[float]:
        """Return timestamps (global seconds) where the picture changes by more than ``threshold``."""
        info = ffmpeg.require()
        args = [info.ffmpeg, "-hide_banner", "-nostats"]
        if keyframes_only:
            args += ["-skip_frame", "nokey"]
        if start:
            args += ["-ss", f"{start:.3f}"]
        args += ["-i", str(video)]
        if end is not None:
            args += ["-t", f"{end - (start or 0.0):.3f}"]
        vf = f"fps={sample_fps},scale=256:-2,select='gt(scene\\,{threshold})',showinfo"
        args += ["-an", "-sn", "-dn", "-vf", vf, "-f", "null", "-"]
        ctx.check_cancelled()
        res = proc.run(args, timeout=timeout)
        times = parse_showinfo_times(res.stderr)
        offset = start or 0.0
        return [round(t + offset, 3) for t in times]

    def smoke_test(self, ctx: Ctx) -> dict[str, Any]:
        return {"status": "passed" if self.probe()["installed"] else "failed", "detail": "ffmpeg select/scene"}
