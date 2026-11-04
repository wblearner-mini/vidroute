# Setting up vidroute — instructions for an AI agent

You are a coding agent (Claude Code, Codex CLI, Gemini CLI, Qwen Code, opencode, Cursor, …) and your
user asked you to install vidroute or connect it to their tools. Follow these steps in order. Run every
vidroute command with `--json` and read **stdout** (one JSON envelope; logs are on stderr). Never pass
`--yes` before the user agreed to the plan you showed them.

## 1. Is vidroute installed?

Run `vidroute version --json`. If that works, go to step 3.

## 2. Install it (needs only uv)

vidroute's only prerequisite is [uv](https://docs.astral.sh/uv/); uv brings its own Python.

1. `uv --version`. If uv is missing, ask the user, then install it:
   - Windows: `winget install -e --id astral-sh.uv`
     (without winget: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`)
   - macOS: `brew install uv` · Linux and macOS: `curl -LsSf https://astral.sh/uv/install.sh | sh`

   Your shell keeps its old PATH, so call uv by its full path afterwards: Windows
   `%USERPROFILE%\.local\bin\uv.exe` or `%LOCALAPPDATA%\Microsoft\WinGet\Links\uv.exe`, elsewhere
   `~/.local/bin/uv`.
2. `uv tool install https://github.com/wblearner-mini/vidroute/archive/refs/heads/main.tar.gz`
   (needs no git; in mainland China set
   `UV_DEFAULT_INDEX=https://mirrors.aliyun.com/pypi/simple/` for this command).
3. vidroute is now in `~/.local/bin` (Windows: `%USERPROFILE%\.local\bin\vidroute.exe`). Use that full
   path for the rest of this session.

People without an agent can do steps 2–4 with one script instead: `install.ps1` (Windows) or
`install.sh` (Linux, macOS) from the repository; see the README.

## 3. Plan the setup and ask once

Run `vidroute setup --json`. Useful options:

- `--host <id>` (repeatable) connects only those hosts; default: every host found on this machine.
  Ids: `claude-code`, `codex-cli`, `gemini-cli`, `qwen-code`, `opencode`, `cursor`, `cline`, `roo`,
  `kilo`, `openclaw`.
- `--mcp` also registers the MCP server (the skill alone is enough for all of these hosts).
- `--mirror cn` uses mirrors in mainland China (picked automatically when huggingface.co is blocked).

It downloads nothing. If ffmpeg is missing and cannot be installed here (Linux, macOS) it exits **2** with
`ffmpeg_missing`: tell the user `error.hint`, and run this step again once it is installed. Otherwise it exits **3**.
`data.plan` holds:

- `install.items`: ffmpeg (when missing), uv, the Python runtime, worker environments and models, with
  sizes, sources and licenses; `install.total_download_mb` is the total (about 0.85 GB the first time).
- `integration.items`: the skill folders and MCP entries it will create or update. These are in the
  hosts' own configuration folders, outside vidroute's data folder.

Summarize the plan for the user in a few lines and **ask**. Only after a yes, run the command in
`error.hint` (the same command with `--yes`).

## 4. Check the result

- Exit 0: `data.selftest.status` is `passed` (a generated clip went through transcription, OCR and
  keyframes). Tell the user `data.next_steps`: usually starting a new session of each host, so it loads
  the skill.
- Exit 5: report `error.message`; running the same command again resumes where it stopped.

`vidroute doctor --json` shows the state any time: `data.capabilities` (what runs locally) and
`data.integrations.hosts` (which hosts see the skill, and whether it is current).

## Undo

`vidroute setup --remove --json` exits 3 with what it would take out of the hosts (only what vidroute
added). Ask, then run it with `--yes`. Models stay installed; `vidroute models remove` deletes them.

## Rules

- Let vidroute change host configuration; do not edit those files yourself. It keeps a backup of every
  JSON file it changes and never touches entries it did not create.
- Do not debug vidroute's internals. If a step fails, report the envelope's `error.message` and
  `error.hint` to the user.
