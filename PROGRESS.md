# Progress

Phases follow the build brief (§12). Each phase ends with passing tests, a
commit and an update here.

| phase | scope | status |
|---|---|---|
| 0 | research, decisions with evidence, scaffold, CI, schemas | ✅ done |
| 1 | `doctor`: system/GPU/ffmpeg/runtime detection, tiers, cache | ✅ done |
| 2 | `install`: downloader, mirrors, verification, uv envs, smoke tests | ✅ done |
| 3 | `llm resolve`, profiles, `probe`, `plan` (two-layer routing) | ✅ done |
| 4 | local perception pipeline, `prepare` for D3/D4 | ✅ done |
| 5 | native routes, `handoff.json`, `drill`, raw-video consent | ✅ done |
| 6 | CLI/MCP/Skill shells, packaging, README | ✅ done |
| 6.5 | open-source release checklist, v0.1.0 | ⏳ next |
| 6.6 | zero-touch setup: `vidroute setup`, host integration, installers, mirrors | ✅ done |
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

## Phase 1 — doctor (2026-10-21)

* `vidroute doctor [--refresh] [--offline] [--json]`: OS, CPU model + AVX2
  (Linux `/proc/cpuinfo`, Windows registry + `IsProcessorFeaturePresent`,
  macOS `sysctl`), RAM/disk, ffmpeg version/encoders/filters, GPUs
  (`nvidia-smi` with compute capability, Windows `Win32_VideoController` via
  PowerShell with AdapterRAM marked approximate, Linux sysfs), uv, worker envs
  (import check), installed models, reusable Hugging Face/ModelScope caches,
  local Ollama/LM Studio, mirror reachability.
* All probes run concurrently with a 5 s timeout and never block; results are
  cached in `state.json` for 6 h (`--refresh` re-probes).
* Tier rules: `cpu-lite` (< 8 GB RAM, x86 without AVX2, or RAM unknown),
  `cpu-full`, `gpu-cuda` only after a passed CUDA smoke test with ≥ 4 GB VRAM
  (unknown VRAM is conservative), `gpu-vulkan` reserved.
* This machine: 0.9 s, Ryzen 7 8745H (AVX2), 19.8 GB, Radeon 780M iGPU,
  ffmpeg 8.0.1 → `cpu-full`. CI asserts doctor finishes in < 10 s on
  windows-latest and ubuntu.
* Tests: 88 passing (nvidia-smi/CIM/cpuinfo/meminfo fixtures, 14 tier boundary
  cases, hanging-probe timeout, cache).

## Phase 2 — install (2026-10-21)

* `vidroute install [--capability ...] [--profile auto|lite|full] [--model ID]
  [--mirror auto|github|hf|modelscope] [--accept-license ID] [--yes]
  [--dry-run] [--force]`, plus `models list|verify|remove` and `clean`.
* Downloader: domain allow-list checked on every redirect hop, HTTP Range
  resume (partials keyed by SHA-256), mirror fallback, SHA-256 + size check,
  atomic replace; safe tar/zip extraction (no traversal, no links).
* Worker envs with uv (Python 3.12 managed, hash-locked, wheels only), uv
  bootstrap into `$VIDROUTE_HOME/runtimes/uv` when missing.
* Workers: `sherpa` (Silero VAD, SenseVoice, Whisper), `ocr` (RapidOCR
  PP-OCRv6), `whisper` (faster-whisper CPU/CUDA with automatic CPU fallback);
  JSON-lines protocol with progress, cancellation and log capture.
* Engine adapters (`probe/ensure_installed/run/smoke_test`) + fake engines for
  offline CI (`VIDROUTE_FAKE_ENGINES=1`).
* **Acceptance (this Linux machine):** one command
  `vidroute install --capability asr --capability vad --capability ocr --capability scene --yes`
  installed the CPU baseline into `~/.vidroute` in 6 m 52 s (Python 3.12 runtime,
  `sherpa` + `ocr` envs, SenseVoice, Silero VAD, PP-OCRv6); all smoke tests
  passed. A download killed with SIGKILL at 87.8/112 MB resumed from 87.8 MB.
  Windows is exercised by the manual `real-install` workflow.
* Tests: 128 passing (+4 real-model smoke tests with `VIDROUTE_REAL_MODELS=1`).
* **Clean Windows acceptance (GitHub `windows-latest`, real-install workflow):**
  uv bootstrapped from PyPI, Python 3.12 + `sherpa`/`ocr` envs, SenseVoice,
  Silero VAD and PP-OCRv6 installed by one command; all smoke tests passed
  (job ≈ 2 min). Ubuntu passed too.

## Phase 3 — profiles, probe, plan (2026-10-22)

* `vidroute llm resolve|set|unset|list|sync`, `vidroute host set|unset|list`:
  capability precedence flag > user config > provider cache (opt-in
  `llm sync --provider openrouter`) > bundled profile > conservative default;
  every field reports its source. Effective capabilities = LLM ∩ host, with
  "limited by the host, not the model" made explicit. Host guessed from
  `CLAUDECODE`, `GEMINI_CLI`, `QWEN_CODE_*`, `CODEX_SANDBOX` when `--host` is
  missing (with a warning).
* `vidroute probe`: streams/subtitles/rotation via ffprobe, `speech_ratio` from
  Silero VAD on sampled windows (energy fallback), `text_density` from PP-OCR
  detection on 8 frames (heuristic fallback), `visual_change`; ~3 s, cached.
* `vidroute plan`: layer 1 picks native-av / native-v / frames / text from
  LLM ∩ host, then admission (privacy, limits after transcode/segmentation,
  token budget) with the explicit chain native → frames → text and a D5
  suggestion when the host is the bottleneck; layer 2 plans R1–R6 steps
  (subtitles first, no ASR without speech, ASR chain by language and tier, OCR
  by text density, keyframe budget). Each step carries reason, alternatives,
  est. seconds, est. input tokens and `uploads_raw_video`; `plan.json` matches
  `schemas/plan.schema.json`.
* Same synthetic video, different plans: gemini/gemini-cli → native-av,
  qwen3.6-plus/qwen-code → native-v, claude/claude-code → frames,
  deepseek-v4-pro/opencode → text.
* Tests: 179 passing (41 table-driven routing cases covering D1–D4 and every
  degradation; probe on synthetic media incl. a Chinese path with spaces).

## Phase 4 — local pipeline and `prepare` for D3/D4 (2026-10-22)

* `vidroute prepare <video> [--plan] [--route] [--out] [--accept-raw-video-handoff]`
  and `vidroute run` (= plan + prepare). Output folder per brief §9:
  `SUMMARY.md`, `ANALYSIS_GUIDE.md`, `handoff.json` (schema
  `vidroute/handoff/1`), `plan.json`, `manifest.json` (files + SHA-256, engines
  with licenses), `audio/16k.wav`, `transcript.{md,srt,json}`, `frames/`,
  `contact-sheets/` (+ `index.md`), `ocr.{md,json}`, `timeline.md`.
* Transcript: embedded text subtitles first (R1, coverage against VAD speech),
  no ASR without speech (R2), language identification on the first 30 s and
  the ASR chain by language and tier with fallback (R3); VAD chunks ≤ 25 s,
  resumable per 5-minute batch; global timestamps everywhere.
* Keyframes (D-030): one decoding pass into 64 px thumbnails, cell-difference
  shots, repeated-picture folding, coverage-first budget
  `min(max_frames, max_images)`; full-resolution JPEGs, labelled thumbnails and
  sharper OCR copies in one parallel ffmpeg pass per frame; contact sheets
  with ffmpeg `tile`.
* OCR on keyframes only (R4), timeline merging pictures/on-screen text/speech,
  analysis guide rendered per route from `references/analysis-guide.template.md`.
* Every step is fingerprinted in `.work/steps.json`: re-runs reuse finished
  steps; native routes stop with exit 3 unless `--accept-raw-video-handoff`.
* **Acceptance (real models, this machine):** synthetic clips (slides with
  subtitles, silent, no audio, Chinese path with spaces); speech clips with the
  real SenseVoice zh/en recordings (zh → SenseVoice, en → language ID switches
  to Whisper); a real 4-minute 1080p Chinese screen-recording tutorial (57 s,
  37 frames, 4 sheets); a real 19-minute Chinese classroom lecture (64 s:
  ASR 42 s for 13 min of speech, keyframes 3 s, OCR 16 s; 60/60 frames). A real
  English video is still pending (KNOWN_ISSUES.md).
* Tests: 201 passing (+1 real-model end-to-end test): chunking, SRT, keyframe
  shots/dedup/budget, timeline sections, template rendering, handoff schema,
  budget from `max_images`, text route without audio/speech, resume, plan reuse,
  exit 3 for native routes, Unicode paths with spaces.

## Phase 5 — native routes, handoff and drill (2026-10-22)

* `prepare` for D1 `native-av` / D2 `native-v` (D-034): copy when the file fits,
  audio-free remux for `native-v`, otherwise H.264 transcode (shorter side,
  fps cap, ABR within the per-file limit, mono AAC) and up to 8 segments with
  3 s overlap; every file is measured against the limit, over-size encodes are
  redone smaller (≤ 3 passes), and a part that still does not fit sends the
  run down the chain to `frames` with the reason in `degraded_from`.
  `segments.json` (`vidroute/segments/1`) keeps every part's global offset.
* Raw-media consent: native routes exit 3 without `--accept-raw-video-handoff`
  (interactive terminals get a y/N prompt); the "frames + audio track" variant
  uses the same flag and otherwise falls back to frames without audio.
* `ANALYSIS_GUIDE.md` now keys its proofreading rules on whether the model can
  hear (native-av, attached audio) rather than on the route name.
* `vidroute drill <out-dir> --range START-END [--fps N] [--asr] [--ocr]
  [--max-frames N]` (D-035): denser frames + contact sheet, fresh transcript and
  OCR for the range, global timestamps, paths relative to the output folder.
* Energy VAD ignores sound bursts < 0.25 s (fixes a Windows-only CI failure:
  an AAC encoder transient in a silent clip counted as speech).
* **Acceptance:** 78 s clip with a 30 s / 1 MB profile → 3 parts of 28 s
  (offsets 0/25/50 s, 0.3 MB each); the 19-minute 201 MB HEVC lecture for
  `gemini-3.8-flash` → 2 parts of 13.1 MB (limit 14.6 MB raw after base64),
  360p@10 fps, encoded in 16 s; files forced not to fit → frames with the
  reason recorded; drill on the 4-minute recording (40 s range: 10 frames,
  re-transcribed and OCR'd in 15 s).
* Tests: 210 passing (native copy/remux/segments, fallback, consent for audio,
  drill ranges, energy VAD bursts).

## Phase 6 — shells and distribution (2026-10-22)

* **MCP server** `vidroute-mcp` (D-036): stdlib JSON-RPC over stdio, tools
  `doctor`, `install` (`confirm`), `list_models`, `resolve_llm`, `probe_video`,
  `plan_video`, `prepare_video` (`accept_raw_video_handoff`), `drill_video`,
  `read_outputs`; progress notifications, cancellation, server instructions with
  the workflow. Verified with the official MCP SDK client (handshake 2025-11-25,
  tool calls, progress, image content, consent error).
* **Agent Skill** `skills/vidroute/` (D-037): SKILL.md (112 lines) with the
  workflow, consent rules, exit codes and hints; `references/hosts.md` (how to
  attach in each host) and `references/outputs.md`; launcher
  `scripts/vidroute.py` that runs from the plugin checkout, PATH or the installed
  package (`mcp` starts the server). Passes `skills-ref validate`.
* **Claude Code plugin + marketplace** manifests; `claude plugin validate .`
  passes. `vidroute skill path` prints the bundled skill folder.
* **README.md / README.zh-CN.md**: routes, 30-second start, agent setup (plugin,
  skill, MCP), CLI table, output layout, model/host support matrix, models and
  licenses, privacy and safety, platforms, known issues, development.
* **Packaging:** wheel 262 KB / sdist 248 KB; a clean venv runs `vidroute`,
  `doctor`, the bundled skill launcher and an MCP handshake (also in CI on
  Windows and Ubuntu).
* Tests: 219 passing (raw JSON-RPC, in-process progress/cancellation,
  `read_outputs` path safety, SDK end to end, skill spec and validator, plugin
  manifests, launcher).
* **Live host acceptance (owner-approved, 2026-10-22):** see "Acceptance
  follow-ups" below.

## Phase 6.5 — release preparation (2026-10-22)

* Release documents: `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md` (adopts Contributor
  Covenant 2.1), `SECURITY.md` (private advisories), `CHANGELOG.md`,
  `CITATION.cff`, `THIRD_PARTY_LICENSES.md` (models, external tools, every
  worker-env and dev package with its license), `docs/PROFILE_CONTRIBUTING.md`,
  `docs/RELEASING.md` (the §11.5 checklist), issue templates (bug, profile
  correction with required official source and date, feature) and a PR template.
* Supply chain (D-038): `tools/license_report.py` (all 87 pinned packages on the
  allow-list) and `tools/vuln_check.py` (osv.dev: no known vulnerabilities),
  `supply-chain.yml` (weekly + on lock changes, also checks hash pinning),
  Dependabot for SHA-pinned actions, `release.yml` (tag → build, checksums,
  GitHub release; PyPI only by manual dispatch with trusted publishing).
* Hygiene test: no model weights, media or binaries tracked in git.
* **Clean-machine re-test (GitHub `windows-latest` and `ubuntu-latest`, Real install
  workflow, 2026-10-22):** one-command CPU baseline install, `doctor` → `cpu-full`,
  smoke tests and the real-model end-to-end `prepare` (Chinese and English speech
  clips, OCR) passed on both. The CI package job installs the wheel in a clean venv
  and runs `doctor`, the bundled skill launcher and an MCP handshake on both OSes.
* **Waiting for the owner:** making the repository public, tagging `v0.1.0`
  (GitHub pre-release with sources and checksums) and the first PyPI upload.

## Acceptance follow-ups (2026-10-22)

* **Real English video** (owner-approved download): NASA's public-domain
  "Kate Rubins: Science on the Space Station" (Wikimedia Commons, 159 s, 1080p
  VP9/Opus WebM, 30 MB, SHA-1 checked; kept outside the repository).
  `frames` for Claude: 67 s end to end — language ID switched to Whisper, 9
  accurate segments, 40/40 keyframes from 309 shots, 4 contact sheets, OCR read
  the title cards. The one ASR slip ("Kate Rubens") is contradicted by OCR on
  frame 1 ("Kate Rubins") — exactly what the guide's correction list is for.
  `native-av` for Gemini: WebM → H.264 540p@10 fps + AAC, 12.3 MB under the
  14.6 MB limit, one part. `drill 02:10-02:20 --fps 1 --ocr`: 10 frames in 6 s.
* Timestamps now truncate like a player's clock (two frames at 137.5 s and
  138.5 s were both labelled 02:18 by rounding to even).
* **Live acceptance in two agent hosts**, headless, on a synthetic clip (slides
  + sample speech), with the models configured in each host:

  | host | shell | model (host config) | route | result |
  |---|---|---|---|---|
  | Claude Code 2.1.303 | MCP (`--mcp-config`, vidroute only) | deepseek-flash | text | ✅ resolve → doctor → plan → prepare → read guide/handoff/timeline → drill → minutes; 15 turns, 65 s |
  | Claude Code 2.1.303 | Skill (repo loaded with `--plugin-dir`) | deepseek-flash | text | ✅ same flow via `vidroute:vidroute`; 37 turns, 90 s |
  | opencode 1.19.3 | MCP (project `opencode.json`) | deepseek-v4.1-flash-free | frames | ✅ `read_outputs` delivered the contact sheet as image content; 6 steps |
  | opencode 1.19.3 | Skill (`.opencode/skills/vidroute`, vidroute on PATH) | deepseek-v4.1-flash-free | frames | ✅ read the contact sheet image directly |

  Every run wrote minutes with global `[mm:ss]` timestamps, a correction list
  and items to verify, and flagged that the sample speech does not match the
  slides instead of inventing content. Fixes from these runs: the skill now says
  `python3` (no `python` on many Linux/macOS systems) and tells agents not to
  debug vidroute internals (one run spent ~20 turns doing so); `prepare` with a
  `plan` path and no `out` writes next to that plan (an agent's outputs had
  landed in the default folder); the opencode host profile is now `verified`.

## Phase 6.6 — zero-touch setup (2026-10-23)

Owner request: fewer manual steps for other users, especially connecting agent
hosts; the repository stays private for now (D-039 … D-046).

* **`vidroute setup`**: one plan, one confirmation — ffmpeg, runtimes and models
  (default asr + vad + ocr + scene), the Agent Skill for every agent host found,
  optional MCP registration (`--mcp`), then an end-to-end self-test.
  `setup --remove` undoes the host part; `doctor` lists which hosts see the
  skill and warns about outdated copies.
* **Host integration** for Claude Code, Codex CLI, Gemini CLI, Qwen Code,
  opencode, Cursor, Cline, Roo Code, Kilo Code and OpenClaw, from each host's
  documentation: the fewest skill folders that reach every host
  (`~/.agents/skills` serves seven of them), ownership markers, the recorded
  interpreter, MCP through `claude`/`codex` commands or key-by-key JSON edits
  with backups. Nothing vidroute did not create is replaced or removed.
* **Launchers without Python on PATH**: `scripts/vidroute` (sh) and
  `scripts/vidroute.cmd` fall back to uv (also where installers put it before
  PATH is refreshed) and use the interpreter `setup` recorded.
* **Installers** `install.ps1` / `install.sh`: pinned uv (GitHub → PyPI →
  Aliyun mirror, SHA-256 checked) → `uv tool install` → `vidroute setup`.
* **Mainland China**: when huggingface.co is blocked, PyPI (Aliyun), Python
  builds (npmmirror) and models (ModelScope, hf-mirror) switch to mirrors;
  `--mirror cn` forces it; every download stays hash-checked.
* **Windows**: pinned portable FFmpeg 9.0.2 when winget is missing or fails;
  Windows on Arm gets x64 worker Pythons where wheels lack `win_arm64`.
* **Docs**: `docs/AGENT_SETUP.md` (instructions an agent follows), README
  install sections (en/zh), Windows troubleshooting, known issues.
* **CI**: every push runs the installer + `setup` + the copied skill's launcher
  without Python on PATH on Windows and Ubuntu; the manual Real install workflow
  gains an installer-driven job with models and the self-test.
* Tests: 278 passing (+51: detection, folder choice, markers, MCP JSON/CLI,
  removal, setup round trip, mirrors, portable FFmpeg, Windows on Arm,
  launchers, installer pins).
* **Acceptance (this Linux machine, sandboxed HOME, fresh VIDROUTE_HOME, real
  network in mainland China):** `sh install.sh --source . --no-setup` detected
  the blocked huggingface.co, fetched uv from the Aliyun PyPI mirror (SHA-256
  checked) and installed vidroute with `uv tool install`; then
  `vidroute setup --host claude-code --yes` installed Python 3.12 (npmmirror),
  the `sherpa` and `ocr` envs (Aliyun), SenseVoice, Silero VAD and PP-OCRv6
  (597 MB) in 6 min 24 s, created the Claude Code skill, and the self-test
  passed in 5.4 s. The copied skill's sh launcher ran `doctor` with an empty
  PATH through the recorded interpreter. With the existing models in
  `~/.vidroute` the self-test passes in 6.0 s.
* **Clean machines (GitHub `windows-latest` / `ubuntu-latest`, 2026-10-23):**
  CI on every push runs installer → `setup --host claude-code` → the copied
  skill's launcher with no Python on PATH → `setup --remove` (green on both).
  Real install workflow, new installer job: `install.ps1 -Source . -Yes`
  downloaded uv, installed vidroute, then `setup` installed FFmpeg through
  winget (found at once in `WinGet\Links`), the envs and models, created the
  Claude Code skill and passed the self-test (speech and OCR) in 12.6 s — about
  1 min 45 s for the whole job; Ubuntu passed the same way. The portable FFmpeg
  path is covered by tests with a local server; the runner had winget.


## Phase 6.7 — fixes from a user-perspective review (2026-11-04)

A review on a clean Ubuntu 24.04 container and with poisoned environments found installation and result-completeness
gaps (D-047 … D-050). The owner made the repository public the same day; tag, Release and PyPI remain open.

* **Installers:** no git, unzip or Python needed; missing curl/wget reported first; stalled transfers move on.
* **`setup`:** stops before any download when ffmpeg must come from the system package manager; hints name the
  package manager found and drop `sudo` for root.
* **Downloads:** a source under 50 KB/s for 30 s is left for the next one, keeping partial data; if nothing else works, a
  second pass accepts the slow one.
* **Results:** `--force-asr` works when VAD hears nothing; damaged models are reported; `omitted-shots.md` and a warning
  for long shots without a frame; large title lines count as text for OCR.
* **Speaker labels:** `--task minutes` or `--diarize on`, model `diarize-pyannote-campplus` (34 MB).
* Tests: 288 passing (+10); real-model tests 5 passed, 2 skipped (optional models not installed).
* **Acceptance (clean `ubuntu:24.04`, real network in mainland China):** no curl → clear message, exit 1. With only curl
  (no git, unzip, Python, ffmpeg) → uv from the Aliyun wheel, vidroute installed, exit 2 with the ffmpeg command after
  13 s and 36 MB. `uv tool install` of the public GitHub archive works without git. After `apt install ffmpeg`,
  `setup --yes` took 180 s (886 MB) and the self-test passed; a minutes run labelled speakers.
* **Measured (16 threads):** chinese narration CER still 1.3 %, one voice got no labels; 6-turn dialogue of recorded voices:
  5 turns right, a 3 s turn got its own label. A 1-hour minutes run took 285 s (59 s without labels and OCR).
* **Not verified:** Windows (`install.ps1` default source changed; needs CI after a push), macOS, real meetings for
  speaker labels, and the slow-mirror switch on a real network (covered by tests with a throttled local server).

## Phase 6.8 — stability fixes from the same review (2026-11-04)

D-051 … D-054. Tests: 310 passing (+22); real-model tests 5 passed, 2 skipped.

* **Concurrency (real models):** 3 runs on one folder, 3 on separate folders and 2 identical drills all exit 0.
* **Resume:** a 20 min minutes run takes 114 s; killed with `kill -9` at 78 s, the rerun took 34 s. No leftover workers.
* **Errors:** empty, cut-off and random files, an unwritable folder and a broken `LD_LIBRARY_PATH` each get a specific
  message; failed runs leave no folder.
* **Clean `ubuntu:24.04`, 2 CPUs, 2 GB:** `doctor` reports `16 threads (2 usable)`, `2.0 GB (container limit)`, `cpu-lite`;
  `setup` installs `ocr-ppocrv6-tiny` in 100 s; a 20 min minutes run takes 194 s without running out of memory.
* **Not verified:** the Windows lock (`msvcrt.locking`) and all of this on Windows or macOS; needs CI after a push.
