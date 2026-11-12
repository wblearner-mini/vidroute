"""Access to bundled data files (profiles, rules, manifest, schemas, templates).

Two layouts are supported:

* installed wheel: files live in the package under ``vidroute/_data/<rel>``;
* source checkout / Agent Skill folder / editable install: files live at the
  repository root (``<repo>/routing``, ``<repo>/models`` ...).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from vidroute.core.errors import DataFileError

_PKG_DIR = Path(__file__).resolve().parent.parent  # .../vidroute


@lru_cache(maxsize=None)
def _roots() -> tuple[Path, ...]:
    roots = [_PKG_DIR / "_data"]
    # src layout: <repo>/src/vidroute -> <repo>
    repo = _PKG_DIR.parent.parent
    if (repo / "routing").is_dir():
        roots.append(repo)
    return tuple(roots)


def data_file(rel: str) -> Path:
    for root in _roots():
        candidate = root / rel
        if candidate.exists():
            return candidate
    raise DataFileError(
        f"找不到内置数据文件：{rel}",
        hint="pipx reinstall vidroute",
        data={"searched": [str(r) for r in _roots()]},
    )


def read_text(rel: str) -> str:
    return data_file(rel).read_text(encoding="utf-8")
