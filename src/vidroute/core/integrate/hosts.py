"""Agent hosts vidroute can connect itself to, and where each keeps user-level skills and MCP servers.

Locations come from each host's own documentation, checked on 2026-10-23
(docs/DECISIONS.md, D-040). Most hosts now read the cross-tool folder
``~/.agents/skills``; Claude Code, Qwen Code and Cline only read their own.

A host counts as installed when its command is on PATH, its configuration
folder exists, or (for VS Code extensions) its extension folder exists.
Detection only looks; it never starts a host.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from vidroute.core.errors import UsageError
from vidroute.core.util import proc


def user_home() -> Path:
    return Path.home()


def _env_dir(var: str, default: Callable[[], Path]) -> Path:
    raw = os.environ.get(var)
    return Path(raw).expanduser() if raw else default()


def claude_dir() -> Path:
    return _env_dir("CLAUDE_CONFIG_DIR", lambda: user_home() / ".claude")


def codex_dir() -> Path:
    return _env_dir("CODEX_HOME", lambda: user_home() / ".codex")


def xdg_config_dir() -> Path:
    return _env_dir("XDG_CONFIG_HOME", lambda: user_home() / ".config")


def openclaw_dir() -> Path:
    return _env_dir("OPENCLAW_STATE_DIR", lambda: user_home() / ".openclaw")


# User-level skill folders, by short name. Each host reads one or more of them.
SKILL_ROOTS: dict[str, Callable[[], Path]] = {
    "claude": lambda: claude_dir() / "skills",
    "agents": lambda: user_home() / ".agents" / "skills",
    "qwen": lambda: user_home() / ".qwen" / "skills",
    "cline": lambda: user_home() / ".cline" / "skills",
}

# Where VS Code and its forks keep extensions (Cline, Roo Code and Kilo Code are extensions).
VSCODE_HOMES = (".vscode", ".vscode-insiders", ".vscode-oss", ".cursor", ".windsurf")


@dataclass(frozen=True)
class Host:
    id: str
    name: str
    executables: tuple[str, ...]
    config_dirs: tuple[Callable[[], Path], ...]
    skill_roots: tuple[str, ...]  # SKILL_ROOTS this host reads, the one to prefer first
    mcp: str | None  # how vidroute registers its MCP server there (see integrate.mcp), None: not supported
    reload: str  # what the user does afterwards so the host picks the skill / server up
    vscode_extension: str | None = None  # extension folder prefix "<publisher>.<name>"


HOSTS: tuple[Host, ...] = (
    Host("claude-code", "Claude Code", ("claude",), (claude_dir,), ("claude",), "claude-cli",
         "新开一个 Claude Code 会话（或在已打开的会话中运行 /reload-skills）"),
    Host("codex-cli", "Codex CLI", ("codex",), (codex_dir,), ("agents",), "codex-cli",
         "如果技能没有出现，请重启 Codex"),
    Host("gemini-cli", "Gemini CLI", ("gemini",), (lambda: user_home() / ".gemini",), ("agents",), "gemini-json",
         "重启 Gemini CLI"),
    Host("qwen-code", "Qwen Code", ("qwen",), (lambda: user_home() / ".qwen",), ("qwen",), "qwen-json",
         "重启 Qwen Code"),
    Host("opencode", "opencode", ("opencode",), (lambda: xdg_config_dir() / "opencode",), ("agents", "claude"),
         "opencode-json", "重启 opencode"),
    Host("cursor", "Cursor", ("cursor-agent", "cursor"), (lambda: user_home() / ".cursor",), ("agents", "claude"),
         "cursor-json", "重启 Cursor（或重新加载其窗口）"),
    Host("cline", "Cline", ("cline",), (lambda: user_home() / ".cline",), ("cline",), None,
         "重新加载编辑器窗口", vscode_extension="saoudrizwan.claude-dev"),
    Host("roo", "Roo Code", (), (lambda: user_home() / ".roo",), ("agents",), None,
         "重新加载编辑器窗口", vscode_extension="rooveterinaryinc.roo-cline"),
    Host("kilo", "Kilo Code", ("kilocode",), (lambda: user_home() / ".kilo", lambda: user_home() / ".kilocode"),
         ("agents",), None, "重新加载编辑器窗口", vscode_extension="kilocode.kilo-code"),
    Host("openclaw", "OpenClaw", ("openclaw",), (openclaw_dir,), ("agents",), None,
         "新开一个 OpenClaw 会话"),
)

BY_ID = {h.id: h for h in HOSTS}


def get(host_id: str) -> Host:
    """Look a host up by id or by an alias from routing/host-profiles.yaml."""
    from vidroute.core.llm.profiles import builtin_host_profiles, find_host

    key = host_id.strip().lower()
    if key not in BY_ID:
        profile = find_host(key, builtin_host_profiles())
        key = profile["id"] if profile else key
    if key not in BY_ID:
        raise UsageError(f"vidroute 无法自行接入宿主 {host_id!r}；支持的宿主：{', '.join(BY_ID)}",
                         hint="vidroute setup --host claude-code")
    return BY_ID[key]


def _extension_dirs(prefix: str) -> list[Path]:
    found = []
    for base in VSCODE_HOMES:
        ext = user_home() / base / "extensions"
        try:
            found += [p for p in ext.iterdir() if p.name.startswith(prefix + "-")]
        except OSError:
            continue
    return found


def detect(host: Host, which: Callable[[str], Path | None] = proc.which) -> dict[str, Any]:
    evidence = []
    for exe in host.executables:
        hit = which(exe)
        if hit:
            evidence.append(f"{exe} on PATH ({hit})")
    for folder in host.config_dirs:
        path = folder()
        if path.is_dir():
            evidence.append(f"{path} exists")
    if host.vscode_extension:
        evidence += [f"extension {p}" for p in _extension_dirs(host.vscode_extension)[:1]]
    return {"detected": bool(evidence), "evidence": evidence}


def detected_ids(which: Callable[[str], Path | None] = proc.which) -> list[str]:
    return [h.id for h in HOSTS if detect(h, which)["detected"]]
