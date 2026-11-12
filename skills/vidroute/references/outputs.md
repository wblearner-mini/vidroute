# vidroute 输出目录

`vidroute prepare` 把所有内容写到 `<out>/<slug>/`（`<slug>` = 文件名 + 短哈希）。所有时间都是原视频的**全局**秒数。

| 文件 | 是什么 |
|---|---|
| `SUMMARY.md` | 从这里开始：路线、原因、生成或跳过了什么及原因、警告、预算 |
| `ANALYSIS_GUIDE.md` | 本路线下如何阅读和校对、纪要模板、钻取建议 |
| `handoff.json` | 机器可读的附带清单（schema `vidroute/handoff/1`） |
| `plan.json` | 路由计划（schema `vidroute/plan/1`） |
| `manifest.json` | 每个文件的 SHA-256、所用引擎/模型及其许可 |
| `segments.json` | 原生路线：视频各部分及其 `offset_sec` |
| `native/` | 原生路线：（转码后的）视频或其分段 |
| `contact-sheets/` | 带时间标签的关键帧拼图 `sheet-NN.jpg`，以及 `index.md` |
| `frames/` | 全分辨率关键帧，`NNN_<时间>.jpg` |
| `transcript.md/.srt/.json` | 本地转写；JSON 片段为 `{start, end, text, lang, source, engine}` |
| `ocr.md/.json` | 每个关键帧上的屏幕文字（`.md` 中省略重复的行） |
| `timeline.md` | 按时间合并的画面、屏幕文字和语音 |
| `audio/16k.wav` | 提取出的音频（drill `--asr` 会用到） |
| `drill/<start>-<end>/` | drill 输出：`frames/`、`contact-sheets/`、`transcript.*`、`ocr.*`、`timeline.md`、`drill.json` |

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
  "on_demand": [{"type": "image", "path": "frames/001_0m02s.jpg", "t": 2.75, "span": [0.0, 6.0]}],
  "guide": "ANALYSIS_GUIDE.md",
  "transcript_role": "sole_source",
  "budget": {"est_input_tokens": 1775, "images": 1, "video_seconds": 0, "budget_tokens": 100000, "over_budget": false},
  "needs_confirmation": [],
  "warnings": [],
  "drill": "vidroute drill \"<out_dir>\" --range 00:10-00:20"
}
```

- `attachments`：全部附带。视频分段带有 `segment: {id, start, end, offset_sec}`。
- `text_context`：读取 `"attach": true` 的项；其他项的内容是重复的（只用于导航）。
- `transcript_role`：`sole_source`（你听不到：只凭看得见的证据校对）、`reference`（你能听到：以你听到的为准）、
  `none`（没有转写出语音）。
- 路径相对于 `handoff.json` 所在的目录。
