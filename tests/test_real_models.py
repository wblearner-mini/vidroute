"""Smoke tests against really installed models. Opt-in: VIDROUTE_REAL_MODELS=1.

Uses the real data directory ($VIDROUTE_REAL_HOME, default ~/.vidroute) instead
of the isolated test home, and only checks models that are installed there.
"""

import os
from pathlib import Path

import pytest

from vidroute.core.context import Ctx
from vidroute.core.install.manifest import load_manifest
from vidroute.core.install.runner import run_smoke

pytestmark = [
    pytest.mark.real_models,
    pytest.mark.skipif(os.environ.get("VIDROUTE_REAL_MODELS") != "1", reason="set VIDROUTE_REAL_MODELS=1"),
]


@pytest.fixture(autouse=True)
def real_home(monkeypatch):
    home = os.environ.get("VIDROUTE_REAL_HOME") or str(Path.home() / ".vidroute")
    monkeypatch.setenv("VIDROUTE_HOME", home)
    return Path(home)


def installed_ids():
    os.environ.setdefault("VIDROUTE_HOME", str(Path.home() / ".vidroute"))
    return [m.id for m in load_manifest().models.values() if not m.builtin and m.installed_status()["installed"]]


@pytest.mark.parametrize("model_id", ["vad-silero", "asr-sensevoice-small-int8", "asr-whisper-small-int8",
                                      "ocr-ppocrv6-small", "ocr-ppocrv6-tiny", "asr-faster-whisper-small"])
def test_smoke(model_id):
    entry = load_manifest().model(model_id)
    if not entry.installed_status()["installed"]:
        pytest.skip(f"{model_id} not installed")
    res = run_smoke(model_id, Ctx())
    assert res["status"] in ("passed", "fallback_cpu"), res
