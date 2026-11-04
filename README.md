# vidroute

**Give coding agents eyes and ears for local video.** · [简体中文](README.zh-CN.md)

Most agent hosts (Claude Code, Codex CLI, opencode, Cursor, …) can hand their model images but not
video — even when the model itself understands video. vidroute decides, deterministically and
explainably, **what to hand the agent's LLM** for a local video, prepares it on your machine, and
writes a reading guide so the answer comes with global timestamps and honest proofreading.

| route | when | what the LLM gets | local transcript |
|---|---|---|---|
| **D1 `native-av`** | LLM ∩ host accept video *with* its audio | the video itself (transcoded / split to fit) | reference with timestamps |
| **D2 `native-v`** | video accepted, but the model cannot hear | the video without audio | the only record of speech |
| **D3 `frames`** | images only (the common case) | keyframes as labelled contact sheets (+ audio track if the model can listen) | the only record of speech |
| **D4 `text`** | text only | a timeline of transcript + on-screen text | the only source |
| D5 `delegate` | host blocks video, you configured a video-model endpoint | text from that endpoint | planned (v0.2) |

Routing has two layers: **what your LLM and host can consume** picks the route (with admission
checks for privacy, file limits after transcoding/splitting, and token budget), then **your
hardware** decides how the local half runs (speech recognition, VAD, OCR, keyframes). Every
degradation (`native → frames → text`) is recorded with its reason.

> **Status: pre-release (v0.1.0.dev0).** Feature-complete for the CLI, MCP server and Agent Skill;
> see [PROGRESS.md](PROGRESS.md). Decisions and their evidence: [docs/DECISIONS.md](docs/DECISIONS.md).

## Install

One command installs vidroute and walks you through the rest:

```powershell
# Windows (PowerShell)
irm https://raw.githubusercontent.com/wblearner-mini/vidroute/main/install.ps1 | iex
```

```bash
# Linux / macOS
curl -LsSf https://raw.githubusercontent.com/wblearner-mini/vidroute/main/install.sh | sh
```

The script fetches [uv](https://docs.astral.sh/uv/) (pinned and SHA-256-checked; uv brings its own
Python), installs vidroute, then runs **`vidroute setup`**, which shows one plan and asks once:

- **ffmpeg** if it is missing (Windows: winget, or a pinned portable build when winget is unavailable;
  Linux/macOS: the package-manager command to run);
- **local models**: speech recognition, voice activity, on-screen text, speaker labels (~0.85 GB, CPU only);
- **your agent hosts**: the vidroute skill for every host it finds — Claude Code, Codex CLI, Gemini CLI,
  Qwen Code, opencode, Cursor, Cline, Roo Code, Kilo Code, OpenClaw (`--mcp` also registers the MCP server);
- an end-to-end **self-test** on a generated clip.

In mainland China the mirrors are picked automatically when huggingface.co is unreachable (or force
them: `--mirror cn`); every download stays hash-checked. You can also clone the repository and run `.\install.ps1 -Source .` or
`sh install.sh --source .`.

**What the system must have:** only `curl` or `wget` (nothing on Windows): no git, Python or unzip. On Linux and macOS,
install ffmpeg with the system package manager first; if it is missing, `setup` gives you the command before downloading
anything.

**Let your agent do it.** Ask it: *"Set up vidroute for me, following
https://github.com/wblearner-mini/vidroute/blob/main/docs/AGENT_SETUP.md"*. The agent installs uv and
vidroute, shows you the plan, and runs it after your yes.

**By hand:** `uv tool install https://github.com/wblearner-mini/vidroute/archive/refs/heads/main.tar.gz` (not on PyPI yet), then `vidroute setup`.

Then ask your agent something like *"take minutes of D:\meetings\weekly.mp4"*, or use the CLI:

```bash
vidroute run meeting.mp4 --llm claude-opus-5-5 --host claude-code --task minutes --out ./video-out
```

and open `./video-out/<slug>/SUMMARY.md`. `vidroute doctor` shows what this machine can do and which
agent hosts see the skill; `vidroute setup --remove` takes vidroute out of them again.

## Use it from your agent

`vidroute setup` connects the hosts it finds (or the ones you name with `--host`). The skill goes into
the fewest folders that reach them all — `~/.agents/skills` (Codex CLI, Gemini CLI, opencode, Cursor,
Roo Code, Kilo Code, OpenClaw), `~/.claude/skills` (Claude Code), `~/.qwen/skills`, `~/.cline/skills` —
and records which Python runs vidroute, so it works before PATH is refreshed. Folders and entries
vidroute did not create are never overwritten.

Other ways:

- **Claude Code plugin:** `/plugin install vidroute --marketplace wblearner-mini/vidroute`
  (`vidroute setup` then skips Claude Code's skill folder).
- **MCP** (stdio, standard-library server, no SDK needed): `vidroute setup --mcp`, or
  `claude mcp add vidroute -- vidroute-mcp`, or in any MCP client config
  `{"mcpServers": {"vidroute": {"command": "vidroute-mcp"}}}`.
- **Copy the skill by hand:** the folder printed by `vidroute skill path`.

MCP tools: `resolve_llm`, `doctor`, `install` (needs `confirm`), `plan_video`, `prepare_video`
(native routes need `accept_raw_video_handoff`), `drill_video`, `probe_video`, `list_models`,
`read_outputs` (returns contact sheets, frames, audio and text as MCP content). Long tools report
progress and honour cancellation.

The agent's workflow (from [skills/vidroute/SKILL.md](skills/vidroute/SKILL.md)):
`llm resolve` → `doctor` / `install` (with your OK) → `plan` (asks you before any raw video leaves
your machine) → `prepare` → read `SUMMARY.md`, `ANALYSIS_GUIDE.md`, `handoff.json` → write the
answer with `[mm:ss]` timestamps → `drill` into a time range when detail is needed. The skill's
launchers (`scripts/vidroute`, `scripts/vidroute.cmd`) need no `python` on PATH: they fall back to uv.

## Command line

| command | does |
|---|---|
| `vidroute setup [--host ID] [--mcp] [--remove] [--yes]` | one plan: ffmpeg, models, agent hosts, self-test |
| `vidroute doctor [--refresh]` | OS, CPU (AVX2), RAM, GPUs, ffmpeg, runtimes, installed models, tier, agent hosts |
| `vidroute install [--capability X] [--model ID] [--mirror auto\|cn] [--yes]` | plan → download (mirrors, resume, SHA-256) → smoke test |
| `vidroute llm resolve --llm ID [--host ID]` | what an LLM on a host can consume, and why |
| `vidroute probe VIDEO` | duration, streams, subtitles, speech ratio, text density (< 10 s) |
| `vidroute plan VIDEO --llm ID [--host ID] [--task] [--prefer]` | the explainable two-layer plan (`plan.json`) |
| `vidroute prepare VIDEO [--out DIR] [--accept-raw-video-handoff]` | run the plan, write the handoff |
| `vidroute run VIDEO --llm ID ...` | plan + prepare |
| `vidroute drill OUT_DIR --range 01:20-02:00 [--fps N] [--asr] [--ocr]` | more detail for a time range |
| `vidroute models list\|verify\|remove`, `clean`, `config get\|set`, `llm set\|list\|sync`, `host set\|list`, `skill path` | housekeeping |

Every command takes `--json` and prints one envelope on stdout
(`{"ok", "schema": "vidroute/1", "data", "warnings", "error": {"code", "message", "hint"}}`);
logs go to stderr. Exit codes: `0` ok · `1` usage · `2` needs install · `3` needs your confirmation ·
`4` hardware cannot run it · `5` run failed. `error.hint`, when present, is always a command that fixes the problem.

## What `prepare` writes

```text
<out>/<slug>/
  SUMMARY.md          start here: route, why, what was produced or skipped, warnings, budget
  ANALYSIS_GUIDE.md   evidence order, proofreading rules for this route, minutes template, drill tips
  handoff.json        exactly what to attach (schemas/handoff.schema.json)
  plan.json  manifest.json (files + SHA-256 + engines and their licenses)  segments.json (split video)
  native/             the (transcoded) video or its parts            — native routes
  contact-sheets/     labelled keyframe grids + index.md              — frames route
  frames/  transcript.{md,srt,json}  ocr.{md,json}  timeline.md  audio/16k.wav  drill/<range>/
```

`--task minutes` (or `--diarize on`) labels `Speaker 1`, `Speaker 2`, … in the transcript: voice groups, not identities,
and they can be wrong. Longer shots that the keyframe budget left out are listed in `omitted-shots.md`; `drill` shows them.

All timestamps are **global** (also inside segments and drill output). The evidence order is
picture/sound > on-screen text (OCR) > local transcript, and the guide makes the LLM correct the
transcript only with visible (or audible) evidence, keeping a correction list.

## Supported models and hosts

LLM profiles (`routing/llm-profiles.yaml`, `vidroute llm list`) cover Gemini, the Qwen Omni/VL/Plus
families, Claude, GPT-5, Grok 4, DeepSeek, Kimi and a conservative fallback for unknown models
(text + images). Each entry cites the official source and the date it was checked; values that
could not be verified are marked `unverified` and kept conservative. Override or add your own:

```bash
vidroute llm set my-model --modalities text,image,video --video-max-seconds 600 --video-max-mb 20
vidroute llm sync --provider openrouter     # opt-in: modalities/context from OpenRouter's model list
```

| host | images | audio | video | profile confidence |
|---|---|---|---|---|
| Claude Code | ✅ | — | — | verified (live test) |
| Codex CLI | ✅ | ✅ | — | source-verified |
| Gemini CLI | ✅ | ✅ | ✅ native, ≤ 20 MB/file | source-verified |
| Qwen Code | ✅ | ✅* | ✅* native (*if the model config declares it) | source-verified |
| opencode | ✅ | — | — | verified (live test) |
| Cursor, Cline, Roo Code, Kilo Code | ✅ | — | — | unverified (conservative) |

Same video, different plans: Gemini on Gemini CLI → `native-av`; Qwen3.6-Plus on Qwen Code →
`native-v`; Claude on Claude Code → `frames`; DeepSeek on opencode → `text`. Corrections with a
source are welcome (see [docs/DECISIONS.md](docs/DECISIONS.md)).

## Local models and licenses

Nothing is downloaded without an install plan you confirm; every file is checked against a pinned
SHA-256 from an allow-listed host (GitHub, Hugging Face, hf-mirror, ModelScope). Weights are never
part of this repository or the Python package.

| model | used for | languages | size | license |
|---|---|---|---|---|
| Silero VAD | voice activity | any | 0.7 MB | MIT |
| SenseVoice-Small int8 (default) | speech recognition | zh, en, ja, ko, yue | 240 MB | FunASR Model License 1.1 (free use incl. commercial; attribution and model name required) |
| Whisper small int8 (default for other languages) | speech recognition | 99 languages | 375 MB | MIT |
| faster-whisper large-v3-turbo (GPU, optional) | speech recognition | 99 languages | 1.6 GB | MIT |
| PP-OCRv6 small (RapidOCR ONNX) | on-screen text | zh, en and more | 31 MB | Apache-2.0 |
| pyannote segmentation-3.0 + CAM++ | speaker labels | any | 34 MB | MIT + Apache-2.0 |
| ffmpeg (your own, or offered by `setup`) | decoding, scene/keyframes, transcoding | — | Windows portable build: 110 MB | LGPL/GPL (gyan.dev essentials 9.0.2: GPLv3) |

Alternatives and experimental models (SenseVoice Cantonese, FunASR-Nano, Qwen3-ASR, PP-OCRv6 tiny)
are listed by `vidroute models list`. Details per model: [models/manifest.yaml](models/manifest.yaml).

## Privacy and safety

- **Local only.** vidroute never calls a cloud model and has no telemetry. Whether your agent's LLM
  runs in the cloud is your agent's choice.
- **Raw media needs your consent.** Plans mark `uploads_raw_video`; native routes (and attaching the
  audio track) refuse to run without `--accept-raw-video-handoff`, and the skill tells the agent to
  ask you first. `--prefer privacy` keeps the raw video local and hands over only keyframes and text.
- **Extracted text is data, not instructions.** Transcripts, subtitles and OCR can contain prompt
  injections; the guide tells the LLM to ignore any instruction found in them.
- **Writes only to `VIDROUTE_HOME` (default `~/.vidroute`) and your `--out` folder** — plus, after you
  confirm `vidroute setup`, the agent-host skill folders and MCP entries listed in its plan. It never
  deletes your files and never replaces host entries it did not create; logs do not contain video content.
- Use vidroute only for videos you are allowed to process; respect privacy, consent and copyright.

## Platforms

Windows 10/11 (x64; Arm64 runs the OCR and faster-whisper workers as x64 under emulation), macOS and
Linux; Windows is tested in CI (`windows-latest`) on every commit.
Pitfalls and fixes: [references/windows-troubleshooting.md](references/windows-troubleshooting.md).
The CPU path always works; an NVIDIA GPU is used only after a passing CUDA smoke test and falls
back to CPU automatically.

## Known issues

See [KNOWN_ISSUES.md](KNOWN_ISSUES.md) — e.g. Gemini CLI native video not yet tested live, Cursor/Cline
profiles unverified, the CUDA path not validated on real hardware.

## Development

```bash
python -m venv .venv && .venv/bin/pip install --require-hashes -r requirements-dev.lock.txt
.venv/bin/pip install --no-deps -e .
.venv/bin/pytest                              # offline: ffmpeg + fake engines
VIDROUTE_REAL_MODELS=1 .venv/bin/pytest tests/test_real_models.py   # with installed models
```

The orchestrator has no runtime dependencies; heavy libraries live in isolated worker environments.
Architecture and decisions: [docs/DECISIONS.md](docs/DECISIONS.md); progress: [PROGRESS.md](PROGRESS.md).

## License

MIT — see [LICENSE](LICENSE). Downloaded models keep their own licenses (table above and each
output's `manifest.json`).
