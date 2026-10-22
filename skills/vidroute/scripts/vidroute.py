#!/usr/bin/env python3
"""Run vidroute from this skill: ``python scripts/vidroute.py <command> [options] --json``.

Which vidroute runs, in order:
1. the source tree this skill belongs to (the vidroute repository or Claude
   Code plugin), with the current Python — no install needed, the
   orchestrator is pure standard-library Python;
2. a ``vidroute`` executable on PATH (``pipx install vidroute`` or
   ``uv tool install vidroute``);
3. a ``vidroute`` package installed for this Python.

``python scripts/vidroute.py mcp`` starts the MCP server instead of the CLI.
Without any of the three, it prints a JSON envelope with error
``needs_install`` (exit code 2) so the agent can tell the user what to do.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MIN_PYTHON = (3, 10)


def _fail(code: str, message: str, hint: str, exit_code: int) -> int:
    sys.stdout.write(json.dumps({"ok": False, "schema": "vidroute/1", "data": {}, "warnings": [],
                                 "error": {"code": code, "message": message, "hint": hint}}) + "\n")
    return exit_code


def _source_tree() -> Path | None:
    """``<repo>/src`` when this file is ``<repo>/skills/vidroute/scripts/vidroute.py``."""
    src = HERE.parents[2] / "src" if len(HERE.parents) > 2 else None
    return src if src is not None and (src / "vidroute" / "__init__.py").is_file() else None


def _run_in_process(args: list[str], mcp: bool) -> int:
    from vidroute import cli, mcp_server

    return mcp_server.main(args) if mcp else cli.main(args)


def main() -> int:
    args = sys.argv[1:]
    mcp = bool(args) and args[0] == "mcp"
    if mcp:
        args = args[1:]
    src = _source_tree()
    if src is not None and sys.version_info >= MIN_PYTHON:
        sys.path.insert(0, str(src))
        return _run_in_process(args, mcp)
    exe = shutil.which("vidroute-mcp" if mcp else "vidroute")
    if exe:
        return subprocess.call([exe, *args])
    if sys.version_info < MIN_PYTHON:
        return _fail("python_too_old", f"vidroute needs Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+, this is "
                     f"{sys.version.split()[0]}.", "pipx install vidroute  (or: uv tool install vidroute)", 2)
    try:
        return _run_in_process(args, mcp)
    except ImportError:
        return _fail("needs_install", "vidroute is not installed.",
                     "pipx install vidroute  (or: uv tool install vidroute)", 2)


if __name__ == "__main__":
    raise SystemExit(main())
