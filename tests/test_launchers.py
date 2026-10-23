"""The skill's shell launchers: `scripts/vidroute` (sh) and `scripts/vidroute.cmd` (Windows) find a Python or uv,
use the interpreter `vidroute setup` recorded, and otherwise answer with a needs_install envelope."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from vidroute import __version__

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "vidroute" / "scripts"
SH = SCRIPTS / "vidroute"
CMD = SCRIPTS / "vidroute.cmd"
posix_only = pytest.mark.skipif(os.name == "nt", reason="POSIX sh launcher")
windows_only = pytest.mark.skipif(os.name != "nt", reason="Windows cmd launcher")


def _copy_with_marker(tmp_path, python):
    """A skill copy outside the source tree, as `vidroute setup` leaves it."""
    copy = tmp_path / "skills" / "vidroute"
    shutil.copytree(ROOT / "skills" / "vidroute", copy)
    (copy / ".vidroute-skill.cfg").write_text(
        f"# test\ninstalled_by=vidroute\nversion={__version__}\npython={python}\n", encoding="utf-8")
    return copy


def _envelope(res):
    assert res.stdout.strip(), res.stderr
    return json.loads(res.stdout)


def test_cmd_launcher_is_crlf_ascii_and_sh_launcher_uses_builtins_only():
    raw = CMD.read_bytes()
    raw.decode("ascii")
    assert raw.count(b"\r\n") == raw.count(b"\n") > 20  # cmd.exe misparses labels in LF-only batch files
    sh = SH.read_text(encoding="utf-8")
    assert "\r" not in sh and sh.startswith("#!/bin/sh")
    for external in ("sed ", "head ", "dirname ", "awk ", "grep "):
        assert external not in sh, external


@posix_only
def test_sh_launcher_runs_the_source_tree(tmp_path):
    res = subprocess.run(["sh", str(SH), "version", "--json"], capture_output=True, text=True, timeout=60,
                         env={**os.environ, "VIDROUTE_HOME": str(tmp_path / "vh")})
    assert res.returncode == 0 and _envelope(res)["data"]["version"] == __version__


@posix_only
def test_sh_launcher_uses_the_recorded_python_without_path(tmp_path):
    copy = _copy_with_marker(tmp_path, sys.executable)
    env = {"PATH": str(tmp_path / "empty"), "HOME": str(tmp_path), "VIDROUTE_HOME": str(tmp_path / "vh")}
    res = subprocess.run(["/bin/sh", str(copy / "scripts" / "vidroute"), "version", "--json"],
                         capture_output=True, text=True, timeout=60, env=env)
    assert res.returncode == 0, res.stdout + res.stderr
    assert _envelope(res)["data"]["version"] == __version__


@posix_only
def test_sh_launcher_without_python_or_uv_says_what_to_install(tmp_path):
    copy = _copy_with_marker(tmp_path, tmp_path / "gone" / "python")
    env = {"PATH": str(tmp_path / "empty"), "HOME": str(tmp_path), "VIDROUTE_HOME": str(tmp_path / "vh")}
    res = subprocess.run(["/bin/sh", str(copy / "scripts" / "vidroute"), "doctor", "--json"],
                         capture_output=True, text=True, timeout=60, env=env)
    assert res.returncode == 2
    err = _envelope(res)["error"]
    assert err["code"] == "needs_install" and "uv" in err["hint"]


def _windows_env(tmp_path, path):
    keep = {k: os.environ[k] for k in ("SystemRoot", "SYSTEMROOT", "ComSpec", "COMSPEC", "WINDIR", "TEMP", "TMP")
            if k in os.environ}
    fake = tmp_path / "profile"
    (fake / "AppData" / "Local").mkdir(parents=True)
    return {**keep, "PATH": path, "USERPROFILE": str(fake), "LOCALAPPDATA": str(fake / "AppData" / "Local"),
            "VIDROUTE_HOME": str(tmp_path / "vh")}


@windows_only
def test_cmd_launcher_runs_the_source_tree(tmp_path):
    res = subprocess.run([str(CMD), "version", "--json"], capture_output=True, text=True, timeout=60,
                         env={**os.environ, "VIDROUTE_HOME": str(tmp_path / "vh")})
    assert res.returncode == 0, res.stdout + res.stderr
    assert _envelope(res)["data"]["version"] == __version__


@windows_only
def test_cmd_launcher_uses_the_recorded_python_without_path(tmp_path):
    copy = _copy_with_marker(tmp_path, sys.executable)
    system32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
    res = subprocess.run([str(copy / "scripts" / "vidroute.cmd"), "version", "--json"], capture_output=True,
                         text=True, timeout=60, env=_windows_env(tmp_path, system32))
    assert res.returncode == 0, res.stdout + res.stderr
    assert _envelope(res)["data"]["version"] == __version__


@windows_only
def test_cmd_launcher_without_python_or_uv_says_what_to_install(tmp_path):
    copy = _copy_with_marker(tmp_path, tmp_path / "gone" / "python.exe")
    system32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
    res = subprocess.run([str(copy / "scripts" / "vidroute.cmd"), "doctor", "--json"], capture_output=True,
                         text=True, timeout=60, env=_windows_env(tmp_path, system32))
    if shutil.which("py", path=system32) or shutil.which("python", path=system32):
        pytest.skip("a Python is installed in System32 on this machine")
    assert res.returncode == 2, res.stdout + res.stderr
    err = _envelope(res)["error"]
    assert err["code"] == "needs_install" and "uv" in err["hint"]
