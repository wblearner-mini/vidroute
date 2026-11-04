# Decisions and evidence log

Every non-obvious choice in vidroute is recorded here with its evidence (URL,
version, date). Facts from the original build brief were re-checked against
primary sources; where they differ, **the re-check wins** and the difference
is noted. All checks below were performed on **2026-10-21** unless stated.

Legend: ✅ confirmed · ⚠️ confirmed with corrections · ❓ not verified (kept
out of default paths or marked `unverified`).

---

## D-000 Project name: `vidroute` (renamed from the working name `vidsense`)

* `vidsense`: free on PyPI (`/pypi/vidsense/json` → 404) but **46 GitHub
  repositories** use the name, many in the same domain (AI video
  understanding, transcription, video RAG). Per brief §13.5 the owner was asked
  and chose `vidroute`.
* `vidroute`: PyPI 404 (free), GitHub `vidroute in:name` → 0 repositories.
  Also free: `vidroute-mcp`. The name describes the core differentiator
  (routing by LLM × host capability) and contains no vendor trademark.

## D-001 License: MIT

MIT (brief default). All default-path dependencies and models are compatible
(see D-007, D-008 and `THIRD_PARTY_LICENSES.md`). Model weights are never
redistributed in the repository or the wheel; they are downloaded by the user
after confirming an install plan, so model licenses apply to the user's copy.

## D-002 Kernel + shells; standard-library-only orchestrator

* All logic lives in `vidroute.api` / `vidroute.core`; shells (CLI, MCP,
  Skill script, future desktop app) only translate arguments and envelopes.
* The orchestrator has **zero runtime dependencies** (`dependencies = []`).
  Heavy libraries run in isolated worker environments (D-004) and talk to the
  orchestrator over JSON lines on stdin/stdout.
* **YAML without PyYAML**: the profile/rules/manifest files are YAML for human
  review, parsed by `vidroute.core.util.yamlish`, a strict loader for a
  documented subset (YAML 1.2 core scalars; no anchors/tags/multi-docs).
  `tests/test_data_files.py` asserts that every bundled file parses identically
  with PyYAML, so contributors can rely on normal YAML tooling.
* **MCP server without the MCP SDK**: `mcp` 2.3.0 (PyPI, 2026-10-02) pulls in
  pydantic, httpx2, jsonschema, opentelemetry and more. The stdio transport is
  newline-delimited JSON-RPC 2.0, small enough to implement with the standard
  library; the official SDK is used as a *test-only* client to check protocol
  conformance (Phase 6).

## D-003 Data layout and packaging

Data files live at the repository root (`routing/`, `models/`, `schemas/`,
`references/`, `envs/`) so that the repository doubles as an Agent Skill folder
and profile changes are easy to review. Hatchling `force-include` copies them
into the wheel under `vidroute/_data/`; `vidroute.core.resources` resolves
both layouts. Build backend: hatchling ≥ 1.27 (PEP 639 `license = "MIT"`).

## D-004 Worker environments: `sherpa`, `ocr`, `whisper` (refines the brief's `onnx`/`whisper`)

* The brief suggested one `onnx` group (onnxruntime + sherpa-onnx + OCR).
  sherpa-onnx wheels ship their own ONNX Runtime libraries, and RapidOCR uses
  the `onnxruntime` package; loading two ONNX Runtime builds in one process is
  a known source of DLL conflicts on Windows. vidroute therefore uses separate
  groups and separate worker processes:
  * `sherpa`: sherpa-onnx 1.13.8 (Apache-2.0; wheels cp37–cp314, 19 Windows
    wheels; PyPI 2026-09-10) + numpy — VAD and ASR.
  * `ocr`: rapidocr 3.10.0 (Apache-2.0, 2026-10-08) + onnxruntime 1.31.0
    (MIT, requires Python ≥ 3.11) + opencv-python-headless, numpy, …
  * `whisper`: faster-whisper 1.2.1 (MIT) + ctranslate2 4.8.3; on NVIDIA,
    plus `nvidia-cublas-cu12` and `nvidia-cudnn-cu12==9.*`.
* Worker environments use **Python 3.12** fetched by `uv` regardless of the
  host interpreter (onnxruntime 1.31 needs ≥ 3.11, numpy 2.5 needs ≥ 3.12).
  If `uv` is missing, vidroute offers to install `uv==0.13.4` from PyPI into
  `$VIDROUTE_HOME/runtimes/uv` (part of the confirmed install plan; nothing
  system-wide).
* Lock files (`envs/*.lock.txt`) are generated with
  `uv pip compile --universal --generate-hashes` and installed with
  `--require-hashes` (supply-chain pinning).

## D-005 Default local models (base package = D3/D4 usable out of the box)

| capability | default | why | evidence |
|---|---|---|---|
| vad | Silero VAD ONNX as distributed by sherpa-onnx | MIT, 0.6 MB, CPU | asset `silero_vad.onnx` (643,854 B) in k2-fsa/sherpa-onnx release `asr-models`; GitHub digest `sha256:9e2449e1…` = local hash |
| vad (fallback) | ffmpeg `silencedetect` | zero download | built in |
| asr zh/en/ja/ko/yue | SenseVoice-Small int8 (2024-07-17) via sherpa-onnx | fast non-autoregressive CPU ASR; best open option for Mandarin/Cantonese | archive digest `7d1efa21…` (163,002,883 B); members hashed locally; `model.int8.onnx` = HF LFS oid `c71f0ce0…` |
| asr multi (CPU) | Whisper small int8 via sherpa-onnx (optional package) | 99 languages, MIT | HF `csukuangfj/sherpa-onnx-whisper-small@8f3c18b3` LFS oids; tokens hashed locally |
| asr (GPU) | faster-whisper large-v3-turbo (`dropbox-dash/faster-whisper-large-v3-turbo@0a363e91`, MIT) | standard CUDA path | HF LFS oid for `model.bin`; small files hashed locally |
| ocr | PP-OCRv6 small det/rec via RapidOCR 3.10.0 | Apache-2.0, ~31 MB, 50 languages, CPU | ModelScope file API + RapidOCR `default_models.yaml` agree on sha256 |
| scene | ffmpeg scene score (`select`/`scene`) | no model, no Python dependency | built in |

Base package: Silero VAD + SenseVoice-Small (≈ 240 MB) + the `sherpa` env.
OCR, Whisper and GPU runtimes are optional packages (size, license and GPU
need are shown before confirmation).

## D-006 Which SenseVoice build

sherpa-onnx publishes two int8 builds. The **2025-09-09** build's README says it
was converted from `ASLP-lab/WSYue-ASR` (`sensevoice_small_yue`), i.e. a
Cantonese fine-tune. The **2024-07-17** build is converted from
FunAudioLLM/SenseVoice. vidroute uses 2024-07-17 as the general default and
offers 2025-09-09 as `asr-sensevoice-yue-int8` (alt).

## D-007 SenseVoice license (not a §13 stop)

FunASR Model Open Source License v1.1
(<https://github.com/modelscope/FunASR/blob/main/MODEL_LICENSE>, last changed
2024-11-20): free use, copying, modification and sharing; **attribution and
keeping the model name are required**; includes a "no denigration" clause and
automatic-revision wording. It does not prohibit commercial use and the model
is not gated, so it is not "restricted" under the brief's rules and SenseVoice
can stay in the default path. It is nevertheless a custom license: vidroute
shows it in every install plan, records the model name in each output
`manifest.json`, and credits it in `THIRD_PARTY_LICENSES.md`. Whisper (MIT) is
available for anyone who prefers OSI-licensed weights.

## D-008 OCR runtime

RapidOCR 3.10.0 defaults to PP-OCRv6 (`ocr_version: PP-OCRv6`, small det/rec)
and hosts ONNX conversions on ModelScope (`RapidAI/RapidOCR@v3.10.0`). The
PaddlePaddle ONNX exports on Hugging Face (`PaddlePaddle/PP-OCRv6_small_det_onnx`
etc., Apache-2.0) are different files (dictionary not embedded), so only the
ModelScope mirror is used for OCR in v0.1 (single mirror; see KNOWN_ISSUES).
The text-line orientation classifier is disabled (video frames are upright),
saving a download.

## D-009 Scene detection

PySceneDetect 0.7.2 (BSD-3) needs `opencv-python`, which conflicts with
`opencv-python-headless` required by RapidOCR. ffmpeg's scene score already
gives deterministic scene-change candidates with no extra install, so it is the
default; PySceneDetect/TransNetV2 are left for later.

## D-010 GPU and alternative runtimes

* faster-whisper: latest CTranslate2 supports only CUDA 12 + cuDNN 9; pip
  route `nvidia-cublas-cu12 nvidia-cudnn-cu12==9.*` (faster-whisper README). GPU
  is an *upgrade*: the CPU baseline must work first, and a failed GPU smoke test
  falls back to CPU with the reason recorded.
* FunASR llama.cpp runtime exists (FunAudioLLM/Fun-ASR release
  `runtime-llamacpp-v0.2.1`, 2026-08-27, with windows-x64, -avx2, -cuda and
  -vulkan zips). Not used: sherpa-onnx now runs Fun-ASR-Nano directly
  (`sherpa-onnx-funasr-nano-int8-2025-12-30`), avoiding a second native runtime.
  The brief's "CUDA build only for sm_86" was not re-verified ❓.
* whisper.cpp Vulkan: not used in v0.1 ❓. `gpu-vulkan` tier is defined but no
  engine reaches it yet.

## D-011 Downloads: mirrors and domain allow-list

Allowed domains (suffix match, redirects included): `github.com`,
`githubusercontent.com` (release assets redirect to
`release-assets.githubusercontent.com`), `huggingface.co`, `hf.co` (LFS/Xet
redirects to `*.cdn.hf.co`), `hf-mirror.com` (community HF mirror widely used in
mainland China; safe because every file is sha256-verified), `modelscope.cn`.
All checked sources support HTTP `Range`, so downloads resume.

## D-012 LLM facts re-checked (brief §2) → `routing/llm-profiles.yaml`

| brief claim | result | evidence |
|---|---|---|
| Qwen3.8-Omni-Flash: text/image/audio/video in, text out, 1M context | ✅ 1M context window, 991,808 max input (non-thinking) | <https://www.alibabacloud.com/help/en/model-studio/qwen3-8-omni-flash> (updated 2026-10-11) |
| …released Sept 2026 | ✅ (arXiv 2609.25611 "Qwen3.8-Omni"; third-party reviews date it 2026-09-18) | |
| Qwen3.8-Omni-Flash video ≤ 2 h, audio ≤ 3 h ("third-party, must check") | ✅ official: video 2 h, 64 files, 2 GB by URL, base64 < 10 MB; audio 3 h | <https://www.alibabacloud.com/help/en/model-studio/qwen-omni> |
| Qwen3.5-Omni video ≤ 1 h, audio ≤ 3 h | ✅; context 262,144 (196,608 max input) | same + model pages |
| Qwen3-Omni-Flash ≈ 150 s | ✅ 150 s video, 20 min audio, one non-text modality per request, context 65,536 | same |
| Token cost | Qwen: audio 7 tok/s (3.8/3.5), 12.5 (3-Omni); video estimator FPS 2, ≤ 640 tok per frame pair, ≤ 180,224 visual tokens | qwen-omni page |
| Qwen-MM-Plugins fallback chain | ✅ OSS upload when configured, else sampled frames + fitted continuous audio; audio as 16 kHz mono MP3 on a 16–64 kbps ladder; inline budget ≈ 0.97 × 10 MB base64 | `QwenLM/Qwen-MM-Plugins` (Apache-2.0) `src/shared/omni_media.py` |
| Gemini: 1 FPS, ~300 tok/s (~100 low-res) | ⚠️ 1 FPS ✅; **default is now low media resolution ≈ 100 tok/s** (66 tok/frame + 32 tok/s audio), high ≈ 300; 1M-context models take 3 h of video at low res, 1 h at high | <https://ai.google.dev/gemini-api/docs/video-understanding> |
| Gemini: > ~20 MB → File API; 2 GB per file | ⚠️ File API when the request > 20 MB ✅; File API max 2 GB (free) / 20 GB (paid); the same page's table lists inline < 100 MB — vidroute uses the conservative 20 MB | same |
| Current Gemini models | gemini-3.8-flash (stable, Sept 2026): text/image/video/audio/PDF in, 1,048,576 input tokens | <https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash> |
| OpenRouter Models API exposes `input_modalities` | ✅ `architecture.input_modalities` ∈ {text,image,audio,video,file}; also `context_length` (468 models on 2026-10-21) | <https://openrouter.ai/api/v1/models> |
| Claude image limits | 600 images/request (100 for 200k-context models), 8000×8000 px, stricter limit above 20 images, ⌈w/28⌉·⌈h/28⌉ tokens, Claude 4.7+: 2576 px / 4784 tokens | <https://platform.claude.com/docs/en/build-with-claude/vision> |
| OpenAI image limits | 1,500 images and 512 MB per request | <https://developers.openai.com/api/docs/guides/images-vision> |
| Qwen 3.5–3.8 vision (non-omni) | video 2 s–2 h, no audio understanding; 256–2,048 images per request | <https://www.alibabacloud.com/help/en/model-studio/vision> |

Models listed with video input on OpenRouter but without verifiable limits
(Kimi K3, GLM-5.3 Flash, GLM-5V, MiniMax M3, Gemma 4) are profiled with
`unverified: true` and conservative limits.

## D-013 Host facts re-checked (brief §5.2) → `routing/host-profiles.yaml`

| host | brief seed | re-check | confidence |
|---|---|---|---|
| claude-code | image ✅ video ❌ audio ❌ | ✅ unchanged. **Live test** in Claude Code (desktop, 2026-10-21): PNG read as image; `.mp4` and `.mp3` refused as binary | verified |
| codex-cli | image only | ⚠️ rust-v0.163.0 also accepts **local audio** (≤ 50 MB); still no video | source-verified |
| opencode | images (JPEG/PNG/GIF/WebP) only | ✅ docs (attachments.mdx, v1.19.3): PDF/audio/video dropped; 20 MiB limit | docs-only |
| cursor | image, unverified | unchanged (closed source) | unverified |
| gemini-cli | video "?" (docs contradict) | ⚠️ v0.65.0 source: `detectFileType` → `video`, sent as `inlineData` (whole file incl. audio), `MAX_FILE_SIZE_MB = 20`; docs still omit video | source-verified (live test pending) |
| openclaw | media-understanding preprocessing (D5-like) | ✅ defaults 10 MB image / 20 MB audio / 50 MB video; ⚠️ reply models configured with video input can now receive video directly via `video_url` | docs-only |
| qwen-code / bailian-cli | delegates via tool | ⚠️ qwen-code now attaches video/audio **natively** when the model config declares the modality (inline < 10 MB data URI; experimental Omni upload ≤ 1 GiB). bailian-cli (`modelstudioai/cli`, npm 2.5.0, Apache-2.0) is a tool for agents that offers long-video understanding via DashScope → a D5 delegate candidate, not a host | source-verified |
| cline / roo / kilo | unverified | cline: no video handling in `src/` (code search); Roo Code **archived 2026-05-15**; Kilo 7.x builds on opencode | unverified |
| local-mistralrs | video ✅ by model | ⚠️ mistral.rs decodes video to frames with FFmpeg → `frame-sampled` (D3) | docs-only |

Schema extensions (documented in `schemas/host-profiles.schema.json`):
`confidence` gains `source-verified`; `video_delivery` gains `preprocessed`
(host converts media to text itself; routed like "no video").

## D-014 "Frames + audio" variant of D3

When the effective capabilities include audio but not video (e.g. codex-cli
with an audio-capable model), vidroute keeps route `frames` but also attaches
the audio track as 16 kHz mono MP3 fitted to the limits (idea from
Qwen-MM-Plugins, no code copied). The proofreading rules then follow D1
("what the model hears wins").

## D-015 Outputs and writes

Without `--out`, outputs go to `$VIDROUTE_HOME/out/<slug>/` so vidroute never
writes outside `VIDROUTE_HOME` or `--out`. SKILL.md tells agents to pass
`--out` inside their workspace so the host can attach the files.

## D-016 CLI contract details

* Exit codes 0/2/3/4/5 as in the brief; **1** is used for invalid usage/input
  (the brief leaves it undefined; argparse's own exit code 2 would collide with
  "needs install", so argparse errors are converted to envelopes with exit 1).
* Warnings are objects `{code, message, hint?}` rather than strings.

## D-017 Agent Skills specification (brief §2, Phase 6)

✅ <https://agentskills.io/specification>: `name` 1–64 chars, lowercase
letters/digits/hyphens, no leading/trailing/double hyphen, **must match the
parent directory name**; `description` 1–1024 chars; optional `license`,
`compatibility` (≤ 500), `metadata` (string map), `allowed-tools`
(experimental); keep SKILL.md < 500 lines; validate with `skills-ref
validate` (PyPI `skills-ref` 0.1.1, needs Python ≥ 3.11).

## D-018 Related projects and how vidroute differs

No code is copied from any of these; ideas are credited here.

| project | license | what it does | what we borrow / how we differ |
|---|---|---|---|
| HUANGCHIHHUNGLeo/claude-real-video (★2.2k) | MIT | scene-aware deduplicated frames + transcript, URL or local file | same D3 idea; vidroute adds capability routing, native-video routes, installs, proofreading protocol; no URL download |
| guimatheus92/mcp-video-analyzer (★96) | MIT | MCP server: frames + transcript + OCR from URLs/local files | vidroute: MCP is one shell over a routing kernel |
| jordanrendric/claude-video-vision (★1.4k) | MIT | Claude Code plugin; structure-first, drill down like binary search | "drill like binary search" guidance (ANALYSIS_GUIDE, `drill`) |
| Newuxtreme/watch-video-skill (★72) | MIT | Claude skill to "watch" videos | skill packaging reference |
| buxuku/SmartSub (★5.8k) | MIT | desktop subtitles; multi-engine ASR, per-engine model dirs, multi-mirror resumable downloads, GPU detection | download manager design, extract embedded subtitles before ASR, Windows GPU-detection pitfalls |
| HKUDS/VideoAgent (★2.0k) | MIT | agentic video understanding/editing framework | out of scope: vidroute is not an agent framework |
| QwenLM/Qwen-MM-Plugins (★3.1k) | Apache-2.0 | multimodal plugins; media delivery with fallbacks | inline-budget fitting, frames + audio fallback (D-014) |

**Differentiator**: two-layer routing by *LLM × host* capability (native
video / native video + local ASR / keyframes + transcript / text), explicit and
explainable degradation, model detection and one-command install, raw-video
handoff consent, and a proofreading protocol for transcripts.

## D-019 Documentation language

Repository documents (README, SKILL.md, references, this log) are in English
for an international audience; `README.zh-CN.md` mirrors the README in
Chinese.

## D-020 Development hosting

During development the code lives in a **private** GitHub repository
(`wblearner-mini/vidroute`) so Windows CI can run; it is made public at release
time (owner's decision, 2026-10-21).

## D-021 Python support window

`requires-python >= 3.10` as specified. Python 3.10 reaches end-of-life in
October 2026 (PEP 619); CI keeps testing 3.10 until v0.2. Worker environments
always use Python 3.12 (D-004).

## D-022 OCR models come from the hash-locked RapidOCR wheel when possible

The `rapidocr` 3.10.0 wheel (27 MB) ships `PP-OCRv6_det_small.onnx`,
`PP-OCRv6_rec_small.onnx` and the v2.0 orientation classifier, byte-identical
to the manifest entries (same SHA-256). Because the wheel itself is pinned by
hash in `envs/ocr.lock.txt`, copying those files (and re-checking their
SHA-256) is as trustworthy as downloading them, so `ocr-ppocrv6-small` lists a
`type: bundled` source first and ModelScope second. RapidOCR is always given
explicit local model paths so it never downloads anything on its own.

## D-023 Everything uv writes stays in VIDROUTE_HOME

Worker environments are created with `UV_PYTHON_INSTALL_DIR`, `UV_CACHE_DIR`
inside `$VIDROUTE_HOME`, `UV_PYTHON_INSTALL_REGISTRY=0` / `--no-registry`
(no Windows registry entries), `UV_NO_CONFIG=1` (user uv config ignored),
`--managed-python` (python-build-standalone 3.12, identical on every OS) and
`--no-build` (wheels only, never compile). The uv cache is deleted after a
successful install because files are copied (`UV_LINK_MODE=copy`).

Universal locks: `sherpa-onnx`'s Linux/macOS wheels depend on
`sherpa-onnx-core`, but uv's universal resolution read metadata from a wheel
that does not declare it; `sherpa-onnx-core==1.13.8` is therefore pinned
explicitly so `--require-hashes` covers it on every OS.

## D-024 Worker protocol

Workers are stand-alone scripts run with `<env python> -I` (isolated mode, no
user site, no cwd on `sys.path`). The protocol stream is a duplicate of the
original stdout fd while fd 1 is redirected to stderr, so native libraries that
print cannot corrupt JSON. stderr goes to `$VIDROUTE_HOME/cache/logs` (kept only
when non-empty) and its tail is attached to worker errors. ASR/VAD and OCR run
in separate processes (D-004).

## D-025 Install confirmation semantics

`vidroute install` always computes the plan first. Without `--yes` it returns
the plan with error `needs_confirmation` (exit 3) and downloads nothing; an
interactive terminal shows the plan and asks instead. Restricted licenses
return `license_not_accepted` (exit 3) until `--accept-license <id>` is given.
An explicitly requested model the hardware cannot run is exit 4. ffmpeg is
never installed silently: on Windows with `--yes` the plan runs
`winget install --id Gyan.FFmpeg` (shown in the plan); elsewhere the command is
printed.

## D-026 RAM tolerance for the 8 GB boundary

The brief defines `cpu-lite` as "< 8 GB RAM". Machines sold with 8 GB report
7.5–7.99 GB usable (firmware/iGPU reservations); GitHub's windows-latest runner
reports 7.99 GB. vidroute therefore treats ≥ 7.5 GB as the 8 GB class; below
7.5 GB is `cpu-lite`.

## D-027 Probe: text density from OCR detection when available

`text_density` drives R4 (OCR on/off). A pure-Python block heuristic
(contrast + edge density + bimodal histogram) is always computed, but it has
known failure modes on synthetic calibration clips: high-contrast random
texture (`life`) reads as text (0.875) and a burned-in subtitle over a busy
background is missed (0.0). When an OCR model is installed, probe runs the
PP-OCR **detector only** (no recognition, ~0.16 s per 640 px frame) on the same
8 frames and discards boxes that cannot be text lines (taller than 20 % of the
frame, tiny slivers, whole-frame blobs). On the calibration set this classified
slides, code, subtitles-only, testsrc2 (has a timecode), mandelbrot and life all
correctly; probe stays at ~3 s. The heuristic remains the fallback and is
reported as such (`pictures.method`).

## D-028 Segment count vs. per-request file limits

Native routes split over-limit videos into at most `max_segments` (8)
segments with 3 s overlap. A model's per-request file limit (`max_files`, e.g. 1
for Qwen3-Omni-Flash) does not cap the segment count — segments can be sent in
separate turns — and is reported to the agent instead.

## D-029 Audio attachments need the same consent as raw video

The "frames + audio" variant (D-014) hands the audio track to the LLM provider.
It is planned only when LLM ∩ host accept audio and speech is present, and
`prepare` only attaches it with `--accept-raw-video-handoff`; without the flag it
silently falls back to frames without audio (still a valid, more private
delivery) instead of failing.

## D-030 Keyframes: cell-difference shots instead of a global scene score

R5 asks for "scene changes + a timed grid + perceptual-hash dedup". Measured on
the synthetic calibration set (slides with crossfades, code, Game of Life,
Mandelbrot zoom) and a real 4-minute 1080p screen recording:

* ffmpeg's `scene` score is a global frame difference; adding one bullet to a
  slide or opening a menu in a screen recording stays far below the usual 0.3
  threshold, so such changes were missed.
* A 64-bit dHash is decided by noise on flat pictures (blank slides, dark
  screens): two identical blank frames can differ in > 20 bits, so dedup
  failed exactly where it matters (A–B–A decks).

vidroute therefore decodes once into 64 px gray thumbnails (the video's own
keyframes when they are dense enough, otherwise a decoded grid of at most 4 fps
and `max_candidates` frames), starts a new shot when any 4×4 cell differs by
more than `change_threshold` (10/255) from the previous candidate or the shot's
first one, and folds repeated pictures using a perceptual signature (the mean of
every cell) whose largest difference is a provable lower bound of the cell
difference — a cheap prefilter that cannot miss a true twin. The budget
(`min(max_frames, max_images)`, fewer for short videos) is filled coverage
first: the longest-on-screen shot of each of N equal time bins, then the longest
remaining shots, never two picks closer than `min_interval_seconds`.
Result on the slides clip: exactly the four slides, no crossfade frames; the
4-minute recording: 47 distinct shots, 23 returns to an earlier screen folded.

## D-031 Output contract of `prepare` (handoff.json v1)

`handoff.json` (schema `vidroute/handoff/1`, `schemas/handoff.schema.json`)
extends brief §7 with `on_demand` (single keyframes the agent may open),
`text_context[].attach` and `est_tokens`, `transcript_role`, `uploads_raw_audio`
and a `drill` example. All paths are relative to the output folder and all times
are global. What is marked for attaching depends on the route:

* frames (D3): contact sheets + `transcript.md` + `ocr.md`; `timeline.md` and
  `contact-sheets/index.md` are navigation only (they repeat content);
* text (D4): `timeline.md` only — transcript and on-screen text merged by time
  — with `transcript.md` / `ocr.md` available on demand.

`ANALYSIS_GUIDE.md` is rendered from `references/analysis-guide.template.md`
with route blocks, so contributors can edit the guidance without touching code.

## D-032 `prepare` reuses the plan in the output folder

`vidroute plan --out DIR` writes `DIR/<slug>/plan.json`; a later
`vidroute prepare <video> --out DIR` without `--llm/--host` uses that plan (with
a `plan_reused` warning) so the agent's "plan → ask the user → prepare" flow
needs no extra arguments, and the exit-3 hint for native routes is a plain
re-run with `--accept-raw-video-handoff`. `vidroute run` always plans afresh.

## D-033 OCR on sharper copies; single-character lines dropped

When the source is larger than `frame_max_side_px` (1280), keyframes are also
grabbed at `ocr_frame_max_side_px` (1920) for OCR only; the attached frames stay
at 1280. RapidOCR's default detector does not downscale a 1080p frame, so small
UI text survives. Lines of a single character are dropped: in screen recordings
they are nearly always icons read as text ("×", "□", "Q").

## D-034 Native routes: check the files, not just the plan

The plan's bitrate arithmetic (D-028) decides copy / audio-free remux /
transcode and the segment boundaries, but encoders overshoot on short or busy
segments. `prepare` therefore measures every produced file against
`max_file_mb` (97 % margin: container overhead, MB vs MiB). An over-size encode
is redone at a proportionally lower bitrate, up to three passes; if a part
still does not fit, `prepare` plans again with the route forced to `frames`,
records `{"route": "native-…", "why": "prepared files exceed the limit …"}` in
`degraded_from` and removes `native/`. Encoders: `libx264` (veryfast,
ABR with maxrate/bufsize, or CRF without a size limit), else `libopenh264`,
else MPEG-4 Part 2; hardware encoders are never used. Audio for `native-av` is
mono AAC; `native-v` never ships an audio track. `segments.json`
(`vidroute/segments/1`) gives every part its `offset_sec` so times inside a
part map back to global time. `native-av` only runs ASR when the plan asks for
a verbatim reference (long video, segments, minutes/transcript task); embedded
subtitles are still extracted because they are free.

## D-035 Drill works on an output folder

`vidroute drill <out-dir> --range START-END` reads the folder's `plan.json`
(video path, route, LLM image limits) and writes `drill/<start>-<end>[-Nfps]/`
with denser keyframes (change-based with a default budget of 24, or a fixed
`--fps` up to 10 and 120 frames, both capped by `max_images`), a labelled
contact sheet, and on request a fresh transcript (`--asr`, VAD restricted to
the range) and OCR (`--ocr`). Paths in `drill.json` are relative to the main
output folder and all times are global, so the agent never converts time.

## D-036 MCP server: handshake protocol versions, no SDK at runtime

`vidroute-mcp` speaks newline-delimited JSON-RPC 2.0 over stdio with the
`initialize` handshake for protocol versions 2024-11-05, 2025-03-26,
2025-06-18 and 2025-11-25 (it echoes a supported request and counter-offers
2025-11-25 otherwise). The official SDK (`mcp` 2.3.0, PyPI 2026-10-02) also
knows 2026-07-28, which replaces the handshake with `server/discover` and
per-request `_meta`; vidroute does not implement it yet (`server/discover` gets
-32601, after which clients use the handshake). Tool results carry the envelope
as `structuredContent` and as JSON text, `isError` mirrors `ok`; progress
notifications use a strictly increasing counter; a cancelled request gets no
response, per the cancellation rules. `read_outputs` returns output files as
image/audio/text content (audio only from 2025-03-26 on) and refuses paths
outside the output folder. Conformance is tested with the SDK's own stdio
client (test-only dependency).

## D-037 Agent Skill and Claude Code plugin layout

The skill lives in `skills/vidroute/` (Agent Skills spec: `name` = folder,
description with "Use when", < 500 lines, references one level deep) and passes
`skills-ref validate` (`skills-ref` 0.1.1, Python ≥ 3.11, test-only). Its
launcher `scripts/vidroute.py` runs, in order, the source tree it belongs to
(the orchestrator is pure stdlib, so a plugin checkout needs no install), a
`vidroute` executable on PATH, or the installed package; otherwise it returns a
`needs_install` envelope. The repository root is also a Claude Code plugin
(`.claude-plugin/plugin.json`) and a one-plugin marketplace
(`.claude-plugin/marketplace.json`, `claude plugin validate .` passes). The
plugin ships no `.mcp.json`: there is no Python command name that works on
Windows, macOS and Linux alike, so the README documents
`claude mcp add vidroute -- vidroute-mcp` instead. The wheel bundles the skill;
`vidroute skill path` prints its folder for copying into any host's skills folder.

## D-038 Data schema versions and supply-chain checks

Every data file and output declares `schema: vidroute/<kind>/<major>` (rules,
manifest, profiles, plan, handoff, segments, drill, output manifest). Loaders
reject an unknown major version with an error naming the expected one; a
migration function will be added alongside the first `/2`. Supply chain:
`tools/license_report.py` reads every pinned package's license from PyPI
metadata (PEP 639 expression, else classifiers) and fails outside an allow-list
of permissive and weak-copyleft licenses (NVIDIA CUDA libraries are reviewed
exceptions for the optional GPU path); `tools/vuln_check.py` queries osv.dev for
all pinned versions. Both are standard-library scripts run by the
`supply-chain` workflow (on lock changes and weekly). Releases are built by
`release.yml` on version tags with `SHA256SUMS.txt`; PyPI uploads use trusted
publishing and only run on a manual dispatch.

## D-039 `vidroute setup`: one plan, one confirmation (2026-10-23)

Owner request: fewer manual steps, especially connecting agent hosts. `vidroute
setup` combines the install plan (D-025, unchanged), connecting the agent hosts
on this machine (D-040) and an end-to-end self-test (D-046) into one plan with
one confirmation: exit 3 with `data.plan` and a `--yes` hint, the interactive
y/N prompt in a terminal, nothing written before. Host integration writes
outside `VIDROUTE_HOME`, which brief §13.3 allows only with the user's
confirmation — so it is always part of that plan, never implicit. Defaults:
capabilities asr + vad + ocr + scene (the README's CPU baseline), hosts `auto`
(every host detected), skill on, MCP off. A machine that is already set up gets
the self-test only. Exit codes: 2 when ffmpeg is still missing (Linux/macOS,
where it is the user's package manager's job; only a warning with
`--no-install`, which skips installing on purpose), 5 when an install step,
integration or the self-test failed; re-running resumes. `setup --remove` takes
out exactly what state.json and the folder markers say vidroute added; models
stay.

## D-040 Agent-host integration: skill folders, markers, MCP opt-in (2026-10-23)

User-level skill folders, from each host's documentation on 2026-10-23:

| host | reads | source |
|---|---|---|
| Claude Code | `~/.claude/skills` (`$CLAUDE_CONFIG_DIR/skills`) | code.claude.com/docs/en/skills |
| Codex CLI | `~/.agents/skills` | learn.chatgpt.com/docs/build-skills |
| Gemini CLI | `~/.gemini/skills`, `~/.agents/skills` | geminicli.com/docs/cli/skills |
| Qwen Code | `~/.qwen/skills` | qwenlm.github.io/qwen-code-docs/en/users/features/skills |
| opencode | `~/.config/opencode/skills`, `~/.claude/skills`, `~/.agents/skills` | opencode.ai/docs/skills |
| Cursor | `~/.agents/skills`, `~/.cursor/skills`, `~/.claude/skills`, `~/.codex/skills` | cursor.com/docs/skills |
| Cline | `~/.cline/skills` | docs.cline.bot/customization/skills |
| Roo Code | `~/.roo/skills`, `~/.agents/skills` | roocodeinc.github.io/Roo-Code/features/skills |
| Kilo Code | `~/.kilo/skills`, `~/.agents/skills` | kilo.ai/docs/customize/skills |
| OpenClaw | `~/.agents/skills` (and its state dir) | docs.openclaw.ai/tools/skills |

* **Fewest copies.** Hosts with one folder fix it; the others join a folder
  already chosen, else `~/.agents/skills`. Claude Code + opencode → one copy;
  Claude Code + Codex → two (opencode/Cursor may then list the skill twice;
  KNOWN_ISSUES.md).
* **Detection** only looks: command on PATH, configuration folder, or (Cline,
  Roo, Kilo) a VS Code-family extension folder.
* **Ownership.** A copy carries `.vidroute-skill.cfg` (`installed_by=vidroute`,
  version, content digest, interpreter). Only such folders are updated or
  removed; a hand-made `vidroute` folder is skipped, or with `--force` renamed to
  `vidroute.bak-<time>` — never deleted. `doctor` reports each host's copy and
  warns when it is outdated or its interpreter is gone.
* **Recorded interpreter.** The marker holds the Python that runs vidroute (and
  the source tree when it is not installed), so the copy works in a host that
  was started before PATH changed.
* **Claude Code plugin.** When `installed_plugins.json` lists `vidroute@…`, the
  plugin already brings the skill and `~/.claude/skills` is skipped. The plugin
  still has no `bin/` folder: claude.ai and Cowork refuse plugins that ship one
  (code.claude.com/docs/en/plugins-reference), and the launchers are reachable
  through the skill folder anyway.
* **MCP is opt-in** (`--mcp`): the skill covers every host above, and MCP tool
  definitions cost context on every turn. Claude Code and Codex are changed
  through their own commands (`claude mcp add --scope user`, `codex mcp add`;
  `~/.claude.json` is a large state file the host rewrites, Codex uses TOML).
  Gemini CLI, Qwen Code, Cursor and opencode keep servers in JSON
  (`mcpServers` / opencode `mcp` with `type: local`); vidroute changes only its
  own key, keeps the file's indentation and a one-time `.vidroute-backup`, and
  leaves files that are not strict JSON (comments) alone, printing the snippet
  instead. An entry is vidroute's own only when state.json recorded writing it.
  The server command is absolute (`<python> -m vidroute.mcp_server`). Cline,
  Roo, Kilo and OpenClaw keep MCP settings in editor storage: skill only.

## D-041 Launchers that need no `python` on PATH (2026-10-23)

`skills/vidroute/scripts/` has three entry points: `vidroute` (POSIX sh, shell
builtins only, also for Git Bash), `vidroute.cmd` (cmd/PowerShell, CRLF) and
`vidroute.py`. The shell launchers try the interpreter recorded by `setup`, then
`python3`/`python`/`py -3` — each test-run, so the Microsoft Store placeholder
`python.exe` is skipped — then uv, also where installers put it before PATH is
refreshed (`~/.local/bin`, `WinGet\Links`, `~/.cargo/bin`, `VIDROUTE_HOME`), and
run `uv run --no-project --python 3.12 vidroute.py` with
`UV_PYTHON_INSTALL_DIR=$VIDROUTE_HOME/runtimes/python`, so that download is the
same Python the model workers use. Without any of them they print a
`needs_install` envelope whose hint installs uv (winget / PowerShell installer /
`curl … | sh`). `vidroute.py` prefers the source tree it lives in, then the
recorded installation, then an importable package, then PATH. Its install hint
is `uv tool install git+https://github.com/wblearner-mini/vidroute` until the
first PyPI release (docs/RELEASING.md lists the switch).

## D-042 Mirrors for packages and the Python runtime (2026-10-23)

Worker environments are installed with `--require-hashes`, and uv verifies every
managed-Python download against the SHA-256 in its metadata (`Error::HashMismatch`,
`crates/uv-python-managed/src/downloads.rs` at uv 0.13.4; uv 0.13.4 downloads
from `releases.astral.sh` and `UV_PYTHON_INSTALL_MIRROR` replaces that prefix —
checked with `uv python list --show-urls`). A mirror therefore cannot change
what is installed. Rule for `--mirror auto`: keep official sources; use mirrors
when the official one does not answer, or when huggingface.co is unreachable
while hf-mirror.com or ModelScope answer (the signature of a mainland-China
network, where GitHub and PyPI downloads also crawl). Mirrors: PyPI —
`mirrors.aliyun.com/pypi/simple/` then `pypi.tuna.tsinghua.edu.cn/simple/`;
Python builds — `registry.npmmirror.com/-/binary/python-build-standalone/`
(lists releases up to 20261021, checked 2026-10-23); models — bundled, then
ModelScope, then hf-mirror.com, then the rest. `--mirror cn` forces this. The
user's `UV_DEFAULT_INDEX` / `UV_INDEX_URL` / `PIP_INDEX_URL` /
`UV_PYTHON_INSTALL_MIRROR` and the config keys `pypi_index` / `python_mirror`
(`official` turns mirrors off) always win. The plan shows the choice and why.

## D-043 Portable FFmpeg for Windows (refines D-025, 2026-10-23)

winget stays first when it is available (brief §6). Without winget (LTSC,
Server, locked-down PCs), when winget fails, or with
`vidroute config set ffmpeg_source portable`, the plan offers a pinned build
placed in `$VIDROUTE_HOME/runtimes/ffmpeg` — not added to PATH;
`util.ffmpeg` already searches that folder. Build: gyan.dev "essentials"
FFmpeg 9.0.2 from `github.com/GyanD/codexffmpeg` (versioned tags are kept, unlike
BtbN's pruned autobuilds), 114,768,076 bytes, SHA-256
`60f46726…a2047ba` = GitHub release asset digest = local download; `ffmpeg.exe`,
`ffprobe.exe` and `LICENSE` are each pinned too (210 MB extracted, `ffplay`
skipped). Static GPLv3 build with libx264, libmp3lame and freetype (README.txt
in the archive) — the same family CI installs with `choco install ffmpeg
--version 9.0.2`. GPL obligations apply to redistribution; vidroute only
downloads the file for the user (after the plan) and runs it as a separate
program, so the manifest marks it unrestricted with that note. The x64 build
also serves Windows on Arm (emulation). The hint for a missing ffmpeg on
Windows is now `vidroute install --capability scene`, which plans exactly this.

## D-044 Windows on Arm: x64 Python where wheels are missing (2026-10-23)

PyPI on 2026-10-23: `opencv-python-headless` 5.0.0.93 and `pyclipper` 1.4.0
(OCR env) and `ctranslate2` 4.8.3 (faster-whisper env) have no `win_arm64`
wheels; everything in the sherpa env has. Worker envs are built `--no-build`,
so the manifest marks `ocr` and `whisper` with `windows_arm64_arch: x86_64`:
on Windows on Arm vidroute asks uv for `cpython-3.12-windows-x86_64-none`
(uv's request syntax, checked with `uv python list --all-platforms` on uv
0.13.4) and Windows runs it under emulation; sherpa stays native. The OS
architecture comes from `IsWow64Process2`, because an emulated x64 Python
reports `AMD64`. Not validated on real Arm hardware (no Arm runner for private
repositories).

## D-045 One-line installers (2026-10-23)

`install.ps1` (Windows PowerShell 5.1+) and `install.sh` (POSIX sh) need no
Python: they find uv or place uv 0.13.4 (= `UV_VERSION`) in
`$VIDROUTE_HOME/runtimes/uv` — from the GitHub release (asset digests pinned),
else the PyPI wheel, else the same wheel from the Aliyun PyPI mirror (same
`packages/…` path, checked 2026-10-23; wheel hashes = `envs/uv.lock.txt`,
asserted by tests). Then `uv tool install --force --reinstall <source>`
(re-running upgrades; uv supplies a Python if there is none), `uv tool
update-shell`, and `vidroute setup` by absolute path (the current shell has
the old PATH). Mainland-China detection (D-042) also switches the uv download
order and passes `--mirror cn`. Under `irm | iex` the script never calls
`exit` (it would close the user's window); `curl | sh` reads the setup answer
from `/dev/tty`. Until the repository is public they are run from a checkout
(`-Source .` / `--source .`).

## D-046 Self-test (2026-10-23)

`setup` ends with a 6-second clip generated by ffmpeg: a white slide reading
"VIDROUTE SELF TEST" (when `drawtext` exists) over the Chinese sample recording
shipped with SenseVoice (a tone without it), run through `prepare --route
frames` into `$VIDROUTE_HOME/cache/selftest`. Checks: outputs written, a
contact sheet, speech transcribed (when ASR is installed), the slide text read
back (when OCR is installed). On this machine with the real models: passed in
6.0 s ("开放时间早上9点至下午5点。", "VIDROUTE SELF TEST").

## D-047 Fresh-machine installers (2026-11-04)

From a review on a clean Ubuntu container: the default `git+https` source needs git, and the Aliyun uv wheel could not
be unpacked without unzip or Python, which led to a GitHub download that stalled with no timeout. The default source is
now the GitHub source archive. `install.sh` reads the wheel's zip headers with `od` and inflates with `gzip` as the last
resort; the wheel's SHA-256 is checked first and the extracted binary is run before use. A transfer below 10 KB/s for
20 s counts as stalled. The repository became public on 2026-11-04 (owner's decision); tag, Release and PyPI stay open.

## D-048 ffmpeg is checked before the plan (2026-11-04)

On Linux and macOS vidroute cannot install ffmpeg, yet `setup` downloaded about 850 MB and only then failed. `setup` now
exits 2 before asking or downloading. `--no-install` keeps its warning.

## D-049 Speaker labels (2026-11-04)

sherpa-onnx offline diarization (pyannote segmentation-3.0 + 3D-Speaker CAM++, already in the `sherpa` env, 34 MB, on the
default path because minutes need it). Runs for `--task minutes` or `--diarize on`; `auto` skips other tasks because it
costs about 0.08 x speech length on 16 threads. Turns are computed before ASR and chunks are cut at speaker changes, so
one segment holds one speaker. One detected voice gets no labels. A failure only warns. Labels are voice groups, never
identities, and the guide says so. Checked with distinct recorded voices (6 turns: 5 right, one 3 s turn got its own
label); not yet with real meetings.

## D-050 Reporting what the frame budget leaves out (2026-11-04)

The 60-frame budget stays: it protects tokens and dense-image limits. Shots on screen for at least
`frames.omitted_min_seconds` (5 s) without a frame go to `omitted-shots.md` plus a warning with a `drill` command, so
fast cuts add no noise and a 120-slide deck does not look complete at 60 frames. The OCR box filter's height limit went
from 20 % to 40 % of the frame: large title lines had been thrown away, which skipped OCR on sparse slides.

## D-051 Runs on one output folder wait for each other (2026-11-04)

Three simultaneous runs on one video and folder used to crash two of them (`FileNotFoundError` while one run rebuilt
what another was reading). A kernel lock on `.work/lock` (`flock`, `msvcrt.locking`) now serializes `prepare`, `run` and
`drill` per output folder. Waiting beats failing: the second run gets cached results, and agents retry by themselves
anyway. The kernel drops the lock on any exit, including `kill -9`, so no stale-lock handling is needed. It gives up after
one hour. Filesystems without locks run unprotected, as before.

## D-052 A hint is a command, or nothing (2026-11-04)

Generic failures used to suggest `vidroute doctor --refresh` and unreadable files a raw `ffprobe` line, neither of which
helps. Machine problems (full disk, no permission) now get their own codes. When only the user can fix something (a
damaged file), `error.hint` is null and `message` says what to do. Unexpected errors ask for a report with `-v` output.

## D-053 Resource limits come from the process, not the host (2026-11-04)

`os.cpu_count()` and `/proc/meminfo` describe the host, so a 2 GB container looked like 19.8 GB and `cpu-full`. RAM now
uses the tightest cgroup limit (v1 and v2, walking up from this process's cgroup); CPUs use the affinity mask and the
quota. The tier, default OCR model and thread counts follow. Unlimited machines keep their previous thread counts.
Measured in a 2-CPU 2-GB container: `cpu-lite`, no out-of-memory, 20 min video with speaker labels in 194 s.

## D-054 Resume granularity (2026-11-04)

ASR already resumed by chunk, but saved only every 300 s of speech. It now saves every 60 s. Speaker turns are stored in
`.work/turns.json`, because they cost as much as ASR and the chunk cache is keyed by chunks cut at those turns.

