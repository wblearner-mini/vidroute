"""Project-wide guarantees that are easy to break by accident."""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "vidroute"
STDLIB = set(getattr(sys, "stdlib_module_names", ()))


def _py_files(base):
    return sorted(p for p in base.rglob("*.py") if "__pycache__" not in p.parts)


def test_no_shell_true_anywhere():
    for path in _py_files(SRC):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword) and node.arg == "shell":
                value = node.value
                assert isinstance(value, ast.Constant) and value.value is False, f"shell=True in {path}"


def test_kernel_is_stdlib_only():
    """The orchestrator must import only the standard library (workers excepted)."""
    if not STDLIB:  # Python < 3.10 has no stdlib_module_names
        return
    offenders = []
    for path in _py_files(SRC):
        if "workers" in path.relative_to(SRC).parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            for name in names:
                top = name.split(".")[0]
                if top not in STDLIB and top != "vidroute" and top != "__future__":
                    offenders.append(f"{path.relative_to(ROOT)}: {name}")
    assert not offenders, offenders


def test_sources_are_utf8_and_lf():
    for path in _py_files(SRC):
        raw = path.read_bytes()
        raw.decode("utf-8")
        assert b"\r\n" not in raw, f"CRLF in {path}"
