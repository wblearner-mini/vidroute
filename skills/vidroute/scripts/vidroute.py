#!/usr/bin/env python3
"""Run vidroute from this skill: ``python scripts/vidroute.py <command> [options] --json``.

Which vidroute runs, in order:
1. the source tree this skill belongs to (the vidroute repository or Claude
   Code plugin), with the current Python — no install needed, the
   orchestrator is pure standard-library Python;
2. the installation recorded by ``vidroute setup`` in ``../.vidroute-skill.cfg``
   (works before PATH is refreshed, e.g. in an agent host started earlier);
3. a ``vidroute`` package importable by this Python, or a ``vidroute``
   executable on PATH (``uv tool install`` / ``pipx install``).

``python scripts/vidroute.py mcp`` starts the MCP server instead of the CLI.
Without any of these, it prints a JSON envelope with error ``needs_install``
(exit code 2) so the agent can tell the user what to do. The ``vidroute`` (sh)
and ``vidroute.cmd`` launchers next to this file find a Python (or uv) first.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MIN_PYTHON = (3, 10)
INSTALL_HINT = "uv tool install git+https://github.com/wblearner-mini/vidroute   (then: vidroute setup)"
# Python puts this script's folder first on sys.path; since the script is called vidroute.py it would
# shadow the vidroute package on `import vidroute`.
sys.path[:] = [p for p in sys.path if Path(p or ".").resolve() != HERE]


def _fail(code: str, message: str, hint: str, exit_code: int) -> int:
    sys.stdout.write(json.dumps({"ok": False, "schema": "vidroute/1", "data": {}, "warnings": [],
                                 "error": {"code": code, "message": message, "hint": hint}}) + "\n")
    return exit_code


def _source_tree() -> Path | None:
    """``<repo>/src`` when this file is ``<repo>/skills/vidroute/scripts/vidroute.py``."""
    src = HERE.parents[2] / "src" if len(HERE.parents) > 2 else None
    return src if src is not None and (src / "vidroute" / "__init__.py").is_file() else None


def _recorded() -> dict[str, str]:
    """``key=value`` lines of the marker ``vidroute setup`` writes next to a copied skill."""
    try:
        text = (HERE.parent / ".vidroute-skill.cfg").read_text(encoding="utf-8")
    except OSError:
        return {}
    pairs = (line.partition("=") for line in text.splitlines() if "=" in line and not line.startswith("#"))
    return {k.strip(): v.strip() for k, _, v in pairs}


def _same(a: str, b: str) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def _run_in_process(args: list[str], mcp: bool) -> int:
    from vidroute import cli, mcp_server

    return mcp_server.main(args) if mcp else cli.main(args)


def main() -> int:
    args = sys.argv[1:]
    mcp = bool(args) and args[0] == "mcp"
    if mcp:
        args = args[1:]
    new_enough = sys.version_info >= MIN_PYTHON
    src = _source_tree()
    if src is not None and new_enough:
        sys.path.insert(0, str(src))
        return _run_in_process(args, mcp)
    rec = _recorded()
    if rec.get("pythonpath") and Path(rec["pythonpath"], "vidroute").is_dir() and new_enough:
        sys.path.insert(0, rec["pythonpath"])
        return _run_in_process(args, mcp)
    importable = new_enough and importlib.util.find_spec("vidroute") is not None
    py = rec.get("python")
    if py and Path(py).is_file() and not _same(py, sys.executable):  # the installation setup recorded wins
        return subprocess.call([py, "-m", "vidroute.mcp_server" if mcp else "vidroute", *args])
    if importable:
        return _run_in_process(args, mcp)
    exe = shutil.which("vidroute-mcp" if mcp else "vidroute")
    if exe:
        return subprocess.call([exe, *args])
    if not new_enough:
        return _fail("python_too_old", f"vidroute needs Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+, this is "
                     f"{sys.version.split()[0]}.", INSTALL_HINT, 2)
    return _fail("needs_install", "vidroute is not installed.", INSTALL_HINT, 2)


if __name__ == "__main__":
    raise SystemExit(main())
