# vidroute

**Give coding agents eyes and ears for local video.**

Most agent hosts (Claude Code, Codex CLI, opencode, Cursor, …) can hand their
model images but not video, even when the model itself understands video.
vidroute decides — deterministically and explainably — *what* to hand the
agent's LLM for a given local video, then prepares it locally:

| route | when | what the LLM gets |
|---|---|---|
| **D1 `native-av`** | LLM ∩ host accept video *with* its audio track | the (transcoded/segmented) video itself |
| **D2 `native-v`** | video accepted but the model cannot hear | the video + a local transcript |
| **D3 `frames`** | images only (the common case) | keyframes + contact sheets + local transcript (+ OCR) |
| **D4 `text`** | text only | a timeline of transcript + OCR |
| **D5 `delegate`** | host blocks video but you configured a video model endpoint | text from that endpoint (planned) |

Routing is two-layered: first *what the LLM and host can consume* decides the
route, then your hardware decides *how* the local half runs (ASR, VAD, OCR,
scene detection). Every degradation is recorded with its reason.

> **Status: under active development (pre-v0.1).** See [PROGRESS.md](PROGRESS.md).
> Profiles, decisions and their evidence: [docs/DECISIONS.md](docs/DECISIONS.md).

## Principles

* **Local only.** vidroute never calls a cloud model itself and has no
  telemetry. Whether your LLM runs in the cloud is your agent's choice; vidroute
  marks every plan with `uploads_raw_video` and refuses native routes until you
  explicitly accept handing the raw video to the LLM provider.
* **Evidence order:** picture/sound > OCR > local transcript. Local transcripts
  are references, never facts.
* **Install only after consent.** Models are downloaded only after you confirm
  an install plan; every file is SHA-256 verified; restricted licenses need an
  explicit `--accept-license`.
* **CPU first.** A pure-CPU, no-compile path exists for every capability;
  GPUs are a verified upgrade with automatic fallback.

## Quick look

```bash
pipx install vidroute          # or: uv tool install vidroute
vidroute version --json
```

More commands (`doctor`, `install`, `llm resolve`, `plan`, `prepare`, `drill`)
land phase by phase; see [PROGRESS.md](PROGRESS.md).

## License

MIT — see [LICENSE](LICENSE). Downloaded models keep their own licenses; see
[docs/DECISIONS.md](docs/DECISIONS.md) and `THIRD_PARTY_LICENSES.md`.
