"""Output directory layout (brief §9) and small helpers shared by prepare/drill.

<out>/<slug>/
  SUMMARY.md  ANALYSIS_GUIDE.md  handoff.json  plan.json  manifest.json  segments.json
  native/  audio/16k.wav  transcript.{md,srt,json}  frames/  contact-sheets/
  ocr.{md,json}  timeline.md  drill/  .work/ (resume state, not a deliverable)
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vidroute.core import config, paths
from vidroute.core.errors import UsageError
from vidroute.core.util.jsonio import read_json, write_json_atomic


@dataclass
class Layout:
    root: Path

    @property
    def work(self) -> Path:
        return self.root / ".work"

    @property
    def audio_wav(self) -> Path:
        return self.root / "audio" / "16k.wav"

    @property
    def frames(self) -> Path:
        return self.root / "frames"

    @property
    def sheets(self) -> Path:
        return self.root / "contact-sheets"

    @property
    def native(self) -> Path:
        return self.root / "native"

    def file(self, name: str) -> Path:
        return self.root / name

    def rel(self, path: Path) -> str:
        return path.resolve().relative_to(self.root.resolve()).as_posix()

    def ensure(self) -> "Layout":
        for d in (self.root, self.work):
            d.mkdir(parents=True, exist_ok=True)
        return self


def out_root(out: str | None) -> Path:
    if out:
        return Path(out).expanduser().resolve()
    configured = config.get("out_root")
    return Path(configured).expanduser().resolve() if configured else paths.default_out_root()


def layout_for(video: Path, out: str | None) -> Layout:
    root = out_root(out) / paths.video_slug(video)
    if root.exists() and not root.is_dir():
        raise UsageError(f"Output path exists and is not a directory: {root}")
    try:
        return Layout(root).ensure()
    except OSError as exc:
        raise UsageError(f"Cannot write to the output folder {root}: {exc.strerror or exc}. Choose another with --out.",
                         code="output_not_writable", hint="vidroute config set out_root <a folder you can write to>") from exc


def reset_dir(path: Path, layout: Layout) -> Path:
    """Empty a deliverable sub-directory of this output (never anything outside it)."""
    if not paths.is_within(path, layout.root):
        raise UsageError(f"Refusing to clear {path}: outside {layout.root}")
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def remove_dir(path: Path, layout: Layout) -> None:
    """Delete a stale deliverable sub-directory of this output (never anything outside it)."""
    if not paths.is_within(path, layout.root) or path == layout.root:
        raise UsageError(f"Refusing to delete {path}: outside {layout.root}")
    if path.is_dir():
        shutil.rmtree(path)


def fingerprint(*parts: Any) -> str:
    h = hashlib.sha1()
    for p in parts:
        h.update(repr(p).encode("utf-8"))
    return h.hexdigest()[:16]


class StepState:
    """Remembers completed steps (and their input fingerprint) so a re-run can resume."""

    def __init__(self, layout: Layout) -> None:
        self.path = layout.work / "steps.json"
        self.data: dict[str, Any] = read_json(self.path, default={}) or {}

    def done(self, step: str, fp: str) -> bool:
        return self.data.get(step, {}).get("fp") == fp

    def mark(self, step: str, fp: str, **info: Any) -> None:
        self.data[step] = {"fp": fp, **info}
        write_json_atomic(self.path, self.data)

    def get(self, step: str) -> dict[str, Any]:
        return self.data.get(step, {})
