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
| 6 | CLI/MCP/Skill shells, packaging, README | ⏳ next |
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
