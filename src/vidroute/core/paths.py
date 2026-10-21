"""Locations vidroute may write to.

vidroute only ever writes inside ``VIDROUTE_HOME`` (default ``~/.vidroute``) and
the user-supplied ``--out`` directory. Everything else is read-only.
"""

from __future__ import annotations

import hashlib
import os
import re
import unicodedata
from pathlib import Path

ENV_HOME = "VIDROUTE_HOME"


def home() -> Path:
    raw = os.environ.get(ENV_HOME)
    if raw:
        return Path(raw).expanduser().resolve()
    return (Path.home() / ".vidroute").resolve()


def models_dir() -> Path:
    return home() / "models"


def envs_dir() -> Path:
    return home() / "envs"


def runtimes_dir() -> Path:
    return home() / "runtimes"


def cache_dir() -> Path:
    return home() / "cache"


def downloads_dir() -> Path:
    """Partial downloads (``*.part``) live here until verified and moved into place."""
    return home() / "cache" / "downloads"


def default_out_root() -> Path:
    return home() / "out"


def state_path() -> Path:
    return home() / "state.json"


def config_path() -> Path:
    return home() / "config.json"


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


_SLUG_DROP = re.compile(r"[^\w\-]+", re.UNICODE)


def slugify(text: str, max_len: int = 48) -> str:
    """Filesystem-safe slug that keeps CJK and other letters (works on Windows)."""
    text = unicodedata.normalize("NFKC", text)
    slug = _SLUG_DROP.sub("-", text).strip("-_.").lower()
    slug = re.sub(r"-{2,}", "-", slug)
    return slug[:max_len].rstrip("-_.") or "video"


def video_slug(video: Path) -> str:
    """Stable per-file slug: readable stem plus a short hash of path, size and mtime."""
    resolved = video.resolve()
    try:
        st = resolved.stat()
        ident = f"{resolved}|{st.st_size}|{int(st.st_mtime)}"
    except OSError:
        ident = str(resolved)
    digest = hashlib.sha1(ident.encode("utf-8", "surrogatepass")).hexdigest()[:8]
    return f"{slugify(resolved.stem)}-{digest}"
