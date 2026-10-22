# vidroute output folder

`vidroute prepare` writes everything to `<out>/<slug>/` (`<slug>` = file name + short hash).
All times are **global** seconds of the original video.

| file | what it is |
|---|---|
| `SUMMARY.md` | start here: route, reasons, what was produced or skipped and why, warnings, budget |
| `ANALYSIS_GUIDE.md` | how to read and proofread for this route, minutes template, drill advice |
| `handoff.json` | machine-readable list of what to attach (schema `vidroute/handoff/1`) |
| `plan.json` | the routing plan (schema `vidroute/plan/1`) |
| `manifest.json` | every file with SHA-256, engines/models used and their licenses |
| `segments.json` | native routes: the video parts and their `offset_sec` |
| `native/` | native routes: the (transcoded) video or its parts |
| `contact-sheets/` | `sheet-NN.jpg` grids of keyframes with time labels, and `index.md` |
| `frames/` | full-resolution keyframes, `NNN_<time>.jpg` |
| `transcript.md/.srt/.json` | local transcript; JSON segments `{start, end, text, lang, source, engine}` |
| `ocr.md/.json` | on-screen text per keyframe (repeated lines omitted in the `.md`) |
| `timeline.md` | pictures, on-screen text and speech merged by time |
| `audio/16k.wav` | the extracted audio (used by drill `--asr`) |
| `drill/<start>-<end>/` | drill output: `frames/`, `contact-sheets/`, `transcript.*`, `ocr.*`, `timeline.md`, `drill.json` |

## handoff.json

```json
{
  "route": "frames",
  "reasons": ["..."],
  "degraded_from": [{"route": "native-av", "why": "..."}],
  "uploads_raw_video": false,
  "uploads_raw_audio": false,
  "attachments": [{"type": "image", "path": "contact-sheets/sheet-01.jpg", "note": "...", "est_tokens": 1500}],
  "text_context": [{"type": "transcript", "path": "transcript.md", "role": "sole_source", "attach": true}],
  "on_demand": [{"type": "image", "path": "frames/001_0m03s.jpg", "t": 2.75, "span": [0.0, 6.0]}],
  "guide": "ANALYSIS_GUIDE.md",
  "transcript_role": "sole_source",
  "budget": {"est_input_tokens": 1775, "images": 1, "video_seconds": 0, "budget_tokens": 100000, "over_budget": false},
  "needs_confirmation": [],
  "warnings": [],
  "drill": "vidroute drill \"<out_dir>\" --range 00:10-00:20"
}
```

- `attachments`: attach all of them. Video parts carry `segment: {id, start, end, offset_sec}`.
- `text_context`: read the items with `"attach": true`; the others repeat content (navigation only).
- `transcript_role`: `sole_source` (you cannot hear: proofread only with visible evidence),
  `reference` (you can hear: what you hear wins), `none` (no speech transcribed).
- Paths are relative to the folder holding `handoff.json`.
