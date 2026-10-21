"""Streaming SHA-256."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Callable

CHUNK = 1024 * 1024


def sha256_file(path: Path, progress: Callable[[int], None] | None = None) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        while True:
            block = fh.read(CHUNK)
            if not block:
                break
            h.update(block)
            if progress:
                progress(len(block))
    return h.hexdigest()
