# Attaching vidroute outputs in your host

`handoff.json` lists what to attach; this page says how. vidroute's host profiles
(`vidroute host list --json`) hold the evidence and the date each behaviour was checked.
If your host cannot attach something the plan assumed, do not force it: plan again with
the right `--host` (or `vidroute host set <host> --attach-video no ...`).

| host | images | audio | video | how |
|---|---|---|---|---|
| Claude Code (`claude-code`) | yes | no | no | Read the `.jpg` files by absolute path; they reach the model as images. `.mp4`/`.mp3` are refused as binary, so plans for Claude Code use `frames` or `text`. |
| Codex CLI (`codex-cli`) | yes | yes (wav/mp3/m4a ≤ 50 MB) | no | Local images and audio are sent as data URLs; audio only helps if your model accepts audio. |
| Gemini CLI (`gemini-cli`) | yes | yes | yes (≤ 20 MB per file) | `@path/to/file` or the read-file tool returns the file inline, video with its audio. Attach segment files one by one, in order. |
| Qwen Code (`qwen-code`) | yes | if the model declares it | if the model declares it | Video/audio go inline only when the configured model declares `modalities.video`/`audio`; the base64 payload must stay under 10 MB (≈ 7.4 MB file). |
| opencode (`opencode`) | yes (PNG/JPEG/GIF/WebP) | no | no | Images and text only; attachments over 20 MiB are rejected. |
| Cursor, Cline, Roo Code, Kilo Code | yes | no | no | Image attachments only (unverified, conservative). |
| anything else | ? | ? | ? | Plan with `--host unknown` (image-only); use the MCP tool `read_outputs` if you cannot open images. |

## Tips

- **Contact sheets first.** Each tile is labelled with its global time; `contact-sheets/index.md`
  maps tiles to frame files. Open a single frame from `frames/` only when a tile is too small.
- **Segments.** For a split video, attach the parts in order. A time `t` inside part *k* is
  `offset_sec(k) + t` in the whole video (`segments.json`); parts overlap by a few seconds, so report
  an event seen in two parts once.
- **Audio attachments** (`audio/attach/part-NN.mp3`) only exist when your model can listen and the
  user agreed to hand the raw audio over.
- **Budget.** `handoff.json` → `budget.est_input_tokens` estimates the cost of attaching everything
  marked; if `over_budget` is true, ask the user before attaching it all, or start with the transcript
  and the first contact sheets.
