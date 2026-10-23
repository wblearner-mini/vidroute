"""Portable ffmpeg for Windows (plan, download, verify, winget fallback) and Windows-on-Arm worker Pythons."""

import hashlib
import http.server
import io
import threading
import zipfile
from dataclasses import replace

import pytest

from vidroute.core import config
from vidroute.core.context import Ctx
from vidroute.core.errors import RunFailed
from vidroute.core.install import envs, planner, portable, runner
from vidroute.core.install.manifest import ModelFile, load_manifest

FILES = {"bin/ffmpeg.exe": b"MZ fake ffmpeg" * 1000, "bin/ffprobe.exe": b"MZ fake ffprobe" * 1000,
         "LICENSE": b"GPLv3"}
PREFIX = "ffmpeg-9.9-essentials_build/"


def _zip(files, prefix=PREFIX):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(prefix + name, data)
        zf.writestr(prefix + "bin/ffplay.exe", b"not wanted")
    return buf.getvalue()


@pytest.fixture
def served():
    payload = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):  # noqa: N802
            data = payload.get(self.path)
            if data is None:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield payload, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _entry_and_manifest(base, archive, files=FILES):
    m = load_manifest()
    real = m.runtimes["ffmpeg-windows"]
    src = replace(real.sources[0], url=f"{base}/ffmpeg.zip", sha256=hashlib.sha256(archive).hexdigest(),
                  size=len(archive), strip_prefix=PREFIX)
    entry = replace(real, sources=(src,), files=tuple(
        ModelFile(name, len(data), hashlib.sha256(data).hexdigest()) for name, data in files.items()))
    manifest = replace(m, allowed_domains=("127.0.0.1",), runtimes={entry.id: entry})
    return entry, manifest


def test_manifest_pins_the_windows_build():
    m = load_manifest()
    entry = portable.for_platform(m, key="windows-x86_64")
    assert entry is portable.for_platform(m, key="windows-arm64") and entry.version == "9.0.2"
    assert portable.for_platform(m, key="linux-x86_64") is None
    src = entry.sources[0]
    assert src.url.startswith("https://github.com/GyanD/codexffmpeg/releases/download/9.0.2/")
    assert len(src.sha256) == 64 and {f.path for f in entry.files} == {"bin/ffmpeg.exe", "bin/ffprobe.exe", "LICENSE"}
    assert m.domain_allowed("github.com") and entry.path.name == "ffmpeg"


def test_portable_install_verifies_and_places_the_files(served):
    payload, base = served
    archive = _zip(FILES)
    payload["/ffmpeg.zip"] = archive
    entry, manifest = _entry_and_manifest(base, archive)
    res = portable.install(entry, manifest, Ctx())
    assert res["status"] == "installed" and entry.installed_status()["installed"]
    assert (entry.path / "bin" / "ffmpeg.exe").read_bytes() == FILES["bin/ffmpeg.exe"]
    assert not (entry.path / "bin" / "ffplay.exe").exists()
    assert portable.install(entry, manifest, Ctx())["status"] == "already_installed"


def test_portable_install_rejects_a_tampered_file(served):
    payload, base = served
    archive = _zip({**FILES, "bin/ffprobe.exe": b"evil"})
    payload["/ffmpeg.zip"] = archive
    entry, manifest = _entry_and_manifest(base, archive)  # file hashes still expect the good ffprobe
    with pytest.raises(RunFailed) as err:
        portable.install(entry, manifest, Ctx())
    assert "ffprobe" in err.value.message and not entry.path.exists()


@pytest.fixture
def windows(monkeypatch):
    """Pretend to be Windows x64 without ffmpeg."""
    monkeypatch.setattr(planner.platform, "system", lambda: "Windows")
    monkeypatch.setattr(planner.ffmpeg, "find_tool", lambda name: None)
    monkeypatch.setattr(portable, "platform_key", lambda: "windows-x86_64")


def test_plan_without_winget_downloads_the_portable_build(windows, monkeypatch):
    monkeypatch.setattr(planner.proc, "which", lambda name, extra=(): None)
    plan = planner.plan_install(["scene"], doctor_data={"tier": "cpu-full", "gpus": [], "disk_free_gb": 100})
    (item,) = [i for i in plan["items"] if i["kind"] == "ffmpeg"]
    assert item["action"] == "download" and item["id"] == "ffmpeg-windows" and item["size_mb"] > 100
    assert plan["total_download_mb"] >= item["size_mb"] and not plan["nothing_to_do"]
    assert "ffmpeg-windows 9.0.2" in planner.describe(plan)


def test_plan_with_winget_keeps_winget_and_names_the_fallback(windows, monkeypatch):
    monkeypatch.setattr(planner.proc, "which", lambda name, extra=(): "C:/winget.exe" if name == "winget" else None)
    item = next(i for i in planner.plan_install(["scene"], doctor_data={"tier": "cpu-full", "gpus": []})["items"]
                if i["kind"] == "ffmpeg")
    assert item["action"] == "run_with_yes" and item["command"][:3] == ["winget", "install", "--id"]
    assert item["fallback"]["action"] == "download"
    config.set_value("ffmpeg_source", "portable")
    item = next(i for i in planner.plan_install(["scene"], doctor_data={"tier": "cpu-full", "gpus": []})["items"]
                if i["kind"] == "ffmpeg")
    assert item["action"] == "download"


def test_failed_winget_falls_back_to_the_portable_build(windows, monkeypatch):
    monkeypatch.setattr(planner.proc, "which", lambda name, extra=(): "C:/winget.exe" if name == "winget" else None)
    plan = planner.plan_install(["scene"], doctor_data={"tier": "cpu-full", "gpus": []})

    class Failed:
        ok = False
        stdout = ""
        stderr = "0x8a15000f : Data required by the source is missing"

    monkeypatch.setattr(runner.proc, "run", lambda *a, **k: Failed())
    calls = []
    monkeypatch.setattr(runner.portable, "install", lambda entry, m, ctx, dl=None: calls.append(entry.id) or
                        {"id": entry.id, "status": "installed", "path": "x", "version": entry.version})
    ctx = Ctx()
    out = runner.execute(plan, ctx)
    assert calls == ["ffmpeg-windows"] and [r["status"] for r in out["results"]] == ["failed", "installed"]
    assert any(w["code"] == "winget_failed" for w in ctx.warnings)


def test_windows_on_arm_uses_x64_python_where_wheels_require_it(monkeypatch, isolated_home):
    monkeypatch.setattr(envs.platform, "system", lambda: "Windows")
    monkeypatch.setattr(envs, "native_machine", lambda: "arm64")
    groups = load_manifest().env_groups
    assert envs.python_request(groups["sherpa"]) == "3.12"
    assert envs.python_request(groups["ocr"]) == "cpython-3.12-windows-x86_64-none"
    assert envs.python_request(groups["whisper"]) == "cpython-3.12-windows-x86_64-none"
    pydir = isolated_home / "runtimes" / "python"
    (pydir / "cpython-3.12.15-windows-aarch64-none").mkdir(parents=True)
    assert planner._python_runtime_present("3.12")
    assert not planner._python_runtime_present("cpython-3.12-windows-x86_64-none")
    (pydir / "cpython-3.12.15-windows-x86_64-none").mkdir()
    assert planner._python_runtime_present("cpython-3.12-windows-x86_64-none")
    item = planner._python_item("cpython-3.12-windows-x86_64-none")
    assert (item["id"], item["version"]) == ("python-3.12-x86_64", "3.12") and "emulated" in item["note"]


def test_x64_windows_keeps_the_plain_request(monkeypatch):
    monkeypatch.setattr(envs.platform, "system", lambda: "Windows")
    monkeypatch.setattr(envs, "native_machine", lambda: "x86_64")
    assert envs.python_request(load_manifest().env_groups["ocr"]) == "3.12"
