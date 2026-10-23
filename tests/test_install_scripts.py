"""install.ps1 / install.sh: pinned uv matches vidroute's own bootstrap and the hash-locked envs/uv.lock.txt."""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from vidroute.core.doctor.runtimes import UV_VERSION

ROOT = Path(__file__).resolve().parents[1]
PS1 = ROOT / "install.ps1"
SH = ROOT / "install.sh"
LOCK_HASHES = set(re.findall(r"sha256:([0-9a-f]{64})", (ROOT / "envs" / "uv.lock.txt").read_text(encoding="utf-8")))


def test_both_scripts_pin_the_same_uv_as_the_bootstrap():
    ps1 = PS1.read_text(encoding="utf-8")
    sh = SH.read_text(encoding="utf-8")
    assert f'$UvVersion = "{UV_VERSION}"' in ps1 and f"UV_VERSION={UV_VERSION} " in sh
    for text in (ps1, sh):
        wheels = re.findall(r"packages/[0-9a-f/]+/uv-([0-9.]+)-py3-none-[a-z0-9_.]+\.whl", text)
        assert wheels and set(wheels) == {UV_VERSION}
        assert f"releases/download/$UvVersion/" in text or "releases/download/$UV_VERSION/" in text


def test_wheel_hashes_are_the_locked_ones():
    for text in (PS1.read_text(encoding="utf-8"), SH.read_text(encoding="utf-8")):
        wheel_hashes = set(re.findall(r'(?:WheelSha = "|wheel_sha=)([0-9a-f]{64})', text))
        assert wheel_hashes and wheel_hashes <= LOCK_HASHES


def test_line_endings_follow_gitattributes():
    raw = PS1.read_bytes()
    assert raw.count(b"\r\n") == raw.count(b"\n")
    assert b"\r" not in SH.read_bytes()


def test_installers_end_in_vidroute_setup():
    assert "& $vidroute @setupArgs" in PS1.read_text(encoding="utf-8")
    assert '"$bin/vidroute" "$@"' in SH.read_text(encoding="utf-8")


@pytest.mark.skipif(os.name == "nt" or not shutil.which("sh"), reason="POSIX shell")
def test_install_sh_parses_and_prints_help():
    assert subprocess.run(["sh", "-n", str(SH)], capture_output=True, timeout=30).returncode == 0
    res = subprocess.run(["sh", str(SH), "--help"], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0 and "vidroute setup" in res.stdout
