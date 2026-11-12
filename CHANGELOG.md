# 更新日志

这里记录所有值得注意的变更。格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)。

## [未发布]

## [0.1.0] - 2026-11-12

首个公开版本。

### 变更

- 文档、技能、分析指南以及 CLI/MCP 面向用户的输出全部改为中文；README 只保留一份中文版（原 `README.zh-CN.md` 已合并）。
  命令、参数、JSON 字段名和错误码保持不变。Windows 的 `install.ps1` 和 `vidroute.cmd` 仍输出英文，以免旧版
  PowerShell/cmd 按系统代码页读取脚本时出错。
- 移除了只对开发过程有用的 `PROGRESS.md` 和 `docs/RELEASING.md`。

### 新增

- 说话人标签：`--task minutes` 或 `--diarize on` 时，转写中会标出 `Speaker 1`、`Speaker 2`……；
  新增默认模型 `diarize-pyannote-campplus`（34 MB，纯 CPU）。
- 关键帧预算漏掉了在屏幕上停留 5 秒及以上的镜头时，生成 `omitted-shots.md` 并给出警告。
- `vidroute doctor`：并发探测系统/CPU/内存/GPU/ffmpeg/运行时，每项 5 秒超时，结果缓存 6 小时，
  档位分为 `cpu-lite` / `cpu-full` / `gpu-cuda`。
- `vidroute install`：安装计划、白名单下载与镜像回退、断点续传、SHA-256 校验、由 uv 管理的 worker 环境、
  冒烟测试、许可确认，以及 `models list|verify|remove`、`clean`。
- `vidroute llm resolve|set|unset|list|sync`、`host set|unset|list`：解析 LLM × 宿主的能力，每个值都注明来源；
  内置带出处的档案。
- `vidroute probe` 与 `vidroute plan`：快速获取视频信息，生成可解释的两层计划
  （native-av / native-v / frames / text，并记录每次降级）。
- `vidroute prepare` / `run`：转写（优先用字幕，VAD、语种识别、可续跑的 ASR）、关键帧与拼图、OCR、时间线、
  原生路线所需的转码/分段视频，以及 `SUMMARY.md`、`ANALYSIS_GUIDE.md`、`handoff.json`、`segments.json`、`manifest.json`。
- `vidroute drill`：对某个时间段补充细节。
- MCP 服务 `vidroute-mcp`；Agent Skill `skills/vidroute`；Claude Code 插件与 marketplace 清单；`vidroute skill path`。
- 中文文档；Windows 故障排查；路由参考。
- `vidroute setup`：一份计划、一次确认，搞定 ffmpeg、运行时与模型，把 Agent Skill 装进找到的每个 Agent 宿主
  （Claude Code、Codex CLI、Gemini CLI、Qwen Code、opencode、Cursor、Cline、Roo Code、Kilo Code、OpenClaw；
  `--mcp` 还会注册 MCP 服务），最后做端到端自检；`setup --remove` 撤销宿主部分。`doctor` 会报告哪些宿主能看到技能。
- 一行安装脚本 `install.ps1`（Windows）与 `install.sh`（Linux、macOS），不需要预装 Python。
- `docs/AGENT_SETUP.md`：给 AI Agent 的分步安装说明。
- 技能启动器 `scripts/vidroute`（sh）与 `scripts/vidroute.cmd`：PATH 上没有 `python` 也能用（会退回到 uv），
  并使用 `vidroute setup` 记录的解释器。
- `--mirror cn`，以及在中国大陆自动为 PyPI、uv 管理的 Python 和模型启用镜像；配置项 `pypi_index`、
  `python_mirror`、`ffmpeg_source`。
- Windows：winget 不可用或失败时，使用固定版本的 FFmpeg 9.0.2 便携包。
- Windows on Arm：OCR 与 faster-whisper 环境使用 x64 worker Python。

### 修复

- 安装脚本不再需要 git、unzip 或 Python：默认来源改为源码压缩包，没有其他工具时 `install.sh` 用 coreutils 和
  gzip 解开 uv 的 wheel。下载卡住 20 秒即判定失败并换下一个来源；缺少 curl/wget 时一开始就报告，并给出要运行的命令。
- 缺少 ffmpeg 且无法代为安装时，`vidroute setup` 会在下载前停下。
- `--force-asr` 在人声检测没发现语音时也会执行。
- 模型损坏时会报告出来，而不是悄悄换成下一个引擎。
- 大号标题行也算作屏幕文字，内容稀疏的幻灯片也会做 OCR。
- 对同一输出目录的多次运行会互相等待而不是崩溃；后一次直接复用结果。
- 空文件、被截断或损坏的文件、不可写的目录、磁盘已满、ffmpeg 无法运行时，都会给出清楚的提示。运行失败后不再留下空目录。
- 长时间运行被中断后，会从最后 60 秒语音处续跑，说话人分段得以保留。
- 没有终端时 `setup` 和 `install` 也会展示计划；另一个技能挡住某个宿主时，`setup` 会给出警告。
- `doctor`、档位和线程数会遵守容器限制与 CPU 亲和性。
- 错误提示里曾写了不存在的参数（`install --retry`、`install --repair`）；现在有测试会把源码中引用的每条
  `vidroute ...` 命令与真实 CLI 对照。
- `vidroute install --force` 现在会真正重建 worker 环境。

[未发布]: https://github.com/wblearner-mini/vidroute/compare/v0.1.0...main
[0.1.0]: https://github.com/wblearner-mini/vidroute/releases/tag/v0.1.0
