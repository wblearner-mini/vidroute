# Contributing LLM and host profiles

Routing is only as good as the profiles in `routing/llm-profiles.yaml` and
`routing/host-profiles.yaml`. Corrections are very welcome — with evidence.

## Rules

1. **Official source required.** Every changed value needs a URL to the provider's or host's own
   documentation, API reference, model card or source code (a specific file and version for code).
   Blog posts, forum answers and third-party tables are not enough on their own.
2. **Check date required.** Set `verified_at: YYYY-MM-DD` to the day you checked the source.
3. **Cannot verify? Mark it.** Use `unverified: true` and conservative values (fewer images, shorter
   video, smaller files). vidroute treats unverified profiles cautiously and warns the user.
4. **Limits are per request, as the host sends them.** For base64/inline uploads give the documented
   payload limit; vidroute converts it to a raw-file limit (`inline_is_base64`).
5. **Hosts:** describe what the agent can actually attach (`can_attach_image/video/audio`,
   `video_delivery`, `video_includes_audio`, `max_attachment_mb`) and set `confidence`:
   `verified` (you tested it live), `source-verified` (you read the code), `docs-only`, `unverified`.
   Put what you observed in `notes`.

## Workflow

```bash
# try your change without editing the bundled file
vidroute llm set my-model --modalities text,image,video --video-max-seconds 600 --video-max-mb 20
vidroute llm resolve --llm my-model --host gemini-cli --json
vidroute plan some-video.mp4 --llm my-model --host gemini-cli --json
```

Then edit the YAML, run `pytest tests/test_data_files.py tests/test_routing.py` (schema validation,
PyYAML parity, routing table), and open a pull request with the **profile correction** template.
Add a routing test case when the change alters a route.

## Matching

Entries are matched by glob on the normalized model id (`claude-*`, `qwen3.8-flash*`), most specific
first; `*` is the conservative fallback and must stay last. Aliases cover alternative spellings.
