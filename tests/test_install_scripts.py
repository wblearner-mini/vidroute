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


def test_default_source_needs_no_git():
    for text in (PS1.read_text(encoding="utf-8"), SH.read_text(encoding="utf-8")):
        assert "git+https" not in text and "/archive/refs/heads/main.tar.gz" in text


@pytest.mark.skipif(os.name == "nt" or not shutil.which("sh"), reason="POSIX shell")
def test_wheel_unpacks_with_only_coreutils_and_gzip(tmp_path):
    """A fresh machine has neither unzip nor python3: the shell reads the zip itself."""
    import zipfile

    payload = b"#!/bin/sh\necho hi\n" + bytes(range(256)) * 400
    wheel = tmp_path / "x.whl"
    with zipfile.ZipFile(wheel, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("first.txt", "skip me")
        z.writestr("pkg.data/scripts/uv", payload)
        z.writestr("stored", "plain", compress_type=zipfile.ZIP_STORED)
    text = SH.read_text(encoding="utf-8")
    funcs = text[text.index("le() {"):text.index("find_uv() {")]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("od", "awk", "tail", "head", "gzip", "wc", "cat"):
        (bin_dir / tool).symlink_to(shutil.which(tool))
    script = tmp_path / "t.sh"
    script.write_text(funcs + f'\nzip_member "{wheel}" pkg.data/scripts/uv "{tmp_path}/out"\n'
                              f'zip_member "{wheel}" stored "{tmp_path}/out2"\nzip_member "{wheel}" nope "{tmp_path}/o3" || echo missing\n',
                      encoding="utf-8")
    res = subprocess.run([shutil.which("sh"), str(script)], env={"PATH": str(bin_dir)}, capture_output=True, text=True, timeout=30)
    assert res.stdout.strip() == "missing", res.stderr
    assert (tmp_path / "out").read_bytes() == payload and (tmp_path / "out2").read_bytes() == b"plain"


@pytest.mark.skipif(os.name == "nt" or not shutil.which("sh"), reason="POSIX shell")
def test_install_sh_without_a_downloader_says_what_to_install(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("uname", "id", "cut", "sed"):
        (bin_dir / tool).symlink_to(shutil.which(tool))
    res = subprocess.run([shutil.which("sh"), str(SH), "--no-setup"], env={"PATH": str(bin_dir), "HOME": str(tmp_path)},
                         capture_output=True, text=True, timeout=30)
    assert res.returncode == 1 and "需要 curl 或 wget" in res.stderr and "not found" not in res.stderr
