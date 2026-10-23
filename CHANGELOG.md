# Changelog

All notable changes are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `vidroute doctor`: OS/CPU/RAM/GPU/ffmpeg/runtime detection with concurrent probes, 5 s timeouts,
  a 6-hour cache and tiers `cpu-lite` / `cpu-full` / `gpu-cuda`.
- `vidroute install`: install plans, allow-listed downloads with mirror fallback, resume and SHA-256
  checks, uv-managed worker environments, smoke tests, license acceptance, `models list|verify|remove`,
  `clean`.
- `vidroute llm resolve|set|unset|list|sync`, `host set|unset|list`: LLM × host capability
  resolution with sources for every value; bundled, cited profiles.
- `vidroute probe` and `vidroute plan`: fast video facts and an explainable two-layer plan
  (native-av / native-v / frames / text with recorded degradations).
- `vidroute prepare` / `run`: transcripts (subtitles first, VAD, language ID, resumable ASR),
  keyframes and contact sheets, OCR, timeline, transcoded/segmented video for native routes,
  `SUMMARY.md`, `ANALYSIS_GUIDE.md`, `handoff.json`, `segments.json`, `manifest.json`.
- `vidroute drill` for time ranges.
- MCP server `vidroute-mcp`; Agent Skill `skills/vidroute`; Claude Code plugin and marketplace
  manifests; `vidroute skill path`.
- Documentation in English and Chinese; Windows troubleshooting; routing reference.
- `vidroute setup`: one plan and one confirmation for ffmpeg, runtimes and models, the Agent Skill in
  every agent host found (Claude Code, Codex CLI, Gemini CLI, Qwen Code, opencode, Cursor, Cline,
  Roo Code, Kilo Code, OpenClaw; `--mcp` also registers the MCP server) and an end-to-end self-test;
  `setup --remove` undoes the host part. `doctor` reports which hosts see the skill.
- One-line installers `install.ps1` (Windows) and `install.sh` (Linux, macOS) that need no Python.
- `docs/AGENT_SETUP.md`: step-by-step setup instructions for an AI agent.
- Skill launchers `scripts/vidroute` (sh) and `scripts/vidroute.cmd` that need no `python` on PATH
  (they fall back to uv) and use the interpreter `vidroute setup` recorded.
- `--mirror cn` and automatic mirrors in mainland China for PyPI, uv-managed Python and models;
  config keys `pypi_index`, `python_mirror`, `ffmpeg_source`.
- Windows: a pinned portable FFmpeg 9.0.2 build when winget is unavailable or fails.
- Windows on Arm: x64 worker Python for the OCR and faster-whisper environments.

### Fixed

- Error hints named flags that do not exist (`install --retry`, `install --repair`); a test now checks
  every `vidroute ...` command quoted in the source against the real CLI.
- `vidroute install --force` now really rebuilds worker environments.

[Unreleased]: https://github.com/wblearner-mini/vidroute/commits/main
