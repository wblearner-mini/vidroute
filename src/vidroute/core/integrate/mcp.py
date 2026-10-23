"""Register vidroute's MCP server with an agent host (opt-in: ``vidroute setup --mcp``).

* ``claude-cli`` / ``codex-cli``: through the host's own command
  (``claude mcp add --scope user``, ``codex mcp add``); their configuration
  files are large state files or TOML, which vidroute does not edit.
* ``*-json``: Gemini CLI, Qwen Code, Cursor and opencode keep MCP servers in a
  JSON file; vidroute changes only its own ``vidroute`` key. A file that is not
  strict JSON (comments, trailing commas) is never rewritten: the user gets the
  snippet to paste instead. The first change keeps a ``.vidroute-backup`` copy.

An entry counts as vidroute's own only when state.json recorded adding it, so a
server the user configured by hand is never replaced or removed.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Callable

from vidroute.core.integrate import hosts as hostmod
from vidroute.core.integrate.skills import runtime
from vidroute.core.util import proc
from vidroute.core.util.jsonio import write_text_atomic

SERVER = "vidroute"
CLI_TIMEOUT = 60.0


def server_command() -> list[str]:
    """The command an MCP client runs: this interpreter with vidroute's MCP module (absolute, PATH-free)."""
    rt = runtime()
    if "pythonpath" not in rt:
        return [rt["python"], "-m", "vidroute.mcp_server"]
    launcher = Path(rt["pythonpath"]).parent / "skills" / "vidroute" / "scripts" / "vidroute.py"
    return [rt["python"], str(launcher), "mcp"]


def _stdio_entry(cmd: list[str]) -> dict[str, Any]:
    return {"command": cmd[0], "args": cmd[1:]}


def _opencode_entry(cmd: list[str]) -> dict[str, Any]:
    return {"type": "local", "command": cmd, "enabled": True}


# method -> (config file, key holding the servers, entry builder)
JSON_TARGETS: dict[str, tuple[Callable[[], Path], str, Callable[[list[str]], dict[str, Any]]]] = {
    "gemini-json": (lambda: hostmod.user_home() / ".gemini" / "settings.json", "mcpServers", _stdio_entry),
    "qwen-json": (lambda: hostmod.user_home() / ".qwen" / "settings.json", "mcpServers", _stdio_entry),
    "cursor-json": (lambda: hostmod.user_home() / ".cursor" / "mcp.json", "mcpServers", _stdio_entry),
    "opencode-json": (lambda: hostmod.xdg_config_dir() / "opencode" / "opencode.json", "mcp", _opencode_entry),
}

CLI_TARGETS: dict[str, dict[str, Any]] = {
    "claude-cli": {"exe": "claude", "where": "user scope", "add": ["mcp", "add", "--scope", "user", SERVER, "--"],
                   "remove": ["mcp", "remove", "--scope", "user", SERVER], "get": ["mcp", "get", SERVER]},
    "codex-cli": {"exe": "codex", "where": "~/.codex/config.toml", "add": ["mcp", "add", SERVER, "--"],
                  "remove": ["mcp", "remove", SERVER], "get": ["mcp", "get", SERVER]},
}


def snippet(method: str, cmd: list[str]) -> str:
    """What to do by hand when vidroute cannot change the host's configuration itself."""
    if method in CLI_TARGETS:
        t = CLI_TARGETS[method]
        return " ".join([t["exe"], *t["add"], *(_quote(c) for c in cmd)])
    path, key, build = JSON_TARGETS[method]
    return f"add to {path} under \"{key}\": " + json.dumps({SERVER: build(cmd)}, ensure_ascii=False)


def _quote(arg: str) -> str:
    return f'"{arg}"' if any(c in arg for c in " \t'") else arg


# ------------------------------------------------------------------- JSON files


def _load(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    """Parsed object, or None with the reason vidroute must not edit the file."""
    if not path.exists():
        return {}, None
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, ValueError) as exc:
        return None, f"{path} is not plain JSON ({type(exc).__name__}); vidroute does not rewrite it"
    if not isinstance(data, dict):
        return None, f"{path} does not hold a JSON object"
    return data, None


def _indent(path: Path) -> int:
    try:
        for line in path.read_text(encoding="utf-8").splitlines()[1:]:
            stripped = line.lstrip(" ")
            if stripped and len(stripped) < len(line):
                return len(line) - len(stripped)
    except OSError:
        pass
    return 2


def _write(path: Path, data: dict[str, Any]) -> None:
    indent = _indent(path)
    backup = path.with_name(path.name + ".vidroute-backup")
    if path.exists() and not backup.exists():
        shutil.copy2(path, backup)
    write_text_atomic(path, json.dumps(data, ensure_ascii=False, indent=indent) + "\n")


def json_state(method: str) -> dict[str, Any]:
    getter, key, _ = JSON_TARGETS[method]
    path = getter()
    data, problem = _load(path)
    if data is None:
        return {"file": str(path), "editable": False, "problem": problem, "entry": None}
    servers = data.get(key)
    entry = servers.get(SERVER) if isinstance(servers, dict) else None
    if servers is not None and not isinstance(servers, dict):
        return {"file": str(path), "editable": False, "problem": f"{key} in {path} is not an object", "entry": None}
    return {"file": str(path), "editable": True, "problem": None, "entry": entry}


def json_set(method: str, cmd: list[str]) -> dict[str, Any]:
    getter, key, build = JSON_TARGETS[method]
    path = getter()
    data, problem = _load(path)
    if data is None:
        raise ValueError(problem)
    if not data and method == "opencode-json":
        data["$schema"] = "https://opencode.ai/config.json"
    servers = data.setdefault(key, {})
    servers[SERVER] = build(cmd)
    _write(path, data)
    return servers[SERVER]


def json_remove(method: str) -> bool:
    getter, key, _ = JSON_TARGETS[method]
    path = getter()
    data, _problem = _load(path)
    if not data or not isinstance(data.get(key), dict) or SERVER not in data[key]:
        return False
    del data[key][SERVER]
    _write(path, data)
    return True


# --------------------------------------------------------------------- CLIs


def cli_exe(method: str, which: Callable[[str], Path | None] = proc.which) -> Path | None:
    return which(CLI_TARGETS[method]["exe"])


def cli_has_server(method: str, exe: Path) -> bool:
    res = proc.run([exe, *CLI_TARGETS[method]["get"]], timeout=CLI_TIMEOUT)
    return res.ok


def cli_add(method: str, exe: Path, cmd: list[str]) -> proc.Result:
    return proc.run([exe, *CLI_TARGETS[method]["add"], *cmd], timeout=CLI_TIMEOUT)


def cli_remove(method: str, exe: Path) -> proc.Result:
    return proc.run([exe, *CLI_TARGETS[method]["remove"]], timeout=CLI_TIMEOUT)
