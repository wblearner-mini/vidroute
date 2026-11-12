"""Worker protocol: start-up, calls, progress, errors, crashes, cancellation, stdout hygiene."""

import sys
import threading
import time
from pathlib import Path

import pytest

from vidroute.core.context import Ctx
from vidroute.core.engines import workerclient
from vidroute.core.engines.workerclient import WorkerClient, WorkerError
from vidroute.core.errors import Cancelled, NeedsInstall
from vidroute.core.install.manifest import EnvGroup

WORKERS = Path(workerclient.__file__).resolve().parents[2] / "workers"

TEST_WORKER = r'''
import importlib.util, os, sys, time
from pathlib import Path
spec = importlib.util.spec_from_file_location("p", PROTOCOL)
P = importlib.util.module_from_spec(spec); spec.loader.exec_module(P)

def echo(p, rid):
    return {"echo": p}

def count(p, rid):
    for i in range(p["n"]):
        P.progress(rid, i + 1, p["n"], "counting")
    return {"done": p["n"]}

def noisy(p, rid):
    print("library chatter on stdout")       # must not break the protocol
    os.write(1, b"raw fd write\n")
    return {"ok": True}

def boom(p, rid):
    raise ValueError("bad input")

def die(p, rid):
    os._exit(3)

def slow(p, rid):
    time.sleep(30)

P.serve({"echo": echo, "count": count, "noisy": noisy, "boom": boom, "die": die, "slow": slow}, {"worker": "test"})
'''


@pytest.fixture
def fake_env(tmp_path, monkeypatch):
    """An 'env' whose python is the current interpreter, running a test worker."""
    script = tmp_path / "test_worker.py"
    script.write_text(TEST_WORKER.replace("PROTOCOL", repr(str(WORKERS / "_protocol.py"))), encoding="utf-8")
    monkeypatch.setattr(workerclient, "script", lambda name: script)
    group = EnvGroup(name="test", description="", python="3", lock="", import_check="pass")
    monkeypatch.setattr(EnvGroup, "python_exe", property(lambda self: Path(sys.executable)))
    return group


def test_call_and_progress(fake_env):
    seen = []
    ctx = Ctx(on_progress=lambda s, d, t, m: seen.append((s, d, t)))
    with WorkerClient(fake_env, "test", ctx, stage="unit") as w:
        assert w.info["worker"] == "test"
        assert w.call("echo", {"x": "中文 ✓"}) == {"echo": {"x": "中文 ✓"}}
        assert w.call("count", {"n": 3}) == {"done": 3}
        assert w.call("noisy") == {"ok": True}
        assert w.call("echo", {"after": "noise"}) == {"echo": {"after": "noise"}}
    assert ("unit", 3, 3) in seen


def test_worker_error_is_structured(fake_env):
    with WorkerClient(fake_env, "test") as w:
        with pytest.raises(WorkerError) as exc:
            w.call("boom")
        assert "ValueError: bad input" in exc.value.message
        assert "Traceback" in exc.value.data["traceback"]
        assert w.call("echo", {"still": "alive"})["echo"]["still"] == "alive"


def test_worker_crash_is_detected(fake_env):
    with WorkerClient(fake_env, "test") as w:
        with pytest.raises(WorkerError) as exc:
            w.call("die")
        assert "意外退出" in exc.value.message


def test_cancellation_kills_worker(fake_env):
    ctx = Ctx()
    with WorkerClient(fake_env, "test", ctx) as w:
        threading.Timer(0.5, ctx.cancel_event.set).start()
        start = time.monotonic()
        with pytest.raises(Cancelled):
            w.call("slow")
        assert time.monotonic() - start < 5
        assert w.proc.poll() is not None


def test_call_timeout(fake_env):
    with WorkerClient(fake_env, "test") as w:
        with pytest.raises(WorkerError) as exc:
            w.call("slow", timeout=0.5)
        assert "超时" in exc.value.message


def test_missing_env_needs_install(tmp_path):
    group = EnvGroup(name="nope", description="", python="3.12", lock="", import_check="pass")
    with pytest.raises(NeedsInstall):
        WorkerClient(group, "sherpa").start()
