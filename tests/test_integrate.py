"""Connecting vidroute to agent hosts: detection, skill folders, MCP entries, removal — all in a fake home."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from vidroute import __version__
from vidroute.core import integrate
from vidroute.core.context import Ctx
from vidroute.core.integrate import hosts as hostmod
from vidroute.core.integrate import mcp as mcpmod
from vidroute.core.integrate import skills


@pytest.fixture
def home(user_home):
    return user_home


def no_commands(name):
    return None


def plan(hosts, **kw):
    return integrate.plan(hosts, which=kw.pop("which", no_commands), **kw)


def test_detection_by_folder_command_and_extension(home):
    (home / ".claude").mkdir()
    (home / ".cursor" / "extensions" / "saoudrizwan.claude-dev-3.40.0").mkdir(parents=True)
    which = {"opencode": Path("/usr/local/bin/opencode")}.get
    found = hostmod.detected_ids(which)
    assert found == ["claude-code", "opencode", "cursor", "cline"]  # ~/.cursor exists too
    assert hostmod.detect(hostmod.BY_ID["opencode"], which)["evidence"][0].startswith("opencode on PATH")
    assert integrate.select_hosts(["none"], which) == ([], "none")
    assert integrate.select_hosts(["claude", "kilocode"], which) == (["claude-code", "kilo"], "explicit")


def test_unknown_host_is_a_usage_error(home):
    from vidroute.core.errors import UsageError

    with pytest.raises(UsageError):
        integrate.select_hosts(["notepad"])


@pytest.mark.parametrize("hosts, expected", [
    (["claude-code", "opencode"], {"claude": ["claude-code", "opencode"]}),
    (["codex-cli", "opencode", "cursor", "roo", "kilo", "openclaw"],
     {"agents": ["codex-cli", "opencode", "cursor", "roo", "kilo", "openclaw"]}),
    (["claude-code", "codex-cli", "gemini-cli", "opencode"],
     {"claude": ["claude-code"], "agents": ["codex-cli", "gemini-cli", "opencode"]}),
    (["claude-code", "qwen-code", "cline"], {"claude": ["claude-code"], "qwen": ["qwen-code"], "cline": ["cline"]}),
    (["cursor"], {"agents": ["cursor"]}),
])
def test_fewest_skill_folders_reach_every_host(hosts, expected):
    assert integrate.choose_roots(hosts) == expected


def test_setup_copies_the_skill_and_the_copy_runs_without_path(home, tmp_path):
    p = plan(["claude-code", "codex-cli"])
    assert [(i["path"], i["action"]) for i in p["items"]] == [
        (str(home / ".claude" / "skills" / "vidroute"), "create"),
        (str(home / ".agents" / "skills" / "vidroute"), "create")]
    out = integrate.apply(p, Ctx())
    assert out["ok"] and len(out["next_steps"]) == 2
    copy = home / ".claude" / "skills" / "vidroute"
    assert (copy / "SKILL.md").is_file() and (copy / "scripts" / "vidroute.cmd").is_file()
    marker = skills.read_marker(copy)
    assert marker["version"] == __version__ and marker["python"] == sys.executable
    env = {**os.environ, "VIDROUTE_HOME": str(tmp_path / "vh"), "PYTHONPATH": "", "PATH": os.defpath}
    res = subprocess.run([sys.executable, str(copy / "scripts" / "vidroute.py"), "version", "--json"],
                         capture_output=True, text=True, env=env, timeout=60)
    assert res.returncode == 0, res.stdout + res.stderr
    assert json.loads(res.stdout)["data"]["version"] == __version__
    # second run: nothing to do
    again = plan(["claude-code", "codex-cli"])
    assert [i["action"] for i in again["items"]] == ["keep", "keep"] and again["changes"] == 0


def test_an_outdated_copy_is_refreshed(home):
    integrate.apply(plan(["claude-code"]), Ctx())
    copy = home / ".claude" / "skills" / "vidroute"
    (copy / "SKILL.md").write_text("old text", encoding="utf-8")
    record = skills.read_marker(copy)
    (copy / skills.MARKER).write_text(skills.marker_text({**record, "digest": "0" * 64, "version": "0.0.1"}),
                                      encoding="utf-8")
    p = plan(["claude-code"])
    assert p["items"][0]["action"] == "update"
    integrate.apply(p, Ctx())
    assert (copy / "SKILL.md").read_text(encoding="utf-8").startswith("---")


def test_a_hand_made_folder_is_never_overwritten(home):
    mine = home / ".agents" / "skills" / "vidroute"
    mine.mkdir(parents=True)
    (mine / "SKILL.md").write_text("my notes", encoding="utf-8")
    p = plan(["codex-cli"])
    assert p["items"][0]["action"] == "skip" and p["changes"] == 0
    forced = plan(["codex-cli"], force=True)
    assert forced["items"][0]["action"] == "replace"
    res = integrate.apply(forced, Ctx())["results"][0]
    assert res["status"] == "created" and Path(res["backup"], "SKILL.md").read_text(encoding="utf-8") == "my notes"
    assert skills.read_marker(mine) is not None


def test_removal_takes_out_only_what_vidroute_added(home):
    integrate.apply(plan(["claude-code", "codex-cli"]), Ctx())
    foreign = home / ".cline" / "skills" / "vidroute"
    foreign.mkdir(parents=True)
    (foreign / "SKILL.md").write_text("someone else's", encoding="utf-8")
    rp = integrate.plan_remove()
    assert sorted(i["path"] for i in rp["items"]) == sorted(
        [str(home / ".claude" / "skills" / "vidroute"), str(home / ".agents" / "skills" / "vidroute")])
    out = integrate.remove(rp, Ctx())
    assert out["ok"] and not (home / ".claude" / "skills" / "vidroute").exists() and foreign.exists()
    assert integrate.plan_remove()["changes"] == 0


def test_removal_for_one_host(home):
    integrate.apply(plan(["claude-code", "codex-cli"]), Ctx())
    rp = integrate.plan_remove(["claude-code"])
    assert [i["path"] for i in rp["items"]] == [str(home / ".claude" / "skills" / "vidroute")]


def test_claude_plugin_already_brings_the_skill(home):
    plugins = home / ".claude" / "plugins"
    plugins.mkdir(parents=True)
    (plugins / "installed_plugins.json").write_text(
        json.dumps({"version": 2, "plugins": {"vidroute@vidroute": [{"scope": "user"}]}}), encoding="utf-8")
    p = plan(["claude-code", "opencode"])
    assert [(i["path"], i["hosts"]) for i in p["items"]] == [(str(home / ".agents" / "skills" / "vidroute"), ["opencode"])]
    assert "plugin" in p["notes"][0]
    status = integrate.status(no_commands)
    claude = next(h for h in status["hosts"] if h["id"] == "claude-code")
    assert claude["skill"]["plugin"] and status["claude_plugin"]


def test_mcp_json_hosts_keep_other_servers(home, monkeypatch):
    monkeypatch.setattr(mcpmod, "server_command", lambda: ["/opt/py/bin/python", "-m", "vidroute.mcp_server"])
    cursor = home / ".cursor" / "mcp.json"
    cursor.parent.mkdir(parents=True)
    cursor.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}, indent=4) + "\n", encoding="utf-8")
    gemini = home / ".gemini" / "settings.json"
    gemini.parent.mkdir(parents=True)
    gemini.write_text('{\n  // a comment\n  "theme": "dark"\n}\n', encoding="utf-8")
    p = plan(["cursor", "gemini-cli", "opencode", "roo"], mcp=True, skill=False)
    actions = {i["host"]: i["action"] for i in p["items"]}
    assert actions == {"gemini-cli": "manual", "opencode": "add", "cursor": "add", "roo": "unsupported"}
    assert "mcpServers" in next(i for i in p["items"] if i["host"] == "gemini-cli")["manual"]
    integrate.apply(p, Ctx())
    data = json.loads(cursor.read_text(encoding="utf-8"))
    assert data["mcpServers"]["other"] == {"command": "x"}
    assert data["mcpServers"]["vidroute"] == {"command": "/opt/py/bin/python", "args": ["-m", "vidroute.mcp_server"]}
    assert cursor.read_text(encoding="utf-8").startswith('{\n    "mcpServers"')  # indentation kept
    assert (cursor.parent / "mcp.json.vidroute-backup").is_file()
    oc = json.loads((home / ".config" / "opencode" / "opencode.json").read_text(encoding="utf-8"))
    assert oc["$schema"] == "https://opencode.ai/config.json"
    assert oc["mcp"]["vidroute"] == {"type": "local", "command": ["/opt/py/bin/python", "-m", "vidroute.mcp_server"],
                                     "enabled": True}
    assert gemini.read_text(encoding="utf-8").startswith('{\n  // a comment')  # untouched
    # the user edits the cursor entry by hand: removal leaves it alone
    data["mcpServers"]["vidroute"]["args"].append("--debug")
    cursor.write_text(json.dumps(data), encoding="utf-8")
    rp = integrate.plan_remove()
    acts = {i["host"]: i["action"] for i in rp["items"] if i["kind"] == "mcp"}
    assert acts == {"cursor": "skip", "opencode": "remove"}
    integrate.remove(rp, Ctx())
    assert "vidroute" in json.loads(cursor.read_text(encoding="utf-8"))["mcpServers"]
    assert "vidroute" not in json.loads((home / ".config" / "opencode" / "opencode.json").read_text(encoding="utf-8"))["mcp"]


def test_mcp_cli_hosts_use_the_hosts_own_commands(home, monkeypatch):
    calls = []
    registered = set()
    exe = Path("/fake/claude")
    monkeypatch.setattr(mcpmod, "server_command", lambda: ["/opt/py/python", "-m", "vidroute.mcp_server"])
    monkeypatch.setattr(mcpmod, "cli_exe", lambda method, which=None: exe)
    monkeypatch.setattr(mcpmod, "cli_has_server", lambda method, e: method in registered)

    class Res:
        ok = True
        stdout = stderr = ""

    def add(method, e, cmd):
        calls.append(("add", method, cmd))
        registered.add(method)
        return Res()

    def remove(method, e):
        calls.append(("remove", method))
        registered.discard(method)
        return Res()

    monkeypatch.setattr(mcpmod, "cli_add", add)
    monkeypatch.setattr(mcpmod, "cli_remove", remove)
    registered.add("codex-cli")  # configured by hand before
    p = plan(["claude-code", "codex-cli"], mcp=True, skill=False)
    assert {i["host"]: i["action"] for i in p["items"]} == {"claude-code": "add", "codex-cli": "skip"}
    integrate.apply(p, Ctx())
    assert calls == [("add", "claude-cli", ["/opt/py/python", "-m", "vidroute.mcp_server"])]
    assert plan(["claude-code"], mcp=True, skill=False)["items"][0]["action"] == "keep"
    rp = integrate.plan_remove()
    assert [(i["host"], i["action"]) for i in rp["items"]] == [("claude-code", "remove")]
    integrate.remove(rp, Ctx())
    assert calls[-1] == ("remove", "claude-cli") and "codex-cli" in registered


def test_mcp_cli_missing_gives_the_command_to_run(home, monkeypatch):
    monkeypatch.setattr(mcpmod, "server_command", lambda: ["/opt/py/python", "-m", "vidroute.mcp_server"])
    item = plan(["claude-code"], mcp=True, skill=False)["items"][0]
    assert item["action"] == "manual"
    assert item["manual"] == "claude mcp add --scope user vidroute -- /opt/py/python -m vidroute.mcp_server"


def test_server_command_is_absolute_and_starts_the_server(home):
    cmd = mcpmod.server_command()
    assert Path(cmd[0]).is_absolute()
    init = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                       "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                  "clientInfo": {"name": "t", "version": "0"}}})
    res = subprocess.run(cmd, input=init + "\n", capture_output=True, text=True, timeout=60,
                         env={**os.environ, "PYTHONPATH": ""})
    assert json.loads(res.stdout.splitlines()[0])["result"]["serverInfo"]["name"] == "vidroute"


def test_doctor_reports_hosts_and_outdated_copies(home, monkeypatch):
    from vidroute.core import doctor

    (home / ".claude").mkdir()
    integrate.apply(plan(["claude-code"]), Ctx())
    copy = home / ".claude" / "skills" / "vidroute"
    record = skills.read_marker(copy)
    (copy / skills.MARKER).write_text(skills.marker_text({**record, "digest": "1" * 64}), encoding="utf-8")
    ctx = Ctx()
    data = doctor.run(offline=True, ctx=ctx)
    row = next(h for h in data["integrations"]["hosts"] if h["id"] == "claude-code")
    assert row["detected"] and row["skill"]["managed"] and not row["skill"]["current"]
    assert any(w["code"] == "skill_outdated" for w in ctx.warnings)
