# 已知问题

尚未解决的问题和未经核实的假设。每一条都说明对用户的影响，以及怎样才能解决。

## 尚未核实

* **Gemini CLI 原生视频尚未实测。** 宿主档案依据 v0.65.0 源码核对（`fileUtils.ts`，`MAX_FILE_SIZE_MB = 20`），
  标为 `source-verified`。要关闭这一条，需要用 Gemini API key 实际跑一次。
* **Qwen Code 原生视频取决于用户的模型配置。** 只有所配置的模型声明了 `modalities.video` 时才会附带视频。
  没有该配置的用户应运行 `vidroute host set qwen-code --video-delivery none`。
* **Cursor、Cline、Roo Code、Kilo Code 的档案为 `unverified`。** 采用保守假设（只支持图片）。
* **支持视频输入但限额未核实的模型**（Kimi K3、GLM-5.3 Flash、GLM-5V、MiniMax M3、Gemma 4）使用保守限额
  （`unverified: true`）。
* **Gemini 的内联限额在官方文档中有歧义**（同一页面里写着请求 20 MB，表格里又写"< 100 MB"）；vidroute 取 20 MB。
* **以下说法未重新核实：** FunASR llama.cpp 的 CUDA 构建只支持 sm_86；whisper.cpp 的 Vulkan 在 Windows 上需要自行编译；
  PP-OCRv6 的参数量。这些都不在默认路径中使用。

## 平台

* 开发在 Linux 上进行；Windows 由 CI（`windows-latest`）覆盖。
* 开发期间没有 NVIDIA GPU：CUDA 路径（`asr-faster-whisper-large-v3-turbo`）已实现并会自动回退到 CPU，
  但没有在真实硬件上验证。

## 安装与 Agent 宿主

* **技能发现只在 Claude Code 和 opencode 中实测过。** Codex CLI、Gemini CLI、Qwen Code、Cursor、Cline、
  Roo Code、Kilo Code 和 OpenClaw 的技能目录来自它们的文档（2026-10-23，D-040）。
* **同时接入 Claude Code 和另一个使用 `~/.agents/skills` 的宿主时，opencode 和 Cursor 可能会把技能列出两次**
  （它们两个目录都读）。两份内容完全相同。
* **MCP 注册**已为 Claude Code、Codex CLI、Gemini CLI、Qwen Code、Cursor 和 opencode 自动化；Cline、Roo Code、
  Kilo Code 和 OpenClaw 只装技能（它们的 MCP 设置保存在编辑器内部存储里）。
* **Linux/macOS 仍需用系统包管理器安装 ffmpeg**，通常需要 sudo。`setup` 会在下载任何东西之前告诉你。
* **Windows on Arm**：以 x64 仿真运行的 OCR / faster-whisper 环境尚未在真实 Arm 硬件上验证。

## 下载

* Windows 的 FFmpeg 便携包只从 GitHub 下载（中国大陆没有镜像）；有 winget 时会先尝试 winget。
* OCR 模型只有一个镜像（ModelScope `RapidAI/RapidOCR@v3.10.0`）；Hugging Face 上 PaddlePaddle 的 ONNX 文件是
  不同的文件，不会使用。

## 本地处理流程

* **机器转写会有常见的 ASR 错误**（产品名、同音词，例如 "Obsidian" → "opseing"、"Kate Rubins" → "Kate Rubens"，
  SenseVoice 把 "pieces of gold" 听成 "pieces of code"）。这是有意交给下游处理的：分析指南要求 LLM 只凭屏幕上的证据
  纠正，并保留纠错清单。以屏幕词汇作为热词的两遍 ASR（R7）属于后续扩展。
* **拼图中的小图太小，看不清 1080p 录屏里的界面文字**；OCR 和全分辨率的 `frames/` 可以弥补。指南会让 Agent 打开单帧查看。
* **说话人标签**只在由不同真人录音合成的对话上检查过，没有在真实会议上检查。几秒钟的发言可能被单独分成一个标签
  （见过一次：同一个声音被分成两个标签），重叠的发言会归到一个说话人。在 16 线程上，说话人区分大约增加语音时长的 0.08 倍。
  用内嵌字幕代替 ASR 时没有说话人标签。
* **运行被中断：** ASR 和已完成的步骤会续跑；被打断的说话人区分会从头开始。
* **长视频的镜头数可能超过 60 帧的预算。** 被漏掉的、在屏幕上停留 5 秒及以上的镜头会列在 `omitted-shots.md` 中并给出警告；
  要查看它们仍需 `drill`。
* **OCR 开销**：在 8 核 CPU 上，每张文字密集的 1080p 帧约 1.2 秒（最多 60 帧，最坏约 1–2 分钟）。

## 语言

* **仍有少量英文。** 为了安全，Windows 上的 `install.ps1` 和技能启动器 `vidroute.cmd` 的提示保留英文（Windows PowerShell 5.1 和
  cmd 会按系统代码页读取脚本，中文可能导致解析错误）。模型与宿主档案（`routing/*.yaml`、`models/manifest.yaml`）里的说明和出处、
  `-v` 调试日志、worker 内部错误，以及 ffmpeg/第三方库原样转出的错误信息也仍是英文。
