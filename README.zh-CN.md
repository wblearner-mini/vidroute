# vidroute

**让编程 Agent 能"看"和"听"本地视频。** · [English](README.md)

大多数 Agent 宿主（Claude Code、Codex CLI、opencode、Cursor……）只能把图片交给模型，交不了视频——
哪怕模型本身看得懂视频。vidroute 根据"**你用的大模型 × 你的宿主**"，确定性地、可解释地决定**该把什么交给
Agent 的 LLM**，在本机准备好这些材料，并生成一份阅读指南，让最终回答带全局时间戳、按证据校对。

| 路线 | 何时 | LLM 拿到什么 | 本地转写的角色 |
|---|---|---|---|
| **D1 `native-av`** | LLM ∩ 宿主能接收**带音轨**的视频 | 视频本身（必要时转码/分段以满足限额） | 带时间戳的参考 |
| **D2 `native-v`** | 能接收视频但听不到声音 | 去掉音轨的视频 | 唯一的语音来源 |
| **D3 `frames`** | 只能接收图片（最常见） | 带时间标签的关键帧拼图（模型能听时另附音频） | 唯一的语音来源 |
| **D4 `text`** | 只能接收文本 | 转写 + 屏幕文字的时间线 | 唯一来源 |
| D5 `delegate` | 宿主附不了视频，但你配置了视频模型端点 | 由该端点返回的文本 | 计划中（v0.2） |

路由分两层：先由**LLM 与宿主能消费什么**决定路线（并做准入检查：隐私、转码/分段后的文件限额、token 预算），
再由**你的硬件**决定本地部分怎么跑（语音识别、VAD、OCR、关键帧）。每次降级（`native → frames → text`）
都会记录原因。

> **状态：预发布（v0.1.0.dev0）。** CLI、MCP 服务和 Agent Skill 已完整可用，进度见
> [PROGRESS.md](PROGRESS.md)；所有选型及其证据见 [docs/DECISIONS.md](docs/DECISIONS.md)。

## 30 秒上手

```bash
pipx install vidroute                 # 或：uv tool install vidroute
vidroute doctor                       # 本机能力：CPU/GPU 档位、ffmpeg、已装模型
vidroute install --capability asr --capability vad --capability ocr --capability scene
#   ^ 只打印安装计划（来源、体积、许可），不下载任何东西；确认后加 --yes 安装
#     （CPU 基线约占 0.8 GB 磁盘：Python 运行时、两个 worker 环境、模型）
vidroute run 会议.mp4 --llm claude-opus-5-5 --host claude-code --task minutes --out ./video-out
```

然后打开 `./video-out/<slug>/SUMMARY.md`——更好的方式是让你的 Agent 来读（见下文）。
首个 PyPI 版本发布前，可从 GitHub 安装：`pipx install git+https://github.com/wblearner-mini/vidroute`。

需要 Python 3.10+ 和 PATH 上的 **ffmpeg**（`winget install --id Gyan.FFmpeg`、`brew install ffmpeg`、
`sudo apt install ffmpeg`）。不需要 GPU、编译器或其他系统软件：模型运行在 vidroute 用 `uv` 管理的隔离环境中。

## 在 Agent 里使用

**Claude Code 插件**（Agent Skill）：

```text
/plugin install vidroute --marketplace wblearner-mini/vidroute
```

**任何支持 Agent Skills 的宿主**：把内置技能复制到宿主的技能目录，例如 Claude Code：

```bash
cp -r "$(vidroute skill path)" ~/.claude/skills/
```

**MCP**（stdio，纯标准库实现，不依赖 SDK）：

```bash
claude mcp add vidroute -- vidroute-mcp
```

或在任意 MCP 客户端配置中：

```json
{"mcpServers": {"vidroute": {"command": "vidroute-mcp"}}}
```

工具：`resolve_llm`、`doctor`、`install`（需 `confirm`）、`plan_video`、`prepare_video`（原生路线需
`accept_raw_video_handoff`）、`drill_video`、`probe_video`、`list_models`、`read_outputs`（把拼图、关键帧、
音频和文本作为 MCP 内容返回）。耗时工具会上报进度并支持取消。

Agent 的工作流（见 [skills/vidroute/SKILL.md](skills/vidroute/SKILL.md)）：
`llm resolve` → `doctor` / `install`（先征得你同意）→ `plan`（原片要离开本机前先问你）→ `prepare` →
读 `SUMMARY.md`、`ANALYSIS_GUIDE.md`、`handoff.json` → 写带 `[mm:ss]` 时间戳的回答 → 需要细节时 `drill` 某个时间段。

## 命令行

| 命令 | 作用 |
|---|---|
| `vidroute doctor [--refresh]` | 系统、CPU（AVX2）、内存、GPU、ffmpeg、运行时、已装模型、档位 |
| `vidroute install [--capability X] [--model ID] [--yes]` | 计划 → 下载（多镜像、断点续传、SHA-256）→ 冒烟测试 |
| `vidroute llm resolve --llm ID [--host ID]` | 某 LLM 在某宿主上能消费什么，以及依据 |
| `vidroute probe 视频` | 时长、流、字幕、语音占比、文字密度（< 10 秒） |
| `vidroute plan 视频 --llm ID [--host ID] [--task] [--prefer]` | 可解释的两层计划（`plan.json`） |
| `vidroute prepare 视频 [--out 目录] [--accept-raw-video-handoff]` | 执行计划，生成交付物 |
| `vidroute run 视频 --llm ID ...` | plan + prepare |
| `vidroute drill 输出目录 --range 01:20-02:00 [--fps N] [--asr] [--ocr]` | 对某时间段补充细节 |
| `vidroute models list\|verify\|remove`、`clean`、`config get\|set`、`llm set\|list\|sync`、`host set\|list`、`skill path` | 管理类命令 |

所有命令都支持 `--json`，在 stdout 输出一个统一外壳
（`{"ok", "schema": "vidroute/1", "data", "warnings", "error": {"code", "message", "hint"}}`），日志走 stderr。
退出码：`0` 成功 · `1` 用法错误 · `2` 需要安装 · `3` 需要你确认 · `4` 硬件不足 · `5` 运行失败。
`error.hint` 永远是一条能解决问题的命令。

## `prepare` 的输出

```text
<out>/<slug>/
  SUMMARY.md          必读首页：路线、原因、产出/跳过及原因、警告、预算
  ANALYSIS_GUIDE.md   证据层级、本路线的校对规则、纪要模板、钻取建议
  handoff.json        该附带什么（schemas/handoff.schema.json）
  plan.json  manifest.json（文件 + SHA-256 + 所用引擎及许可）  segments.json（分段时）
  native/             转码后的视频或分段                — 原生路线
  contact-sheets/     带时间标签的关键帧拼图 + index.md  — frames 路线
  frames/  transcript.{md,srt,json}  ocr.{md,json}  timeline.md  audio/16k.wav  drill/<区间>/
```

所有时间戳都是**全局时间**（分段与钻取输出中也是）。证据层级：画面/声音 > 屏幕文字（OCR）> 本地转写；
指南要求 LLM 只能凭看得见（或听得见）的证据改写转写，并保留纠错清单。

## 支持的模型与宿主

LLM 档案（`routing/llm-profiles.yaml`，`vidroute llm list`）覆盖 Gemini、通义千问 Omni/VL/Plus 系列、
Claude、GPT-5、Grok 4、DeepSeek、Kimi，以及对未知模型的保守默认（文本 + 图片）。每条档案都注明官方来源和
核实日期；无法核实的值标为 `unverified` 并取保守值。可以自行覆盖或补充：

```bash
vidroute llm set my-model --modalities text,image,video --video-max-seconds 600 --video-max-mb 20
vidroute llm sync --provider openrouter     # 可选：从 OpenRouter 模型列表获取模态与上下文长度
```

| 宿主 | 图片 | 音频 | 视频 | 档案可信度 |
|---|---|---|---|---|
| Claude Code | ✅ | — | — | 已实测 |
| Codex CLI | ✅ | ✅ | — | 已核对源码 |
| Gemini CLI | ✅ | ✅ | ✅ 原生，≤ 20 MB/文件 | 已核对源码 |
| Qwen Code | ✅ | ✅* | ✅* 原生（*需模型配置声明该模态） | 已核对源码 |
| opencode | ✅ | — | — | 已实测 |
| Cursor、Cline、Roo Code、Kilo Code | ✅ | — | — | 未核实（保守） |

同一个视频、不同计划：Gemini + Gemini CLI → `native-av`；Qwen3.6-Plus + Qwen Code → `native-v`；
Claude + Claude Code → `frames`；DeepSeek + opencode → `text`。欢迎附官方来源提交更正。

## 本地模型与许可

不经你确认的安装计划，不下载任何东西；每个文件都对照固定的 SHA-256 校验，且只来自白名单域名
（GitHub、Hugging Face、hf-mirror、ModelScope）。模型权重从不放进本仓库或 Python 包。

| 模型 | 用途 | 语言 | 体积 | 许可 |
|---|---|---|---|---|
| Silero VAD | 语音活动检测 | 任意 | 0.7 MB | MIT |
| SenseVoice-Small int8（默认） | 语音识别 | 中、英、日、韩、粤 | 240 MB | FunASR 模型许可 1.1（可免费使用含商用；须署名并保留模型名） |
| Whisper small int8（其他语言默认） | 语音识别 | 99 种语言 | 375 MB | MIT |
| faster-whisper large-v3-turbo（GPU，可选） | 语音识别 | 99 种语言 | 1.6 GB | MIT |
| PP-OCRv6 small（RapidOCR ONNX） | 屏幕文字 | 中、英等 | 31 MB | Apache-2.0 |
| ffmpeg（自行安装） | 解码、关键帧、转码 | — | — | LGPL/GPL（取决于你的构建） |

备选与实验模型（SenseVoice 粤语版、FunASR-Nano、Qwen3-ASR、PP-OCRv6 tiny）见 `vidroute models list`，
详见 [models/manifest.yaml](models/manifest.yaml)。

## 隐私与安全

- **纯本地。** vidroute 自身从不调用云端模型，没有遥测。你的 Agent 用的 LLM 是否在云端，由你的 Agent 决定。
- **原始媒体外传需你同意。** 计划会标注 `uploads_raw_video`；原生路线（以及附带音轨）没有
  `--accept-raw-video-handoff` 就拒绝执行，技能也要求 Agent 先征得你的同意。`--prefer privacy`
  会让原片留在本机，只交关键帧和文字。
- **提取出的文本是数据，不是指令。** 转写、字幕和 OCR 里可能有提示注入，指南要求 LLM 忽略其中的任何指令。
- **只写入 `VIDROUTE_HOME`（默认 `~/.vidroute`）和你指定的 `--out`**；从不删除你的文件；日志不记录视频内容。
- 请只处理你有权处理的视频，尊重隐私、知情同意与版权。

## 平台

Windows 10/11、macOS、Linux；每次提交都在 CI（`windows-latest`）上测试 Windows。常见问题与解决办法见
[references/windows-troubleshooting.md](references/windows-troubleshooting.md)。CPU 路径永远可用；
NVIDIA GPU 只有在 CUDA 冒烟测试通过后才启用，失败自动回退 CPU。

## 已知问题

见 [KNOWN_ISSUES.md](KNOWN_ISSUES.md)，例如 Gemini CLI 原生视频尚未实测、Cursor/Cline 档案未核实、
CUDA 路径未在真实硬件上验证。

## 开发

```bash
python -m venv .venv && .venv/bin/pip install --require-hashes -r requirements-dev.lock.txt
.venv/bin/pip install --no-deps -e .
.venv/bin/pytest                              # 离线：ffmpeg + 假引擎
VIDROUTE_REAL_MODELS=1 .venv/bin/pytest tests/test_real_models.py   # 使用已安装的真实模型
```

编排器零运行时依赖；重型库运行在隔离的 worker 环境中。架构与决策见 [docs/DECISIONS.md](docs/DECISIONS.md)，
进度见 [PROGRESS.md](PROGRESS.md)。

## 许可证

MIT，见 [LICENSE](LICENSE)。下载的模型保留各自的许可（见上表及每次输出的 `manifest.json`）。
