# Progress

Phases follow the build brief (§12). Each phase ends with passing tests, a
commit and an update here.

| phase | scope | status |
|---|---|---|
| 0 | research, decisions with evidence, scaffold, CI, schemas | ✅ done |
| 1 | `doctor`: system/GPU/ffmpeg/runtime detection, tiers, cache | ⏳ next |
| 2 | `install`: downloader, mirrors, verification, uv envs, smoke tests | — |
| 3 | `llm resolve`, profiles, `probe`, `plan` (two-layer routing) | — |
| 4 | local perception pipeline, `prepare` for D3/D4 | — |
| 5 | native routes, `handoff.json`, `drill`, raw-video consent | — |
| 6 | CLI/MCP/Skill shells, packaging, README | — |
| 6.5 | open-source release checklist, v0.1.0 | — |
| 8 | desktop app (optional, v0.2+) | — |
| 7 | extensions (D5 delegate, two-pass ASR, VLM, diarization) | — |

## Phase 0 — research and scaffold (2026-10-21)

* Re-checked every fact in brief §2 and every seed host profile against
  official docs/source, plus one live host test (Claude Code). Results and
  corrections: [docs/DECISIONS.md](docs/DECISIONS.md) (D-012, D-013).
* Chose and verified default models with real SHA-256s (D-005…D-008):
  Silero VAD, SenseVoice-Small int8 (2024-07-17), Whisper small int8,
  faster-whisper large-v3-turbo (GPU), PP-OCRv6 small via RapidOCR.
* Name check → renamed to `vidroute` (owner decision, D-000).
* Scaffold: stdlib-only kernel (`errors`, `envelope`, `paths`, `resources`,
  `config`, `state`, YAML-subset loader, subprocess helpers), thin CLI
  (`version`, `config`), data files (`routing/*.yaml`, `models/manifest.yaml`),
  JSON Schemas, CI for windows-latest + ubuntu (py3.10/3.13) with
  hash-locked dev dependencies and SHA-pinned actions.
* Tests: 58 passing (YAML loader vs PyYAML, schema validation of all data files,
  manifest integrity, CLI envelope/exit codes, no `shell=True`, stdlib-only kernel).
