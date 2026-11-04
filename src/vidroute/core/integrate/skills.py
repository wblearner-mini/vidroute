"""Copy the bundled Agent Skill into a host's user-level skill folder, and take it out again.

A copy is vidroute's own only while it holds :data:`MARKER`. vidroute replaces
or removes such copies; a ``vidroute`` folder without the marker (copied by
hand, or another skill of that name) is left alone, or with ``force`` renamed
aside to ``vidroute.bak-<timestamp>`` — never deleted.

The marker also records the Python interpreter that runs vidroute, so the
launchers in the copy work even when ``vidroute`` is not on PATH yet (an agent
host started before the install does not see PATH changes).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from vidroute import __version__
from vidroute.core.resources import data_file
from vidroute.core.util.jsonio import write_text_atomic
from vidroute.core.util.proc import CREATE_NO_WINDOW

SKILL_NAME = "vidroute"
MARKER = ".vidroute-skill.cfg"
_SKIP = ("__pycache__", ".pyc", MARKER)


def source_dir() -> Path:
    return data_file(f"skills/{SKILL_NAME}/SKILL.md").parent


def _files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file() and not any(s in p.parts or p.name.endswith(s)
                                                                          for s in _SKIP))


def digest(root: Path) -> str:
    """Content hash of a skill folder (paths and bytes), ignoring the marker and caches."""
    h = hashlib.sha256()
    for p in _files(root):
        h.update(p.relative_to(root).as_posix().encode("utf-8") + b"\0")
        h.update(p.read_bytes() + b"\0")
    return h.hexdigest()


@lru_cache(maxsize=1)
def runtime() -> dict[str, str]:
    """How a copied skill starts vidroute: this interpreter, plus the source tree when it is not installed."""
    import vidroute

    here = Path(vidroute.__file__).resolve()
    try:
        cp = subprocess.run([sys.executable, "-I", "-c", "import vidroute, sys; sys.stdout.write(vidroute.__file__)"],
                            capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL,
                            creationflags=CREATE_NO_WINDOW, check=False)
        installed = cp.returncode == 0 and Path(cp.stdout.strip()).resolve() == here
    except (OSError, subprocess.SubprocessError):
        installed = False
    out = {"python": sys.executable}
    if not installed:  # running from a checkout or plugin folder: point the copy at it
        out["pythonpath"] = str(here.parent.parent)
    return out


def read_marker(folder: Path) -> dict[str, str] | None:
    try:
        text = (folder / MARKER).read_text(encoding="utf-8")
    except OSError:
        return None
    record = {}
    for line in text.splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            record[key.strip()] = value.strip()
    return record if record.get("installed_by") == "vidroute" else None


def marker_text(record: dict[str, str]) -> str:
    lines = ["# Written by `vidroute setup`. vidroute replaces or removes this folder only while this file is here.",
             "# The launchers in scripts/ read `python` (and `pythonpath`) to start vidroute without PATH."]
    lines += [f"{k}={v}" for k, v in record.items()]
    return "\n".join(lines) + "\n"


def inspect(target: Path) -> dict[str, Any]:
    """State of ``<root>/vidroute``: absent, ours (current or outdated) or foreign."""
    info: dict[str, Any] = {"path": str(target), "exists": target.exists(), "ours": False, "current": False}
    if not info["exists"]:
        return info
    record = read_marker(target)
    if record is None:
        info["foreign"] = True
        return info
    py = record.get("python", "")
    info.update(ours=True, version=record.get("version"), installed_at=record.get("installed_at"),
                python=py, python_ok=bool(py) and Path(py).is_file())
    # Current: the same skill text as this vidroute, and the recorded interpreter still exists.
    info["current"] = record.get("digest") == digest(source_dir()) and info["python_ok"]
    return info


def is_vidroute_skill(target: Path) -> bool:
    """A copy of this skill (say, copied by hand), as opposed to another skill that happens to use the folder name."""
    try:
        text = (target / "SKILL.md").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return "name: vidroute" in text and (target / "scripts" / "vidroute").is_file()


def _swap(staging: Path, target: Path) -> None:
    old = target.with_name(f"{target.name}.old-{os.getpid()}")
    if target.exists():
        os.replace(target, old)
    try:
        os.replace(staging, target)
    except OSError:
        if old.exists():
            os.replace(old, target)
        raise
    shutil.rmtree(old, ignore_errors=True)


def install(target: Path, force: bool = False) -> dict[str, Any]:
    """Copy the bundled skill to ``target`` (``<skill root>/vidroute``) and write the marker."""
    state = inspect(target)
    backup = None
    if state.get("foreign"):
        if not force:
            return {"path": str(target), "status": "skipped",
                    "reason": "a vidroute skill folder that vidroute did not create is already there"}
        backup = target.with_name(f"{target.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        os.replace(target, backup)
    src = source_dir()
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_name(f".{target.name}.tmp-{os.getpid()}")
    shutil.rmtree(staging, ignore_errors=True)
    shutil.copytree(src, staging, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", MARKER))
    record = {"installed_by": "vidroute", "version": __version__, "digest": digest(src),
              "installed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **runtime()}
    write_text_atomic(staging / MARKER, marker_text(record))
    _swap(staging, target)
    out = {"path": str(target), "status": "updated" if state.get("ours") else "created", "version": __version__}
    if backup:
        out["backup"] = str(backup)
    return out


def remove(target: Path) -> dict[str, Any]:
    if not target.exists():
        return {"path": str(target), "status": "absent"}
    if read_marker(target) is None:
        return {"path": str(target), "status": "skipped", "reason": "not created by vidroute; left untouched"}
    shutil.rmtree(target)
    return {"path": str(target), "status": "removed"}
