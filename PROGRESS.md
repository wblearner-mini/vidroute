# Progress

Phases follow the build brief (§12). Each phase ends with passing tests, a
commit and an update here.

| phase | scope | status |
|---|---|---|
| 0 | research, decisions with evidence, scaffold, CI, schemas | ✅ done |
| 1 | `doctor`: system/GPU/ffmpeg/runtime detection, tiers, cache | ✅ done |
| 2 | `install`: downloader, mirrors, verification, uv envs, smoke tests | ✅ done |
| 3 | `llm resolve`, profiles, `probe`, `plan` (two-layer routing) | ✅ done |
| 4 | local perception pipeline, `prepare` for D3/D4 | ⏳ next |
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
