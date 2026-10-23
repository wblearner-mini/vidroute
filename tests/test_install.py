"""Install planning and model installation (no network: a local HTTP server stands in for mirrors)."""

import hashlib
import http.server
import io
import json
import tarfile
import threading

import pytest

from vidroute.api import install as api_install
from vidroute.core.context import Ctx
from vidroute.core.errors import InsufficientHardware
from vidroute.core.install import planner
from vidroute.core.install.download import Downloader
from vidroute.core.install.manifest import load_manifest, parse_manifest
from vidroute.core.install.models import install_model, remove_model, verify_model

DOCTOR_CPU = {"tier": "cpu-full", "gpus": [], "ram_gb": 16, "disk_free_gb": 500, "reusable_models": []}
DOCTOR_LITE = {**DOCTOR_CPU, "tier": "cpu-lite", "ram_gb": 4}
DOCTOR_NV = {**DOCTOR_CPU, "gpus": [{"vendor": "nvidia", "name": "RTX", "vram_gb": 12}]}


def ids(plan, kind="model"):
    return [i["id"] for i in plan["items"] if i["kind"] == kind]


@pytest.fixture(autouse=True)
def english_locale(monkeypatch):
    monkeypatch.setattr(planner, "system_language", lambda: "en-us")


def test_auto_profile_is_base_package():
    plan = planner.plan_install(doctor_data=DOCTOR_CPU)
    assert ids(plan) == ["vad-silero", "asr-sensevoice-small-int8"]
    assert {i["id"] for i in plan["items"] if i["kind"] == "env"} == {"sherpa"}
    assert plan["confirm_command"] == "vidroute install --yes"


def test_auto_adds_whisper_for_uncovered_language(monkeypatch):
    monkeypatch.setattr(planner, "system_language", lambda: "de-de")
    assert "asr-whisper-small-int8" in ids(planner.plan_install(doctor_data=DOCTOR_CPU))


def test_full_profile_without_gpu_skips_cuda_model():
    plan = planner.plan_install(profile="full", doctor_data=DOCTOR_CPU)
    assert "ocr-ppocrv6-small" in ids(plan) and "asr-whisper-small-int8" in ids(plan)
    assert "asr-faster-whisper-large-v3-turbo" not in ids(plan)


def test_full_profile_with_nvidia_adds_cuda_env():
    plan = planner.plan_install(profile="full", doctor_data=DOCTOR_NV)
    assert "asr-faster-whisper-large-v3-turbo" in ids(plan)
    env = next(i for i in plan["items"] if i["kind"] == "env" and i["id"] == "whisper")
    assert env["variant"] == "cuda"


def test_cpu_lite_gets_tiny_ocr():
    plan = planner.plan_install(capabilities=["ocr"], doctor_data=DOCTOR_LITE)
    assert ids(plan) == ["ocr-ppocrv6-tiny"]


def test_scene_is_builtin():
    plan = planner.plan_install(capabilities=["scene"], doctor_data=DOCTOR_CPU)
    assert ids(plan) == [] and plan["nothing_to_do"]


def test_explicit_gpu_model_without_gpu_is_insufficient_hardware():
    with pytest.raises(InsufficientHardware):
        planner.plan_install(model_ids=["asr-faster-whisper-large-v3-turbo"], doctor_data=DOCTOR_CPU)


def test_experimental_only_when_explicit():
    plan = planner.plan_install(model_ids=["asr-funasr-nano-int8"], doctor_data=DOCTOR_CPU)
    assert ids(plan) == ["asr-funasr-nano-int8"]


def test_plan_reports_disk_shortage():
    plan = planner.plan_install(profile="full", doctor_data={**DOCTOR_CPU, "disk_free_gb": 0.1})
    assert plan["disk_ok"] is False


def test_api_install_requires_confirmation(monkeypatch):
    monkeypatch.setattr(planner, "plan_install", lambda *a, **k: {
        "items": [{"kind": "model", "id": "x"}], "license_acceptance_required": [], "disk_ok": True,
        "nothing_to_do": False, "confirm_command": "vidroute install --yes", "accept_command": None})
    env = api_install()
    assert not env.ok and env.exit_code == 3 and env.error["code"] == "needs_confirmation"
    assert env.data["plan"]["items"][0]["id"] == "x"


def test_api_install_restricted_license_needs_acceptance(monkeypatch):
    monkeypatch.setattr(planner, "plan_install", lambda *a, **k: {
        "items": [], "license_acceptance_required": ["gated-model"], "disk_ok": True, "nothing_to_do": False,
        "confirm_command": "c", "accept_command": "vidroute install --accept-license gated-model --yes"})
    env = api_install(yes=True)
    assert env.exit_code == 3 and env.error["code"] == "license_not_accepted"
    assert "--accept-license gated-model" in env.error["hint"]


def test_restricted_license_is_flagged_in_plan():
    raw = json.loads(json.dumps(_raw_manifest("http://127.0.0.1:1/")))
    raw["models"][0]["license"]["restricted"] = True
    manifest = parse_manifest(raw)
    plan = planner.plan_install(model_ids=["test-model"], doctor_data=DOCTOR_CPU, manifest=manifest)
    assert plan["license_acceptance_required"] == ["test-model"]
    plan = planner.plan_install(model_ids=["test-model"], accept_licenses=["test-model"], doctor_data=DOCTOR_CPU,
                                manifest=manifest)
    assert plan["license_acceptance_required"] == []


# ------------------------------------------------------------- model install

FILE_A = b"model-weights-" * 1000
FILE_B = b"tokens\n" * 100


def _sha(b):
    return hashlib.sha256(b).hexdigest()


def _tarball():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:bz2") as tf:
        for name, data in (("pkg/a.onnx", FILE_A), ("pkg/b.txt", FILE_B), ("pkg/README", b"x")):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


TARBALL = _tarball()


def _raw_manifest(base, files_first=False):
    sources = [
        {"type": "github", "kind": "archive", "url": base + "pkg.tar.bz2", "size": len(TARBALL),
         "sha256": _sha(TARBALL), "strip_prefix": "pkg/"},
        {"type": "hf", "kind": "files", "base_url": base + "files/"},
    ]
    if files_first:
        sources.reverse()
    return {
        "schema": "vidroute/manifest/1",
        "allowed_domains": ["127.0.0.1"],
        "env_groups": {"g": {"description": "", "python": "3.12", "lock": "envs/sherpa.lock.txt", "import_check": "pass"}},
        "models": [{
            "id": "test-model", "capability": "asr", "engine": "none", "env_group": "g", "package": "optional",
            "status": "default", "languages": ["multi"], "size_mb": 0.1, "tiers": ["cpu-lite"],
            "license": {"spdx": "MIT", "restricted": False, "gated": False, "name": "MIT", "url": "https://x"},
            "files": [{"path": "a.onnx", "size": len(FILE_A), "sha256": _sha(FILE_A)},
                      {"path": "b.txt", "size": len(FILE_B), "sha256": _sha(FILE_B)}],
            "sources": sources, "verified_at": "2026-10-21",
        }],
    }


class _Mirror(http.server.BaseHTTPRequestHandler):
    routes: dict = {}

    def log_message(self, *a):
        pass

    def do_GET(self):  # noqa: N802
        data = self.routes.get(self.path)
        if data is None:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def mirror():
    _Mirror.routes = {"/pkg.tar.bz2": TARBALL, "/files/a.onnx": FILE_A, "/files/b.txt": FILE_B}
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Mirror)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/"
    httpd.shutdown()
    httpd.server_close()


def test_install_from_archive_then_verify_and_remove(mirror, isolated_home):
    manifest = parse_manifest(_raw_manifest(mirror))
    entry = manifest.model("test-model")
    res = install_model(entry, manifest, Ctx(), downloader=Downloader(["127.0.0.1"], retries=1, backoff=0))
    assert res["status"] == "installed" and res["source"] == "github:archive"
    assert (entry.model_dir / "a.onnx").read_bytes() == FILE_A
    assert not (entry.model_dir / "README").exists()  # only manifest files are extracted
    assert verify_model(entry)["ok"]
    assert install_model(entry, manifest, Ctx())["status"] == "already_installed"
    (entry.model_dir / "b.txt").write_bytes(b"tampered")
    assert not verify_model(entry)["ok"]
    assert remove_model(entry) and not entry.model_dir.exists()


def test_install_falls_back_from_broken_archive_to_files(mirror, isolated_home):
    _Mirror.routes["/pkg.tar.bz2"] = b"not the archive"
    manifest = parse_manifest(_raw_manifest(mirror))
    entry = manifest.model("test-model")
    res = install_model(entry, manifest, Ctx(), downloader=Downloader(["127.0.0.1"], retries=1, backoff=0))
    assert res["status"] == "installed" and res["source"] == "hf"
    assert verify_model(entry)["ok"]


def test_failed_install_leaves_no_partial_model(mirror, isolated_home):
    _Mirror.routes = {}
    manifest = parse_manifest(_raw_manifest(mirror))
    entry = manifest.model("test-model")
    with pytest.raises(Exception):
        install_model(entry, manifest, Ctx(), downloader=Downloader(["127.0.0.1"], retries=1, backoff=0))
    assert not entry.model_dir.exists()
    assert not list(entry.model_dir.parent.glob(".staging-*"))


def test_bundled_manifest_entries_point_at_real_env_groups():
    manifest = load_manifest()
    for m in manifest.models.values():
        for s in m.sources:
            if s.type == "bundled":
                assert s.env_group in manifest.env_groups and s.package


def test_force_rebuilds_worker_environments(monkeypatch):
    from vidroute.core.install import envs as envmod
    from vidroute.core.install import runner

    seen = []

    def fake_create_env(group, ctx, variant="cpu", force=False, net=None):
        seen.append((group.name, variant, force))
        return {"group": group.name, "status": "created", "path": "x"}

    monkeypatch.setattr(envmod, "create_env", fake_create_env)
    plan = {"items": [{"kind": "env", "id": "ocr", "variant": "cpu"}], "force": True}
    runner.execute(plan, Ctx(), smoke=False)
    assert seen == [("ocr", "cpu", True)]
