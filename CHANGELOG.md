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

### Fixed

- Error hints named flags that do not exist (`install --retry`, `install --repair`); a test now checks
  every `vidroute ...` command quoted in the source against the real CLI.
- `vidroute install --force` now really rebuilds worker environments.

[Unreleased]: https://github.com/wblearner-mini/vidroute/commits/main
