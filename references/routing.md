# How vidroute routes a video

Routing is deterministic code driven by `routing/rules.yaml`; neither the agent nor an LLM takes
part in it. `vidroute plan --json` shows every decision with its reason.

## Layer 1 — what the LLM gets

1. **Effective capabilities** `M = LLM ∩ host`: the LLM profile (`routing/llm-profiles.yaml`, user
   overrides, optional OpenRouter cache, flags) says what the model accepts; the host profile
   (`routing/host-profiles.yaml`) says what the agent can attach. Anything the model accepts but the
   host blocks is reported as *host-limited* (with a D5 suggestion).
2. **Candidate order**: `native-av` (video with audio) or `native-v` (video, no audio) when `M`
   has video, then `frames` when it has images, then `text`.
3. **Admission** for native routes, in order: `--prefer privacy` or config
   `allow_raw_video_handoff=false` → skip; the file must fit the per-request limits after
   transcoding (720/540/480/360 p, ≤ 10 fps, mono AAC, never upscaled) or splitting into at most
   `max_segments` (8) parts with `segment_overlap_seconds` (3) overlap; the estimated input tokens
   must fit the budget (`budget_fraction` 0.5 of the context window, or `budget_tokens`).
   Over budget: `--prefer cost` degrades, `quality`/`privacy` ask (`needs_confirmation`).
4. **Degradation** follows `native-av → frames → text` and `native-v → frames → text`; every
   skipped route lands in `degraded_from` with its reason. `prepare` can add one more: when a
   transcoded part still exceeds the limit after three passes, it re-plans with `frames`.
5. **Transcript role**: `reference` when the model hears the audio (`native-av`, or `frames` with an
   attached audio track), otherwise `sole_source`. For `native-av` the transcript is attached when
   the video is longer than `native_only_max_minutes` (10), is split, or the task is
   `minutes`/`transcript`.

## Layer 2 — what runs locally

| rule | behaviour | knobs (`local:` / `frames:`) |
|---|---|---|
| R1 | embedded text subtitles become the transcript when they cover ≥ 70 % of the detected speech | `embedded_subtitle_min_coverage` |
| R2 | no ASR when speech is below 5 % of the duration (full VAD confirms the sampled probe) | `speech_ratio_skip_asr` |
| R3 | ASR engine from the language (`zh`/`yue` → `zh` chain, else `multi`) and the tier; language detected on the first 30 s of speech; failures fall down the chain | `asr_chains`, `zh_languages`, `language_probe_seconds` |
| R4 | OCR on keyframes only, when text density ≥ 0.3 (or `--ocr on`) | `ocr_text_density_threshold`, `ocr_chain` |
| R5 | keyframes: change-based shots, repeated pictures folded, coverage-first budget `min(max_frames, max_images)`; contact sheets first, single frames on demand | `max_frames`, `base_interval_seconds`, `change_threshold`, `min_interval_seconds`, `contact_sheet` |
| R6 | longer than 30 min: sparser candidates, keyframe-only decoding when dense enough, resumable ASR batches | `long_video_minutes`, `max_candidates` |
| R8 | vidroute never calls a cloud service | — |

Tiers come from `vidroute doctor`: `cpu-lite` (< 7.5 GB RAM, no AVX2 or unknown), `cpu-full`,
`gpu-cuda` (NVIDIA ≥ 4 GB VRAM **and** a passed CUDA smoke test), `gpu-vulkan` (reserved).

## Estimates

`estimates:` in `rules.yaml` only drive `est_seconds` and `est_input_tokens` in `plan.json`:
transcript ≈ 3.5 tokens per second of speech, OCR ≈ 120 tokens per frame, ASR real-time factors per
tier, OCR seconds per frame, transcode real-time factor. They never change a result.

## Changing the rules

Edit `routing/rules.yaml` in a checkout, or override single keys in `$VIDROUTE_HOME/config.json`
(`vidroute config set budget_fraction 0.3`). Profile changes need an official source URL and the
date you checked it; see `docs/DECISIONS.md` for how existing values were verified.
