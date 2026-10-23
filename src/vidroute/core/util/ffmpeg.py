"""Locating and interrogating ffmpeg/ffprobe.

vidroute never downloads ffmpeg on its own: if it is missing, the install plan
offers winget (or, without winget, a pinned portable build placed in
``$VIDROUTE_HOME/runtimes/ffmpeg``) on Windows and the package-manager command
elsewhere; nothing runs until the user confirms with ``install --yes``.
"""

from __future__ import annotations

import json
import os
import platform
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from vidroute.core import config, paths
from vidroute.core.errors import NeedsInstall, RunFailed
from vidroute.core.util import proc

PROBE_TIMEOUT = 5.0

INTERESTING_ENCODERS = (
    "libx264", "aac", "libmp3lame", "libopus", "mjpeg", "libopenh264", "mpeg4",
    "h264_nvenc", "h264_qsv", "h264_amf", "h264_videotoolbox", "h264_mf",
)
INTERESTING_FILTERS = ("select", "scdet", "silencedetect", "drawtext", "tile", "scale", "showinfo", "aresample")


def install_hint() -> str:
    system = platform.system()
    if system == "Windows":
        return "vidroute install --capability scene   (plans FFmpeg: winget, or a portable build)"
    if system == "Darwin":
        return "brew install ffmpeg"
    return "sudo apt install ffmpeg  (or your distribution's package manager)"


def _candidates(name: str) -> list[Path]:
    extra = [paths.runtimes_dir() / "ffmpeg" / "bin"]
    if platform.system() == "Windows":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            # winget installs Gyan.FFmpeg under a versioned folder and adds a shim link dir.
            extra.append(Path(local) / "Microsoft" / "WinGet" / "Links")
    found = []
    configured = config.get(f"{name}_path")
    if configured:
        found.append(Path(configured))
    env_override = os.environ.get(f"VIDROUTE_{name.upper()}")
    if env_override:
        found.append(Path(env_override))
    hit = proc.which(name, extra)
    if hit:
        found.append(hit)
    return found


def find_tool(name: str) -> Path | None:
    for candidate in _candidates(name):
        if candidate.is_file():
            return candidate
    return None


@dataclass
class FFmpegInfo:
    found: bool
    ffmpeg: str | None = None
    ffprobe: str | None = None
    version: str | None = None
    encoders: dict[str, bool] = field(default_factory=dict)
    filters: dict[str, bool] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "found": self.found,
            "path": self.ffmpeg,
            "ffprobe": self.ffprobe,
            "version": self.version,
            "encoders": self.encoders,
            "filters": self.filters,
            "error": self.error,
            "install_hint": None if self.found else install_hint(),
        }


_VERSION_RE = re.compile(r"ffmpeg version (\S+)")


def parse_version(text: str) -> str | None:
    m = _VERSION_RE.search(text)
    return m.group(1) if m else None


def parse_codec_list(text: str, wanted: tuple[str, ...]) -> dict[str, bool]:
    """Parse ``ffmpeg -encoders`` / ``-filters`` output for the names we care about."""
    names = set()
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and not line.startswith(" ="):
            names.add(parts[1])
            names.add(parts[0])
    return {w: w in names for w in wanted}


def detect(timeout: float = PROBE_TIMEOUT) -> FFmpegInfo:
    ffmpeg = find_tool("ffmpeg")
    ffprobe = find_tool("ffprobe")
    if ffmpeg is None or ffprobe is None:
        missing = "ffmpeg" if ffmpeg is None else "ffprobe"
        return FFmpegInfo(False, str(ffmpeg) if ffmpeg else None, str(ffprobe) if ffprobe else None,
                          error=f"{missing} not found")
    ver = proc.run([ffmpeg, "-hide_banner", "-version"], timeout=timeout)
    if not ver.ok:
        return FFmpegInfo(False, str(ffmpeg), str(ffprobe), error=(ver.stderr or "ffmpeg -version failed").strip()[:300])
    enc = proc.run([ffmpeg, "-hide_banner", "-encoders"], timeout=timeout)
    flt = proc.run([ffmpeg, "-hide_banner", "-filters"], timeout=timeout)
    return FFmpegInfo(
        True,
        str(ffmpeg),
        str(ffprobe),
        version=parse_version(ver.stdout),
        encoders=parse_codec_list(enc.stdout, INTERESTING_ENCODERS) if enc.ok else {},
        filters=parse_codec_list(flt.stdout, INTERESTING_FILTERS) if flt.ok else {},
    )


@lru_cache(maxsize=1)
def _cached_detect() -> FFmpegInfo:
    return detect()


def reset_cache() -> None:
    """Forget a cached "not found" after ffmpeg was installed in this process."""
    _cached_detect.cache_clear()


def require() -> FFmpegInfo:
    """Return ffmpeg info or raise NeedsInstall (exit code 2) with the install command."""
    info = _cached_detect()
    if not info.found:
        _cached_detect.cache_clear()
        raise NeedsInstall(
            f"ffmpeg/ffprobe are required but were not found ({info.error}).",
            code="ffmpeg_missing",
            hint=install_hint(),
        )
    return info


def ffprobe_json(path: Path, timeout: float = 60.0) -> dict[str, Any]:
    info = require()
    res = proc.run(
        [info.ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", "-show_chapters", str(path)],
        timeout=timeout,
    )
    if not res.ok:
        raise RunFailed(
            f"ffprobe could not read {path.name}: {(res.stderr or '').strip()[:300] or 'timed out'}",
            code="probe_failed",
            hint=f'ffprobe -v error -show_format "{path}"',
        )
    try:
        return json.loads(res.stdout)
    except ValueError as exc:
        raise RunFailed(f"ffprobe returned invalid JSON: {exc}", code="probe_failed") from exc
