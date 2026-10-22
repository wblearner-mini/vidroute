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


@pytest.fixture(scope="module")
def speech_videos(tmp_path_factory):
    """Slides narrated with the real speech recordings shipped with SenseVoice (zh.wav, en.wav)."""
    import shutil
    import sys

    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        pytest.skip("ffmpeg/ffprobe not on PATH")
    home = Path(os.environ.get("VIDROUTE_REAL_HOME") or Path.home() / ".vidroute")
    wavs = home / "models" / "asr-sensevoice-small-int8" / "test_wavs"
    if not (wavs / "zh.wav").exists() or not (wavs / "en.wav").exists():
        pytest.skip("SenseVoice test WAVs not installed")
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import media

    root = tmp_path_factory.mktemp("speech")
    return {lang: media.make_speech_video(root / f"speech_{lang}.mp4", [wavs / f"{lang}.wav"] * 2) for lang in ("zh", "en")}


def test_prepare_end_to_end_with_real_models(speech_videos, tmp_path):
    from vidroute.api import prepare

    zh = prepare(str(speech_videos["zh"]), llm="claude-opus-5-5", host="claude-code", task="minutes", out=str(tmp_path))
    assert zh.ok, zh.error
    root = Path(zh.data["out_dir"])
    transcript = (root / "transcript.md").read_text(encoding="utf-8")
    assert "9点" in transcript or "九点" in transcript, transcript
    if (root / "ocr.md").exists():
        assert "Quarterly Review" in (root / "ocr.md").read_text(encoding="utf-8")
    en = prepare(str(speech_videos["en"]), llm="deepseek-v4-pro", host="opencode", out=str(tmp_path))
    assert en.ok, en.error
    text = (Path(en.data["out_dir"]) / "timeline.md").read_text(encoding="utf-8").lower()
    # SenseVoice sometimes hears "pieces of code" for "pieces of gold" (Whisper does not): proofreading material.
    assert "chieftain" in text and "pieces of" in text, text
