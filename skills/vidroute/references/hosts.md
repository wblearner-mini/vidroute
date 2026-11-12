# 在宿主中附带 vidroute 的输出

`handoff.json` 列出要附带什么；本页说明怎么附带。vidroute 的宿主档案（`vidroute host list --json`）中有每项行为的证据和核实日期。
如果你的宿主附带不了计划所假设的内容，不要硬来：用正确的 `--host` 重新计划（或 `vidroute host set <host> --attach-video no ...`）。

| 宿主 | 图片 | 音频 | 视频 | 方法 |
|---|---|---|---|---|
| Claude Code（`claude-code`） | 支持 | 不支持 | 不支持 | 用绝对路径读取 `.jpg` 文件，它们会作为图片交给模型。`.mp4`/`.mp3` 会被当作二进制文件拒绝，所以 Claude Code 的计划使用 `frames` 或 `text`。 |
| Codex CLI（`codex-cli`） | 支持 | 支持（wav/mp3/m4a ≤ 50 MB） | 不支持 | 本地图片和音频以 data URL 发送；只有模型能接收音频时，音频才有用。 |
| Gemini CLI（`gemini-cli`） | 支持 | 支持 | 支持（每个文件 ≤ 20 MB） | `@path/to/file` 或 read-file 工具会内联返回文件，视频连同音频。分段文件按顺序逐个附带。 |
| Qwen Code（`qwen-code`） | 支持 | 模型声明时支持 | 模型声明时支持 | 只有所配置的模型声明了 `modalities.video`/`audio`，视频/音频才会内联发送；base64 内容必须小于 10 MB（文件约 7.4 MB）。 |
| opencode（`opencode`） | 支持（PNG/JPEG/GIF/WebP） | 不支持 | 不支持 | 只支持图片和文本；超过 20 MiB 的附件会被拒绝。 |
| Cursor、Cline、Roo Code、Kilo Code | 支持 | 不支持 | 不支持 | 只支持图片附件（未核实，保守估计）。 |
| 其他宿主 | ? | ? | ? | 用 `--host unknown` 计划（只支持图片）；打不开图片时使用 MCP 工具 `read_outputs`。 |

## 提示

- **先看拼图。** 每张小图都标有全局时间；`contact-sheets/index.md` 把小图对应到帧文件。只有小图太小时才打开 `frames/` 中的单帧。
- **分段。** 对于被切分的视频，按顺序附带各部分。第 *k* 部分内的时间 `t`，在整个视频中是 `offset_sec(k) + t`（见 `segments.json`）；
  各部分之间有几秒重叠，所以在两个部分都看到的事件只报告一次。
- **音频附件**（`audio/attach/part-NN.mp3`）只在你的模型能听、且用户同意交出原始音频时才存在。
- **预算。** `handoff.json` → `budget.est_input_tokens` 估算了附带所有标记内容的开销；如果 `over_budget` 为 true，全部附带前先询问用户，
  或者先从转写和前几张拼图开始。
