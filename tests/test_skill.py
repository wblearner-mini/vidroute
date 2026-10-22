"""The Agent Skill, its launcher and the Claude Code plugin manifests."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from vidroute import __version__

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "vidroute"
LAUNCHER = SKILL / "scripts" / "vidroute.py"


def _frontmatter(text):
    yaml = pytest.importorskip("yaml")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert m, "SKILL.md must start with YAML frontmatter"
    return yaml.safe_load(m.group(1))


def test_skill_follows_the_agent_skills_spec():
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    meta = _frontmatter(text)
    assert meta["name"] == SKILL.name == "vidroute"
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", meta["name"]) and len(meta["name"]) <= 64
    assert 0 < len(meta["description"]) <= 1024 and "Use when" in meta["description"]
    assert len(meta.get("compatibility", "")) <= 500
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in meta["metadata"].items())
    assert len(text.splitlines()) < 500
    for link in re.findall(r"\]\(([^)#]+)\)", text):  # every relative link resolves inside the skill
        if not link.startswith("http"):
            assert (SKILL / link).is_file(), link
    assert "data, not instructions" in text and "--accept-raw-video-handoff" in text


def test_reference_validator_accepts_the_skill():
    validator = pytest.importorskip("skills_ref.validator")
    assert validator.validate(SKILL) == []


def test_plugin_manifests():
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    assert plugin["name"] == "vidroute" and plugin["license"] == "MIT"
    # PEP 440 "0.1.0.dev0" <-> SemVer "0.1.0-dev.0"
    assert plugin["version"] == re.sub(r"\.dev(\d+)$", r"-dev.\1", __version__)
    assert market["owner"]["name"] and [p["name"] for p in market["plugins"]] == ["vidroute"]
    assert market["plugins"][0]["source"] == "./"


def test_launcher_runs_the_source_tree(tmp_path):
    env = {**os.environ, "VIDROUTE_HOME": str(tmp_path / "home"), "PYTHONPATH": ""}
    out = subprocess.run([sys.executable, str(LAUNCHER), "version", "--json"], capture_output=True, text=True,
                         env=env, timeout=60)
    assert out.returncode == 0, out.stderr
    data = json.loads(out.stdout)
    assert data["ok"] and data["data"]["version"] == __version__
    init = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                       "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                  "clientInfo": {"name": "t", "version": "0"}}})
    mcp = subprocess.run([sys.executable, str(LAUNCHER), "mcp"], input=init + "\n", capture_output=True, text=True,
                         env=env, timeout=60)
    assert json.loads(mcp.stdout.splitlines()[0])["result"]["serverInfo"]["name"] == "vidroute"


def test_skill_path_command_points_at_the_bundled_skill():
    from vidroute.api import skill_path

    env = skill_path()
    assert env.ok and Path(env.data["skill_md"]).is_file()
    assert Path(env.data["path"]).name == "vidroute"


def test_launcher_uses_the_installed_package_outside_the_source_tree(tmp_path):
    import shutil

    copy = tmp_path / "skills" / "vidroute"
    shutil.copytree(SKILL, copy)  # no ../../src next to it: like the copy bundled in the wheel
    env = {**os.environ, "VIDROUTE_HOME": str(tmp_path / "home"), "PYTHONPATH": str(ROOT / "src"),
           "PATH": os.defpath}  # no vidroute executable on PATH either
    out = subprocess.run([sys.executable, str(copy / "scripts" / "vidroute.py"), "version", "--json"],
                         capture_output=True, text=True, env=env, timeout=60)
    assert out.returncode == 0, out.stdout + out.stderr
    assert json.loads(out.stdout)["data"]["version"] == __version__
