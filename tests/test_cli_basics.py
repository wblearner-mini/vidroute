import json

from vidroute import __version__
from vidroute.cli import main


def run_json(capsys, *argv):
    code = main([*argv, "--json"])
    out = capsys.readouterr().out
    env = json.loads(out)
    assert env["schema"] == "vidroute/1"
    return code, env


def test_version_json(capsys):
    code, env = run_json(capsys, "version")
    assert code == 0 and env["ok"] and env["data"]["version"] == __version__


def test_json_flag_before_subcommand(capsys):
    code = main(["--json", "version"])
    env = json.loads(capsys.readouterr().out)
    assert code == 0 and env["ok"]


def test_usage_error_is_exit_1_with_envelope(capsys):
    code, env = run_json(capsys, "no-such-command")
    assert code == 1
    assert not env["ok"] and env["error"]["code"] == "invalid_input"
    assert env["error"]["hint"]


def test_config_roundtrip(capsys):
    code, env = run_json(capsys, "config", "set", "budget_fraction", "0.25")
    assert code == 0 and env["data"]["value"] == 0.25
    code, env = run_json(capsys, "config", "get", "budget_fraction")
    assert env["data"]["value"] == 0.25
    code, env = run_json(capsys, "config", "unset", "budget_fraction")
    assert env["data"]["value"] == 0.5


def test_config_rejects_unknown_key(capsys):
    code, env = run_json(capsys, "config", "set", "no_such_key", "1")
    assert code == 1 and env["error"]["code"] == "invalid_input"


def test_config_type_check(capsys):
    code, env = run_json(capsys, "config", "set", "allow_raw_video_handoff", "maybe")
    assert code == 1
