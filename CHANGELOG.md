# Changelog

All notable changes are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed

- Installers no longer need git, unzip or Python: the default source is a source archive, and `install.sh` unpacks
  the uv wheel with coreutils and gzip when nothing else is there. Stalled downloads fail after 20 s and move to the
  next source; a missing curl/wget is reported up front with the command to run.
- `vidroute setup` stops before downloading when ffmpeg is missing and cannot be installed for you.
- `--force-asr` now also runs when voice detection finds no speech.
- A damaged model is reported instead of silently being replaced by the next engine.
- Large title lines count as on-screen text, so sparse slides get OCR.

- Runs on the same output folder wait for each other instead of crashing; the second one reuses the results.
- Clear messages for empty, cut-off or damaged files, unwritable folders, a full disk and an ffmpeg that cannot run. A
  failed run no longer leaves an empty folder behind.
- A long run resumes after an interruption from the last 60 s of speech; speaker turns are kept.
- `setup` and `install` show their plan without a terminal too, and `setup` warns when another skill blocks a host.
- `doctor`, the tier and thread counts respect container limits and CPU affinity.

### Added

- Speaker labels (`Speaker 1`, `Speaker 2`, ...) in the transcript for `--task minutes` or `--diarize on`:
  new default model `diarize-pyannote-campplus` (34 MB, CPU).
- `omitted-shots.md` and a warning when the frame budget leaves out shots that stayed on screen for 5 s or more.
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
