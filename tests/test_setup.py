"""`vidroute setup`: one plan, one confirmation, hosts connected, self-test; and `setup --remove`."""

import json
import shutil

import pytest

from vidroute import api, cli
from vidroute.core import setup as setupmod
from vidroute.core.context import Ctx

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                                  reason="ffmpeg/ffprobe not on PATH")


def test_setup_asks_first_then_connects_the_host(user_home, monkeypatch):
    env = api.setup(hosts=["claude-code"], install=False, selftest=False)
    assert not env.ok and env.error["code"] == "needs_confirmation" and int(env.exit_code) == 3
    assert env.error["hint"] == "vidroute setup --host claude-code --no-install --no-selftest --yes"
    assert not (user_home / ".claude" / "skills" / "vidroute").exists()  # nothing written before the yes
    env = api.setup(hosts=["claude-code"], install=False, selftest=False, yes=True)
    assert env.ok, env.error
    assert (user_home / ".claude" / "skills" / "vidroute" / "SKILL.md").is_file()
    assert env.data["next_steps"] and "Claude Code" in env.data["next_steps"][0]
    again = api.setup(hosts=["claude-code"], install=False, selftest=False)  # nothing left to confirm
    assert again.ok and again.data["executed"] is False


def test_default_plan_installs_speech_voice_and_screen_text(user_home, monkeypatch):
    plan = setupmod.plan(hosts=["none"], selftest=False)
    caps = {i.get("capability") for i in plan["install"]["items"] if i["kind"] == "model"}
    assert caps == {"asr", "vad", "ocr"} and plan["integration"]["hosts"] == []
    assert plan["confirm_command"] == "vidroute setup --host none --no-selftest --yes"


def test_cli_setup_and_remove_round_trip(user_home, monkeypatch, capsys):
    args = ["setup", "--host", "codex-cli", "--no-install", "--no-selftest", "--json"]
    assert cli.main(args) == 3
    plan = json.loads(capsys.readouterr().out)["data"]["plan"]
    assert plan["integration"]["items"][0]["action"] == "create"
    assert cli.main(args + ["--yes"]) == 0
    capsys.readouterr()
    assert (user_home / ".agents" / "skills" / "vidroute").is_dir()
    assert cli.main(["setup", "--remove", "--json"]) == 3
    assert json.loads(capsys.readouterr().out)["data"]["plan"]["changes"] == 1
    assert cli.main(["setup", "--remove", "--yes", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)["data"]
    assert out["results"][0]["status"] == "removed" and not (user_home / ".agents" / "skills" / "vidroute").exists()
    assert cli.main(["setup", "--remove"]) == 0  # nothing left: plain message, no prompt
    assert "has not added anything" in capsys.readouterr().out


def test_missing_ffmpeg_after_setup_is_reported_with_the_fix(user_home, monkeypatch):
    from vidroute.core.util import ffmpeg

    monkeypatch.setattr(ffmpeg, "detect", lambda timeout=5: ffmpeg.FFmpegInfo(False, error="ffmpeg not found"))
    env = api.setup(hosts=["claude-code"], install=False, yes=True)
    assert not env.ok and env.error["code"] == "ffmpeg_missing" and int(env.exit_code) == 2
    assert env.data["integration"]["ok"] and env.data["selftest"]["status"] == "skipped"


@needs_ffmpeg
def test_selftest_runs_the_pipeline_on_a_generated_clip(monkeypatch):
    from vidroute.core import selftest

    monkeypatch.setenv("VIDROUTE_FAKE_ENGINES", "1")
    res = selftest.run(Ctx())
    assert res["status"] == "passed", res
    assert {c["check"] for c in res["checks"]} >= {"outputs", "keyframes"}
    assert res["seconds"] < 120
