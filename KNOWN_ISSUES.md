# Known issues

Open problems and unverified assumptions. Each entry says how it affects
users and what would close it.

## Verification gaps

* **gemini-cli native video not tested live.** The host profile is
  `source-verified` from v0.65.0 source (`fileUtils.ts`, `MAX_FILE_SIZE_MB = 20`).
  Closing it needs a run with a Gemini API key.
* **qwen-code native video depends on the user's model config.** Video is only
  attached when the configured model declares `modalities.video`. Users without
  that config should run `vidroute host set qwen-code --video-delivery none`.
* **Cursor, Cline, Roo Code, Kilo Code profiles are `unverified`.** Conservative
  (image-only) assumptions apply.
* **Models with video input but unverified limits** (Kimi K3, GLM-5.3 Flash,
  GLM-5V, MiniMax M3, Gemma 4) use conservative limits (`unverified: true`).
* **Gemini inline limit is ambiguous in the official docs** (20 MB request vs
  "< 100 MB" in a table on the same page); vidroute uses 20 MB.
* **Brief claims not re-verified:** FunASR llama.cpp CUDA build being sm_86-only;
  whisper.cpp Vulkan needing a self-build on Windows; PP-OCRv6 parameter counts.
  None of these are used in default paths.

## Platform

* Development happens on Linux; Windows is covered by CI (`windows-latest`).
  A clean Windows 10/11 VM re-test is part of the v0.1.0 release checklist.
* No NVIDIA GPU was available during development: the CUDA path
  (`asr-faster-whisper-large-v3-turbo`) is implemented with automatic CPU
  fallback but not validated on real hardware.

## Setup and agent hosts

* **Skill discovery was live-tested in Claude Code and opencode only.** The
  folders for Codex CLI, Gemini CLI, Qwen Code, Cursor, Cline, Roo Code, Kilo
  Code and OpenClaw come from their documentation (2026-10-23, D-040).
* **opencode and Cursor may list the skill twice** when Claude Code and another
  `~/.agents/skills` host are both connected (they read both folders). Both
  copies are identical.
* **MCP registration** is automated for Claude Code, Codex CLI, Gemini CLI,
  Qwen Code, Cursor and opencode; Cline, Roo Code, Kilo Code and OpenClaw get
  the skill only (their MCP settings live in editor storage).
* **The one-line installers** (`irm … | iex`, `curl … | sh`) need the
  repository to be public; until then they run from a checkout.
* **Windows on Arm**: the x64-emulated OCR / faster-whisper environments are
  not validated on real Arm hardware (no Arm runner for private repositories).

## Downloads

* The portable FFmpeg for Windows comes from GitHub only (no mirror in mainland
  China); winget, when available, is tried first.

* OCR models have a single mirror (ModelScope `RapidAI/RapidOCR@v3.10.0`); the
  PaddlePaddle ONNX files on Hugging Face are different files and are not used.

## Local pipeline

* **Machine transcripts contain the usual ASR errors** (product names, homophones,
  e.g. "Obsidian" → "opseing", "Kate Rubins" → "Kate Rubens", SenseVoice hearing
  "pieces of code" for "pieces of gold"). This is by design handled downstream: the
  analysis guide makes the LLM correct only with on-screen evidence and keep a
  correction list. Two-pass ASR with on-screen terms as hotwords (R7) is a
  Phase 7 extension.
* **Contact-sheet tiles are too small for UI text** in 1080p screen recordings;
  OCR and the full-resolution `frames/` cover it. The guide tells the agent to
  open single frames.
* **OCR cost** is ~1.2 s per dense 1080p frame on an 8-core CPU (60 frames max,
  so ~1–2 minutes at worst).
