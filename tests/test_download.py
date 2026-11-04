"""Downloader: resume after interruption, mirror fallback, hash and domain enforcement."""

import hashlib
import http.server
import io
import os
import socket
import tarfile
import threading
import time
import zipfile

import pytest

from vidroute.core.context import Ctx
from vidroute.core.errors import Cancelled, RunFailed
from vidroute.core.install.archive import UnsafeArchive, extract
from vidroute.core.install.download import DomainNotAllowed, Downloader, host_allowed

DATA = os.urandom(300_000)
SHA = hashlib.sha256(DATA).hexdigest()
BAD = os.urandom(300_000)


class _State:
    def __init__(self):
        self.drop_once = {}  # path -> bytes to send before cutting the connection (first request only)
        self.ignore_range = set()
        self.requests = []


class Handler(http.server.BaseHTTPRequestHandler):
    state: _State = None  # type: ignore[assignment]
    files = {"/good.bin": DATA, "/bad.bin": BAD, "/slow.bin": DATA}

    def log_message(self, *args):  # silence
        pass

    def do_GET(self):  # noqa: N802
        st = self.state
        st.requests.append((self.path, self.headers.get("Range")))
        if self.path == "/redirect-evil":
            self.send_response(302)
            self.send_header("Location", "http://evil.example.com/good.bin")
            self.end_headers()
            return
        if self.path == "/redirect-ok":
            self.send_response(302)
            self.send_header("Location", "/good.bin")
            self.end_headers()
            return
        data = self.files.get(self.path)
        if data is None:
            self.send_error(404)
            return
        start = 0
        rng = self.headers.get("Range")
        if rng and self.path not in st.ignore_range:
            start = int(rng.split("=")[1].split("-")[0])
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
        else:
            self.send_response(200)
        body = data[start:]
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        cut = st.drop_once.pop(self.path, None)
        if cut is not None:
            self.wfile.write(body[:cut])
            self.wfile.flush()
            self.connection.shutdown(socket.SHUT_RDWR)
            self.close_connection = True
            return
        if self.path == "/slow.bin":
            for i in range(0, len(body), 64 * 1024):
                self.wfile.write(body[i:i + 64 * 1024])
                self.wfile.flush()
                time.sleep(0.2)
            return
        self.wfile.write(body)


@pytest.fixture
def server():
    st = _State()
    Handler.state = st
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield base, st
    httpd.shutdown()
    httpd.server_close()


def make(tmp_path, ctx=None):
    return Downloader(["127.0.0.1"], ctx=ctx, retries=3, timeout=5, backoff=0, part_dir=tmp_path / "parts")


def test_host_allowed_suffix_rules():
    allowed = ["github.com", "hf.co"]
    assert host_allowed("https://github.com/x", allowed)
    assert host_allowed("https://release-assets.githubusercontent.com/x", ["githubusercontent.com"])
    assert host_allowed("https://us.aws.cdn.hf.co/x", allowed)
    assert not host_allowed("https://evilgithub.com/x", allowed)
    assert not host_allowed("https://github.com.evil.io/x", allowed)


def test_plain_download(server, tmp_path):
    base, _ = server
    dest = tmp_path / "out" / "good.bin"
    res = make(tmp_path).fetch([f"{base}/good.bin"], dest, SHA, len(DATA))
    assert dest.read_bytes() == DATA and res.resumed_from == 0
    assert not list((tmp_path / "parts").glob("*.part"))


def test_resume_after_connection_drop(server, tmp_path):
    base, st = server
    st.drop_once["/good.bin"] = 120_000
    dest = tmp_path / "good.bin"
    res = make(tmp_path).fetch([f"{base}/good.bin"], dest, SHA, len(DATA))
    assert dest.read_bytes() == DATA
    assert res.resumed_from == 120_000
    assert st.requests[-1][1] == "bytes=120000-"


def test_restart_when_server_ignores_range(server, tmp_path):
    base, st = server
    st.drop_once["/good.bin"] = 50_000
    st.ignore_range.add("/good.bin")
    dest = tmp_path / "good.bin"
    res = make(tmp_path).fetch([f"{base}/good.bin"], dest, SHA, len(DATA))
    assert dest.read_bytes() == DATA and res.resumed_from == 0


def test_bad_mirror_falls_back_to_good_one(server, tmp_path):
    base, _ = server
    dest = tmp_path / "x.bin"
    res = make(tmp_path).fetch([f"{base}/bad.bin", f"{base}/good.bin"], dest, SHA, len(DATA))
    assert dest.read_bytes() == DATA and res.url.endswith("/good.bin")


def test_all_mirrors_bad_fails_and_keeps_nothing(server, tmp_path):
    base, _ = server
    dest = tmp_path / "x.bin"
    with pytest.raises(RunFailed) as exc:
        make(tmp_path).fetch([f"{base}/bad.bin"], dest, SHA, len(DATA))
    assert exc.value.code == "download_failed"
    assert not dest.exists() and not list((tmp_path / "parts").glob("*.part"))


def test_redirect_to_disallowed_domain_is_refused(server, tmp_path):
    base, _ = server
    with pytest.raises(DomainNotAllowed):
        make(tmp_path).fetch([f"{base}/redirect-evil"], tmp_path / "x.bin", SHA, len(DATA))


def test_allowed_redirect_is_followed(server, tmp_path):
    base, _ = server
    dest = tmp_path / "x.bin"
    make(tmp_path).fetch([f"{base}/redirect-ok"], dest, SHA, len(DATA))
    assert dest.read_bytes() == DATA


def test_url_outside_allowlist_is_skipped(tmp_path):
    d = Downloader(["github.com"], retries=1, part_dir=tmp_path / "p")
    with pytest.raises(RunFailed) as exc:
        d.fetch(["https://example.org/file.bin"], tmp_path / "x", SHA, 10)
    assert "domain not allowed" in str(exc.value.data["errors"])


def test_existing_verified_file_is_not_downloaded(server, tmp_path):
    base, st = server
    dest = tmp_path / "good.bin"
    dest.write_bytes(DATA)
    res = make(tmp_path).fetch([f"{base}/good.bin"], dest, SHA, len(DATA))
    assert res.url == "local" and st.requests == []


def test_cancel_keeps_partial_and_resume_works(server, tmp_path):
    base, st = server
    ctx = Ctx()
    calls = {"n": 0}

    def on_progress(stage, done, total, msg):
        calls["n"] += 1
        if done and done > 0:
            ctx.cancel_event.set()

    ctx.on_progress = on_progress
    dest = tmp_path / "good.bin"
    st.drop_once["/good.bin"] = 100_000  # slow path: forces at least one progress callback
    with pytest.raises(Cancelled):
        make(tmp_path, ctx).fetch([f"{base}/good.bin"], dest, SHA, len(DATA))
    parts = list((tmp_path / "parts").glob("*.part"))
    assert parts and parts[0].stat().st_size > 0
    res = make(tmp_path).fetch([f"{base}/good.bin"], dest, SHA, len(DATA))
    assert dest.read_bytes() == DATA and res.resumed_from > 0


# ------------------------------------------------------------------ archives


def _tar(tmp_path, entries, name="a.tar.bz2"):
    path = tmp_path / name
    with tarfile.open(path, "w:bz2") as tf:
        for arc, data in entries:
            info = tarfile.TarInfo(arc)
            if data is None:
                info.type = tarfile.SYMTYPE
                info.linkname = "/etc/passwd"
                tf.addfile(info)
            else:
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
    return path


def test_extract_with_prefix_and_members(tmp_path):
    arc = _tar(tmp_path, [("pkg/model.onnx", b"m"), ("pkg/tokens.txt", b"t"), ("pkg/extra/x.wav", b"w")])
    written = extract(arc, tmp_path / "out", "pkg/", members=["model.onnx", "tokens.txt"], optional=["extra/x.wav", "nope"])
    assert sorted(written) == ["extra/x.wav", "model.onnx", "tokens.txt"]
    assert (tmp_path / "out" / "extra" / "x.wav").read_bytes() == b"w"


def test_extract_missing_required_member(tmp_path):
    arc = _tar(tmp_path, [("pkg/model.onnx", b"m")])
    with pytest.raises(RunFailed):
        extract(arc, tmp_path / "out", "pkg/", members=["model.onnx", "tokens.txt"])


@pytest.mark.parametrize("bad", ["../escape.txt", "/abs/path.txt", "pkg/../../x"])
def test_extract_rejects_traversal(tmp_path, bad):
    arc = _tar(tmp_path, [(bad, b"x")])
    with pytest.raises(UnsafeArchive):
        extract(arc, tmp_path / "out")


def test_extract_rejects_symlink(tmp_path):
    arc = _tar(tmp_path, [("pkg/link", None)])
    with pytest.raises(UnsafeArchive):
        extract(arc, tmp_path / "out", "pkg/")


def test_extract_zip(tmp_path):
    path = tmp_path / "a.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("root/a.txt", "A")
        zf.writestr("root/sub/b.txt", "B")
    assert sorted(extract(path, tmp_path / "o", "root/")) == ["a.txt", "sub/b.txt"]


def test_crawling_mirror_is_left_for_the_next_one_and_partial_data_is_reused(server, tmp_path):
    base, st = server
    dl = Downloader(["127.0.0.1"], retries=3, timeout=5, backoff=0, part_dir=tmp_path / "parts",
                    min_speed=2_000_000, speed_window=0.05)
    dest = tmp_path / "out.bin"
    dl.fetch([f"{base}/slow.bin", f"{base}/good.bin"], dest, SHA, len(DATA))
    assert dest.read_bytes() == DATA and dl.slow_seen
    assert [p for p, _ in st.requests].count("/slow.bin") == 1  # no retries on the same slow source
    good = next(rng for p, rng in st.requests if p == "/good.bin")
    assert good and good.startswith("bytes=")


def test_only_slow_source_still_installs_on_the_second_pass(monkeypatch):
    from vidroute.core.install import models as m
    from vidroute.core.install.manifest import load_manifest

    manifest = load_manifest()
    entry = manifest.model("vad-silero")
    dl = Downloader(manifest.allowed_domains)
    speeds = []

    def from_files(_entry, _sources, staging, d):
        speeds.append(d.min_speed)
        if d.min_speed:
            d.slow_seen = True
            raise RunFailed("too slow", code="download_failed")
        (staging / "silero_vad.onnx").write_bytes(b"x")

    monkeypatch.setattr(m, "_from_files", from_files)
    monkeypatch.setattr(m, "_verify_dir", lambda *a: [])
    assert m.install_model(entry, manifest, Ctx(), downloader=dl, force=True)["status"] != "failed"
    assert speeds[0] > 0 and speeds[-1] == 0
