# 决策与证据记录

vidroute 中每一个不显而易见的选择都记录在这里，并附上证据（URL、版本、日期）。最初设计说明中的事实都对照一手来源
重新核实过；两者不一致时，**以重新核实的结果为准**，并注明差异。除非另有说明，下面的核实都在 **2026-10-21** 完成。

图例：✅ 已确认 · ⚠️ 已确认但有更正 · ❓ 未核实（不放进默认路径，或标为 `unverified`）。

---

## D-000 项目名：`vidroute`（由工作名 `vidsense` 改名）

* `vidsense`：PyPI 上可用（`/pypi/vidsense/json` → 404），但有 **46 个 GitHub 仓库**用了这个名字，很多还在同一领域
  （AI 视频理解、转写、视频 RAG）。维护者据此选择了 `vidroute`。
* `vidroute`：PyPI 404（可用），GitHub `vidroute in:name` → 0 个仓库。`vidroute-mcp` 也可用。这个名字描述了核心差异点
  （按 LLM × 宿主能力路由），且不含任何厂商商标。

## D-001 许可证：MIT

MIT。默认路径上的所有依赖和模型都兼容（见 D-007、D-008 和 `THIRD_PARTY_LICENSES.md`）。模型权重从不随仓库或 wheel
再分发；它们由用户在确认安装计划后自行下载，因此模型许可作用于用户自己的副本。

## D-002 内核 + 外壳；编排器只用标准库

* 所有逻辑都在 `vidroute.api` / `vidroute.core`；外壳（CLI、MCP、Skill 脚本、未来的桌面应用）只负责转换参数和统一外壳。
* 编排器**零运行时依赖**（`dependencies = []`）。重型库运行在隔离的 worker 环境中（D-004），通过 stdin/stdout 上的
  JSON lines 与编排器通信。
* **不用 PyYAML 解析 YAML**：档案、规则和清单文件用 YAML 写，便于人工审阅，由 `vidroute.core.util.yamlish` 解析——
  这是一个只支持文档化子集的严格加载器（YAML 1.2 核心标量；不支持锚点、标签和多文档）。`tests/test_data_files.py`
  断言每个内置文件用 PyYAML 解析的结果完全一致，所以贡献者可以放心使用普通的 YAML 工具。
* **不用 MCP SDK 实现 MCP 服务**：`mcp` 2.3.0（PyPI，2026-10-02）会引入 pydantic、httpx2、jsonschema、opentelemetry
  等依赖。stdio 传输就是按行分隔的 JSON-RPC 2.0，用标准库就能实现；官方 SDK 只作为*测试用*客户端，检查协议一致性。

## D-003 数据布局与打包

数据文件放在仓库根目录（`routing/`、`models/`、`schemas/`、`references/`、`envs/`），这样仓库本身也能当作 Agent Skill
目录，档案的改动也便于审阅。Hatchling 的 `force-include` 把它们复制进 wheel 的 `vidroute/_data/`；
`vidroute.core.resources` 两种布局都能解析。构建后端：hatchling ≥ 1.27（PEP 639 `license = "MIT"`）。

## D-004 worker 环境：`sherpa`、`ocr`、`whisper`

* 最初的设想是一个 `onnx` 组（onnxruntime + sherpa-onnx + OCR）。但 sherpa-onnx 的 wheel 自带 ONNX Runtime 库，
  而 RapidOCR 使用 `onnxruntime` 包；在同一进程里加载两份 ONNX Runtime 是 Windows 上 DLL 冲突的已知来源。
  因此 vidroute 使用独立的组和独立的 worker 进程：
  * `sherpa`：sherpa-onnx 1.13.8（Apache-2.0；wheel 覆盖 cp37–cp314，19 个 Windows wheel；PyPI 2026-09-10）+ numpy——VAD 和 ASR。
  * `ocr`：rapidocr 3.10.0（Apache-2.0，2026-10-08）+ onnxruntime 1.31.0（MIT，需要 Python ≥ 3.11）
    + opencv-python-headless、numpy……
  * `whisper`：faster-whisper 1.2.1（MIT）+ ctranslate2 4.8.3；NVIDIA 平台上再加 `nvidia-cublas-cu12` 和
    `nvidia-cudnn-cu12==9.*`。
* 无论宿主解释器是什么，worker 环境都使用由 `uv` 获取的 **Python 3.12**（onnxruntime 1.31 需要 ≥ 3.11，numpy 2.5
  需要 ≥ 3.12）。没有 `uv` 时，vidroute 会提议从 PyPI 把 `uv==0.13.4` 装进 `$VIDROUTE_HOME/runtimes/uv`
  （属于经确认的安装计划，不做任何系统级改动）。
* 锁文件（`envs/*.lock.txt`）用 `uv pip compile --universal --generate-hashes` 生成，并用 `--require-hashes` 安装
  （供应链固定）。

## D-005 默认本地模型（基础包 = D3/D4 开箱即用）

| 能力 | 默认 | 理由 | 证据 |
|---|---|---|---|
| vad | sherpa-onnx 分发的 Silero VAD ONNX | MIT，0.6 MB，CPU | k2-fsa/sherpa-onnx 的 `asr-models` 发布中的 `silero_vad.onnx`（643,854 B）；GitHub digest `sha256:9e2449e1…` = 本地哈希 |
| vad（回退） | ffmpeg `silencedetect` | 无需下载 | 内置 |
| asr 中/英/日/韩/粤 | 经 sherpa-onnx 运行的 SenseVoice-Small int8（2024-07-17） | 快速的非自回归 CPU ASR；普通话/粤语最好的开源选择 | 压缩包 digest `7d1efa21…`（163,002,883 B）；成员文件在本地计算哈希；`model.int8.onnx` = HF LFS oid `c71f0ce0…` |
| asr 多语种（CPU） | 经 sherpa-onnx 运行的 Whisper small int8（可选包） | 99 种语言，MIT | HF `csukuangfj/sherpa-onnx-whisper-small@8f3c18b3` 的 LFS oid；tokens 在本地计算哈希 |
| asr（GPU） | faster-whisper large-v3-turbo（`dropbox-dash/faster-whisper-large-v3-turbo@0a363e91`，MIT） | 标准 CUDA 路径 | `model.bin` 的 HF LFS oid；小文件在本地计算哈希 |
| ocr | 经 RapidOCR 3.10.0 运行的 PP-OCRv6 small det/rec | Apache-2.0，约 31 MB，50 种语言，CPU | ModelScope 文件 API 与 RapidOCR 的 `default_models.yaml` 中 sha256 一致 |
| scene | ffmpeg 场景分数（`select`/`scene`） | 无需模型，无 Python 依赖 | 内置 |

基础包：Silero VAD + SenseVoice-Small（≈ 240 MB）+ `sherpa` 环境。OCR、Whisper 和 GPU 运行时是可选包
（确认前会显示大小、许可和是否需要 GPU）。

## D-006 选用哪个 SenseVoice 构建

sherpa-onnx 发布了两个 int8 构建。**2025-09-09** 构建的 README 写明它转换自 `ASLP-lab/WSYue-ASR`
（`sensevoice_small_yue`），即粤语微调版。**2024-07-17** 构建转换自 FunAudioLLM/SenseVoice。vidroute 把 2024-07-17
作为通用默认，把 2025-09-09 作为 `asr-sensevoice-yue-int8`（备选）提供。

## D-007 SenseVoice 许可

FunASR 模型开源许可 v1.1（<https://github.com/modelscope/FunASR/blob/main/MODEL_LICENSE>，最后修改于 2024-11-20）：
允许免费使用、复制、修改和分享；**必须署名并保留模型名**；含有"不得诋毁"条款和自动修订的措辞。它不禁止商用，模型也不需要
申请访问，因此不属于"受限"许可，SenseVoice 可以留在默认路径中。但它毕竟是自定义许可：vidroute 在每份安装计划中展示它，
在每个输出的 `manifest.json` 中记录模型名，并在 `THIRD_PARTY_LICENSES.md` 中署名。偏好 OSI 许可权重的用户可以改用
Whisper（MIT）。

## D-008 OCR 运行时

RapidOCR 3.10.0 默认使用 PP-OCRv6（`ocr_version: PP-OCRv6`，small det/rec），并在 ModelScope 上托管 ONNX 转换版
（`RapidAI/RapidOCR@v3.10.0`）。Hugging Face 上 PaddlePaddle 导出的 ONNX（`PaddlePaddle/PP-OCRv6_small_det_onnx`
等，Apache-2.0）是不同的文件（没有内嵌字典），所以 v0.1 的 OCR 只使用 ModelScope 镜像（单一镜像；见 KNOWN_ISSUES）。
文字行方向分类器被关闭（视频帧都是正的），省掉一次下载。

## D-009 场景检测

PySceneDetect 0.7.2（BSD-3）需要 `opencv-python`，与 RapidOCR 需要的 `opencv-python-headless` 冲突。ffmpeg 的场景分数
无需额外安装就能给出确定性的场景切换候选，因此作为默认；PySceneDetect/TransNetV2 留待以后。

## D-010 GPU 与其他运行时

* faster-whisper：最新的 CTranslate2 只支持 CUDA 12 + cuDNN 9；pip 方式为 `nvidia-cublas-cu12 nvidia-cudnn-cu12==9.*`
  （faster-whisper README）。GPU 只是*升级*：CPU 基线必须先能用，GPU 冒烟测试失败会回退到 CPU 并记录原因。
* FunASR 有 llama.cpp 运行时（FunAudioLLM/Fun-ASR 发布 `runtime-llamacpp-v0.2.1`，2026-08-27，含 windows-x64、-avx2、
  -cuda 和 -vulkan 压缩包）。未采用：sherpa-onnx 现在能直接运行 Fun-ASR-Nano（`sherpa-onnx-funasr-nano-int8-2025-12-30`），
  免去第二个原生运行时。"CUDA 构建只支持 sm_86"这一说法未重新核实 ❓。
* whisper.cpp Vulkan：v0.1 未采用 ❓。定义了 `gpu-vulkan` 档位，但目前还没有引擎用到它。

## D-011 下载：镜像与域名白名单

允许的域名（后缀匹配，包括重定向）：`github.com`、`githubusercontent.com`（发布附件会重定向到
`release-assets.githubusercontent.com`）、`huggingface.co`、`hf.co`（LFS/Xet 重定向到 `*.cdn.hf.co`）、`hf-mirror.com`
（中国大陆广泛使用的社区 HF 镜像；因为每个文件都校验 sha256，所以是安全的）、`modelscope.cn`。所有核实过的来源都支持
HTTP `Range`，因此下载可以续传。

## D-012 重新核实的 LLM 事实 → `routing/llm-profiles.yaml`

| 原说法 | 结果 | 证据 |
|---|---|---|
| Qwen3.8-Omni-Flash：文本/图片/音频/视频输入，文本输出，1M 上下文 | ✅ 1M 上下文窗口，最大输入 991,808（非思考模式） | <https://www.alibabacloud.com/help/en/model-studio/qwen3-8-omni-flash>（2026-10-11 更新） |
| ……2026 年 9 月发布 | ✅（arXiv 2609.25611 "Qwen3.8-Omni"；第三方评测给出的日期为 2026-09-18） | |
| Qwen3.8-Omni-Flash 视频 ≤ 2 小时、音频 ≤ 3 小时（"来自第三方，须核实"） | ✅ 官方：视频 2 小时、64 个文件、URL 方式 2 GB、base64 < 10 MB；音频 3 小时 | <https://www.alibabacloud.com/help/en/model-studio/qwen-omni> |
| Qwen3.5-Omni 视频 ≤ 1 小时、音频 ≤ 3 小时 | ✅；上下文 262,144（最大输入 196,608） | 同上 + 模型页面 |
| Qwen3-Omni-Flash ≈ 150 秒 | ✅ 视频 150 秒、音频 20 分钟、每次请求只能有一种非文本模态、上下文 65,536 | 同上 |
| Token 开销 | Qwen：音频 7 tok/s（3.8/3.5），12.5（3-Omni）；视频估算 FPS 2，每帧对 ≤ 640 tok，视觉 token ≤ 180,224 | qwen-omni 页面 |
| Qwen-MM-Plugins 回退链 | ✅ 配置了 OSS 时上传，否则用采样帧 + 适配后的连续音频；音频为 16 kHz 单声道 MP3，码率在 16–64 kbps 间逐级选择；内联预算 ≈ 0.97 × 10 MB base64 | `QwenLM/Qwen-MM-Plugins`（Apache-2.0）`src/shared/omni_media.py` |
| Gemini：1 FPS，约 300 tok/s（低分辨率约 100） | ⚠️ 1 FPS ✅；**现在默认是低媒体分辨率 ≈ 100 tok/s**（每帧 66 tok + 音频 32 tok/s），高分辨率 ≈ 300；1M 上下文模型在低分辨率下可接收 3 小时视频，高分辨率下 1 小时 | <https://ai.google.dev/gemini-api/docs/video-understanding> |
| Gemini：> 约 20 MB 走 File API；每个文件 2 GB | ⚠️ 请求 > 20 MB 时走 File API ✅；File API 最大 2 GB（免费）/ 20 GB（付费）；同一页面的表格写着内联 < 100 MB——vidroute 采用保守的 20 MB | 同上 |
| 当前 Gemini 模型 | gemini-3.8-flash（稳定版，2026 年 9 月）：文本/图片/视频/音频/PDF 输入，1,048,576 输入 token | <https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash> |
| OpenRouter Models API 提供 `input_modalities` | ✅ `architecture.input_modalities` ∈ {text,image,audio,video,file}；另有 `context_length`（2026-10-21 时共 468 个模型） | <https://openrouter.ai/api/v1/models> |
| Claude 图片限额 | 每次请求 600 张（200k 上下文模型为 100 张），8000×8000 px，超过 20 张时限制更严，token 为 ⌈w/28⌉·⌈h/28⌉，Claude 4.7+：2576 px / 4784 token | <https://platform.claude.com/docs/en/build-with-claude/vision> |
| OpenAI 图片限额 | 每次请求 1,500 张、512 MB | <https://developers.openai.com/api/docs/guides/images-vision> |
| Qwen 3.5–3.8 视觉（非 omni） | 视频 2 秒–2 小时，不理解音频；每次请求 256–2,048 张图片 | <https://www.alibabacloud.com/help/en/model-studio/vision> |

在 OpenRouter 上标为支持视频输入、但限额无法核实的模型（Kimi K3、GLM-5.3 Flash、GLM-5V、MiniMax M3、Gemma 4），
档案中标为 `unverified: true` 并采用保守限额。

## D-013 重新核实的宿主事实 → `routing/host-profiles.yaml`

| 宿主 | 初始设定 | 重新核实 | 可信度 |
|---|---|---|---|
| claude-code | 图片 ✅ 视频 ❌ 音频 ❌ | ✅ 不变。在 Claude Code（桌面版，2026-10-21）中**实测**：PNG 按图片读取；`.mp4` 和 `.mp3` 被当作二进制文件拒绝 | verified |
| codex-cli | 只支持图片 | ⚠️ rust-v0.163.0 还接受**本地音频**（≤ 50 MB）；仍不支持视频 | source-verified |
| opencode | 只支持图片（JPEG/PNG/GIF/WebP） | ✅ 文档（attachments.mdx，v1.19.3）：PDF/音频/视频会被丢弃；限额 20 MiB | docs-only |
| cursor | 图片，未核实 | 不变（闭源） | unverified |
| gemini-cli | 视频"？"（文档自相矛盾） | ⚠️ v0.65.0 源码：`detectFileType` → `video`，以 `inlineData` 发送（整个文件，含音频），`MAX_FILE_SIZE_MB = 20`；文档仍未提视频 | source-verified（待实测） |
| openclaw | 媒体理解预处理（类似 D5） | ✅ 默认图片 10 MB / 音频 20 MB / 视频 50 MB；⚠️ 配置为支持视频输入的回复模型现在可以通过 `video_url` 直接接收视频 | docs-only |
| qwen-code / bailian-cli | 通过工具委托 | ⚠️ 当模型配置声明了相应模态时，qwen-code 现在会**原生**附带视频/音频（内联 < 10 MB data URI；实验性的 Omni 上传 ≤ 1 GiB）。bailian-cli（`modelstudioai/cli`，npm 2.5.0，Apache-2.0）是给 Agent 用的工具，通过 DashScope 提供长视频理解 → 是 D5 委托的候选，而不是宿主 | source-verified |
| cline / roo / kilo | 未核实 | cline：`src/` 中没有处理视频的代码（代码搜索）；Roo Code **已于 2026-05-15 归档**；Kilo 7.x 基于 opencode | unverified |
| local-mistralrs | 视频是否支持取决于模型 | ⚠️ mistral.rs 用 FFmpeg 把视频解码为帧 → `frame-sampled`（D3） | docs-only |

Schema 扩展（记录在 `schemas/host-profiles.schema.json`）：`confidence` 新增 `source-verified`；`video_delivery` 新增
`preprocessed`（宿主自己把媒体转换为文本；按"不支持视频"路由）。

## D-014 D3 的"帧 + 音频"变体

当有效能力包括音频但不包括视频时（例如 codex-cli 搭配能听音频的模型），vidroute 仍走 `frames` 路线，但同时附上音轨，
转为适配限额的 16 kHz 单声道 MP3（思路来自 Qwen-MM-Plugins，未复制代码）。此时校对规则遵循 D1（"以模型听到的为准"）。

## D-015 输出与写入位置

不指定 `--out` 时，输出写到 `$VIDROUTE_HOME/out/<slug>/`，这样 vidroute 永远不会写到 `VIDROUTE_HOME` 或 `--out` 之外。
SKILL.md 让 Agent 把 `--out` 设在自己的工作区内，以便宿主能附带这些文件。

## D-016 CLI 约定细节

* 退出码 0/2/3/4/5 按最初设计；**1** 用于无效的用法/输入（原设计未定义；argparse 自己的退出码 2 会和"需要安装"冲突，
  所以 argparse 的错误会被转换成外壳并以 1 退出）。
* 警告是对象 `{code, message, hint?}`，而不是字符串。

## D-017 Agent Skills 规范

✅ <https://agentskills.io/specification>：`name` 1–64 个字符，只含小写字母/数字/连字符，不能以连字符开头或结尾，不能连续两个连字符，
**必须与所在目录名一致**；`description` 1–1024 个字符；可选 `license`、`compatibility`（≤ 500）、`metadata`（字符串映射）、
`allowed-tools`（实验性）；SKILL.md 保持在 500 行以内；用 `skills-ref validate` 校验（PyPI `skills-ref` 0.1.1，需要 Python ≥ 3.11）。

## D-018 相关项目及 vidroute 的区别

没有从下列任何项目中复制代码；借鉴的思路在此致谢。

| 项目 | 许可 | 做什么 | 借鉴了什么 / 区别在哪 |
|---|---|---|---|
| HUANGCHIHHUNGLeo/claude-real-video（★2.2k） | MIT | 感知场景、去重的帧 + 转写，支持 URL 或本地文件 | 与 D3 思路相同；vidroute 增加了能力路由、原生视频路线、安装流程和校对协议；不下载 URL |
| guimatheus92/mcp-video-analyzer（★96） | MIT | MCP 服务：从 URL/本地文件提取帧 + 转写 + OCR | vidroute：MCP 只是路由内核外的一层壳 |
| jordanrendric/claude-video-vision（★1.4k） | MIT | Claude Code 插件；先看结构，再像二分查找一样逐步深入 | "像二分查找一样钻取"的指引（ANALYSIS_GUIDE、`drill`） |
| Newuxtreme/watch-video-skill（★72） | MIT | 让 Claude "看"视频的技能 | 技能打包方式的参考 |
| buxuku/SmartSub（★5.8k） | MIT | 桌面字幕工具；多引擎 ASR、按引擎分开的模型目录、多镜像断点续传、GPU 检测 | 下载管理器设计、ASR 前先提取内嵌字幕、Windows GPU 检测的坑 |
| HKUDS/VideoAgent（★2.0k） | MIT | Agent 式视频理解/编辑框架 | 不在范围内：vidroute 不是 Agent 框架 |
| QwenLM/Qwen-MM-Plugins（★3.1k） | Apache-2.0 | 多模态插件；带回退的媒体交付 | 内联预算适配、帧 + 音频回退（D-014） |

**差异点**：按 *LLM × 宿主* 能力做两层路由（原生视频 / 原生视频 + 本地 ASR / 关键帧 + 转写 / 文本），明确且可解释的降级，
模型检测与一条命令安装，原片外传前征得同意，以及转写校对协议。

## D-019 文档语言：中文（2026-11-12 更新）

仓库文档（README、SKILL.md、references、本记录）以及 CLI/MCP 面向用户的输出都使用中文。最初的版本是英文 README
加一份中文镜像；为了只维护一份文档、并照顾主要用户群，维护者决定改为只保留中文。代码标识符、命令、参数、JSON 字段名和错误码
保持英文不变，以免破坏脚本和 Agent 的集成。

## D-020 托管方式

开发期间代码放在 GitHub 的**私有**仓库（`wblearner-mini/vidroute`），以便运行 Windows CI；维护者于 2026-11-04 将仓库公开。
版本标签、GitHub Release 和 PyPI 发布尚未进行。

## D-021 Python 支持范围

`requires-python >= 3.10`。Python 3.10 于 2026 年 10 月结束生命周期（PEP 619）；CI 会持续测试 3.10 到 v0.2。
worker 环境始终使用 Python 3.12（D-004）。

## D-022 OCR 模型尽量取自带哈希锁定的 RapidOCR wheel

`rapidocr` 3.10.0 的 wheel（27 MB）自带 `PP-OCRv6_det_small.onnx`、`PP-OCRv6_rec_small.onnx` 和 v2.0 方向分类器，
与清单中的条目逐字节相同（SHA-256 一致）。由于 wheel 本身在 `envs/ocr.lock.txt` 中按哈希固定，复制这些文件（并再次校验
SHA-256）与下载它们同样可信，所以 `ocr-ppocrv6-small` 把 `type: bundled` 来源列在第一位，ModelScope 第二。总是给 RapidOCR
明确的本地模型路径，确保它不会自行下载任何东西。

## D-023 uv 写的所有东西都留在 VIDROUTE_HOME

创建 worker 环境时，把 `UV_PYTHON_INSTALL_DIR`、`UV_CACHE_DIR` 设在 `$VIDROUTE_HOME` 内，并使用
`UV_PYTHON_INSTALL_REGISTRY=0` / `--no-registry`（不写 Windows 注册表）、`UV_NO_CONFIG=1`（忽略用户的 uv 配置）、
`--managed-python`（python-build-standalone 3.12，各系统一致）和 `--no-build`（只用 wheel，绝不编译）。由于文件是复制的
（`UV_LINK_MODE=copy`），安装成功后会删除 uv 缓存。

通用锁：`sherpa-onnx` 的 Linux/macOS wheel 依赖 `sherpa-onnx-core`，但 uv 的通用解析读取的元数据来自一个没有声明该依赖的
wheel；因此显式固定了 `sherpa-onnx-core==1.13.8`，让 `--require-hashes` 在每个系统上都覆盖到它。

## D-024 worker 协议

worker 是独立脚本，用 `<env python> -I` 运行（隔离模式，不加载用户 site，`sys.path` 中不含当前目录）。协议流是原始 stdout
文件描述符的副本，而 fd 1 被重定向到 stderr，这样会打印输出的原生库也无法破坏 JSON。stderr 写到 `$VIDROUTE_HOME/cache/logs`
（只在非空时保留），其末尾会附在 worker 错误里。ASR/VAD 和 OCR 在不同进程中运行（D-004）。

## D-025 安装确认的语义

`vidroute install` 总是先计算计划。没有 `--yes` 时，它返回计划并报错 `needs_confirmation`（退出码 3），不下载任何东西；
在交互式终端中则展示计划并询问。受限许可会返回 `license_not_accepted`（退出码 3），直到给出 `--accept-license <id>`。
明确请求了硬件无法运行的模型时退出码为 4。ffmpeg 从不静默安装：Windows 上带 `--yes` 时，计划会运行
`winget install --id Gyan.FFmpeg`（计划中会显示）；其他系统则打印命令。

## D-026 8 GB 边界的内存容差

`cpu-lite` 的定义是"内存 < 8 GB"。标称 8 GB 的机器实际可用 7.5–7.99 GB（固件/核显占用）；GitHub 的 windows-latest
runner 报告 7.99 GB。因此 vidroute 把 ≥ 7.5 GB 视为 8 GB 档；低于 7.5 GB 才是 `cpu-lite`。

## D-027 probe：有 OCR 时用检测结果计算文字密度

`text_density` 决定 R4（是否做 OCR）。始终会计算一个纯 Python 的分块启发式（对比度 + 边缘密度 + 双峰直方图），但它在合成
校准片段上有已知的失效情况：高对比度的随机纹理（`life`）被当成文字（0.875），复杂背景上的烧录字幕被漏掉（0.0）。装了 OCR
模型时，probe 会在同样的 8 帧上**只运行** PP-OCR 的**检测器**（不做识别，640 px 帧约 0.16 秒），并丢弃不可能是文字行的框
（高于画面 20 %、极细的碎片、覆盖整帧的色块）。在校准集上，它把幻灯片、代码、纯字幕、testsrc2（带时间码）、mandelbrot
和 life 全部分对了；probe 仍保持在约 3 秒。启发式仍作为回退，并如实报告（`pictures.method`）。

## D-028 分段数与单次请求的文件限额

原生路线把超过限额的视频切成最多 `max_segments`（8）段，相邻两段重叠 3 秒。模型的单次请求文件数限额（`max_files`，
例如 Qwen3-Omni-Flash 为 1）不限制分段数——各段可以在不同轮次中发送——而是报告给 Agent。

## D-029 附带音频与原片外传需要同样的同意

"帧 + 音频"变体（D-014）会把音轨交给 LLM 服务商。只有当 LLM ∩ 宿主都接受音频且视频中有语音时才会计划这一变体，
而且 `prepare` 只在带 `--accept-raw-video-handoff` 时附上音频；没有该参数时，它会悄悄回退为不带音频的帧（仍是有效的、
更私密的交付方式），而不是报错。

## D-030 关键帧：用分块差异切镜头，而非全局场景分数

R5 要求"场景切换 + 定时网格 + 感知哈希去重"。在合成校准集（带交叉淡化的幻灯片、代码、生命游戏、Mandelbrot 缩放）和一段
真实的 4 分钟 1080p 录屏上测量发现：

* ffmpeg 的 `scene` 分数是全局帧差；幻灯片新增一条要点，或录屏中打开一个菜单，都远低于常用的 0.3 阈值，因此这类变化被漏掉了。
* 64 位 dHash 在平坦画面（空白幻灯片、暗屏）上由噪声决定：两帧完全相同的空白画面可能相差 > 20 位，所以去重恰恰在最需要的地方失效
  （A–B–A 式的幻灯片）。

因此 vidroute 只解码一次，得到 64 px 灰度缩略图（视频自身的关键帧足够密时就用它们，否则解码一个最多 4 fps、最多
`max_candidates` 帧的网格）；只要任何一个 4×4 分块与上一个候选帧或本镜头的第一帧相差超过 `change_threshold`（10/255），
就开始一个新镜头；并用感知签名（每个分块的均值）合并重复画面——签名的最大差异可证明是分块差异的下界，是一个不会漏掉真正重复帧的
廉价预筛选。预算（`min(max_frames, max_images)`，短视频更少）优先保证覆盖：先取 N 个等长时间段中各自停留最久的镜头，再取剩下
停留最久的镜头，任意两次选取的间隔不小于 `min_interval_seconds`。在幻灯片片段上的结果：恰好四张幻灯片，没有交叉淡化帧；
在 4 分钟录屏上：47 个不同镜头，23 次回到之前画面的情况被合并。

## D-031 `prepare` 的输出约定（handoff.json v1）

`handoff.json`（schema `vidroute/handoff/1`，`schemas/handoff.schema.json`）在最初设计的基础上增加了 `on_demand`（Agent
可以打开的单张关键帧）、`text_context[].attach` 和 `est_tokens`、`transcript_role`、`uploads_raw_audio`，以及一个
`drill` 示例。所有路径都相对于输出目录，所有时间都是全局时间。标记为需要附带的内容取决于路线：

* frames（D3）：拼图 + `transcript.md` + `ocr.md`；`timeline.md` 和 `contact-sheets/index.md` 只用于导航（内容重复）；
* text（D4）：只附 `timeline.md`——按时间合并的转写和屏幕文字——`transcript.md` / `ocr.md` 按需查看。

`ANALYSIS_GUIDE.md` 由 `references/analysis-guide.template.md` 加上路线区块渲染而成，贡献者无需改代码就能修改指引。

## D-032 `prepare` 复用输出目录中的计划

`vidroute plan --out DIR` 写出 `DIR/<slug>/plan.json`；之后不带 `--llm/--host` 运行 `vidroute prepare <video> --out DIR`
会使用这份计划（并给出 `plan_reused` 警告），这样 Agent 的"plan → 询问用户 → prepare"流程不需要额外参数，原生路线退出码 3
的提示也只是加上 `--accept-raw-video-handoff` 重新运行。`vidroute run` 总是重新做计划。

## D-033 对更清晰的副本做 OCR；丢弃单字符行

当原视频大于 `frame_max_side_px`（1280）时，会额外按 `ocr_frame_max_side_px`（1920）截取关键帧，仅用于 OCR；附带的帧
仍是 1280。RapidOCR 的默认检测器不会缩小 1080p 帧，所以小号界面文字得以保留。只有一个字符的行会被丢弃：在录屏中，它们几乎
总是被识别成文字的图标（"×"、"□"、"Q"）。

## D-034 原生路线：检查生成的文件，而不只是计划

计划中的码率计算（D-028）决定直接复制 / 去音轨重封装 / 转码以及分段边界，但编码器在短片段或画面复杂的片段上会超出预期。
因此 `prepare` 会把每个生成的文件与 `max_file_mb` 对比（留 97 % 余量：容器开销、MB 与 MiB 的差异）。超过大小的编码会按比例
降低码率重做，最多三遍；如果某一部分仍然放不下，`prepare` 会强制改用 `frames` 路线重新计划，在 `degraded_from` 中记录
`{"route": "native-…", "why": "prepared files exceed the limit …"}`，并删除 `native/`。编码器：`libx264`（veryfast，
带 maxrate/bufsize 的 ABR，没有大小限制时用 CRF），否则 `libopenh264`，否则 MPEG-4 Part 2；从不使用硬件编码器。
`native-av` 的音频为单声道 AAC；`native-v` 从不带音轨。`segments.json`（`vidroute/segments/1`）给出每一部分的
`offset_sec`，以便把分段内的时间换算回全局时间。`native-av` 只在计划需要逐字参考时（长视频、分段、纪要/转写任务）才运行 ASR；
内嵌字幕因为不花成本，仍会提取。

## D-035 drill 作用于输出目录

`vidroute drill <out-dir> --range START-END` 读取该目录的 `plan.json`（视频路径、路线、LLM 图片限额），写出
`drill/<start>-<end>[-Nfps]/`，其中包含更密的关键帧（按变化选取，默认预算 24；或固定 `--fps`，最高 10 fps、120 帧，
两者都受 `max_images` 限制）、一张带标签的拼图，以及按需生成的新转写（`--asr`，VAD 限定在该区间）和 OCR（`--ocr`）。
`drill.json` 中的路径相对于主输出目录，时间都是全局时间，所以 Agent 永远不需要换算时间。

## D-036 MCP 服务：握手协议版本，运行时不依赖 SDK

`vidroute-mcp` 在 stdio 上使用按行分隔的 JSON-RPC 2.0，支持协议版本 2024-11-05、2025-03-26、2025-06-18 和 2025-11-25 的
`initialize` 握手（客户端请求的版本受支持时原样返回，否则回复 2025-11-25）。官方 SDK（`mcp` 2.3.0，PyPI 2026-10-02）还支持
2026-07-28，该版本用 `server/discover` 和每次请求的 `_meta` 取代了握手；vidroute 尚未实现它（`server/discover` 返回 -32601，
之后客户端会改用握手）。工具结果同时以 `structuredContent` 和 JSON 文本携带外壳，`isError` 与 `ok` 对应；进度通知使用严格递增的
计数；按取消规则，被取消的请求不会得到响应。`read_outputs` 把输出文件作为图片/音频/文本内容返回（音频从 2025-03-26 起才支持），
并拒绝输出目录之外的路径。用 SDK 自带的 stdio 客户端测试一致性（仅测试依赖）。

## D-037 Agent Skill 与 Claude Code 插件的布局

技能位于 `skills/vidroute/`（Agent Skills 规范：`name` = 目录名，描述中写明使用场景，< 500 行，引用只有一层深），并通过
`skills-ref validate`（`skills-ref` 0.1.1，Python ≥ 3.11，仅测试依赖）。它的启动器 `scripts/vidroute.py` 依次尝试：所在的源码树
（编排器是纯标准库，所以插件检出后无需安装）、PATH 上的 `vidroute` 可执行文件、已安装的包；都没有则返回 `needs_install` 外壳。
仓库根目录同时也是一个 Claude Code 插件（`.claude-plugin/plugin.json`）和只含一个插件的 marketplace
（`.claude-plugin/marketplace.json`，`claude plugin validate .` 通过）。插件不带 `.mcp.json`：没有哪个 Python 命令名在
Windows、macOS 和 Linux 上都通用，所以 README 改为说明 `claude mcp add vidroute -- vidroute-mcp`。wheel 内置技能；
`vidroute skill path` 打印其目录，便于复制到任何宿主的技能目录。

## D-038 数据 schema 版本与供应链检查

每个数据文件和输出都声明 `schema: vidroute/<kind>/<major>`（规则、清单、档案、计划、handoff、分段、drill、输出清单）。
加载器遇到未知的主版本号会报错，并指明期望的版本；第一个 `/2` 出现时会同时加入迁移函数。供应链：`tools/license_report.py`
从 PyPI 元数据读取每个固定包的许可（PEP 639 表达式，否则用分类器），不在宽松与弱 copyleft 许可白名单内就失败（NVIDIA CUDA
库是为可选 GPU 路径审核过的例外）；`tools/vuln_check.py` 在 osv.dev 上查询所有固定版本。两者都是只用标准库的脚本，由
`supply-chain` 工作流运行（锁文件变化时及每周一次）。发布版本由 `release.yml` 在版本标签上构建，并附 `SHA256SUMS.txt`；
PyPI 上传使用 trusted publishing，只在手动触发时运行。

## D-039 `vidroute setup`：一份计划，一次确认（2026-10-23）

维护者的需求：减少手动步骤，尤其是接入 Agent 宿主。`vidroute setup` 把安装计划（D-025，不变）、接入本机的 Agent 宿主
（D-040）和端到端自检（D-046）合并成一份计划、一次确认：以退出码 3 返回 `data.plan` 和 `--yes` 提示，在终端中则是交互式的
y/N 提问，确认前不写入任何东西。宿主接入会写到 `VIDROUTE_HOME` 之外，这只有在用户确认后才允许——所以它总是计划的一部分，
从不隐式进行。默认值：能力为 asr + vad + ocr + scene（README 中的 CPU 基线），宿主为 `auto`（检测到的所有宿主），技能开，
MCP 关。已经装好的机器只做自检。退出码：ffmpeg 仍然缺失时为 2（Linux/macOS 上这是用户包管理器的事；带 `--no-install`
时只是警告，因为它本来就有意跳过安装），某个安装步骤、接入或自检失败时为 5；重新运行会继续。`setup --remove` 只移除
state.json 和目录标记表明由 vidroute 添加的内容；模型保留。

## D-040 Agent 宿主接入：技能目录、标记、MCP 需主动开启（2026-10-23）

用户级技能目录，来自 2026-10-23 各宿主的文档：

| 宿主 | 读取目录 | 来源 |
|---|---|---|
| Claude Code | `~/.claude/skills`（`$CLAUDE_CONFIG_DIR/skills`） | code.claude.com/docs/en/skills |
| Codex CLI | `~/.agents/skills` | learn.chatgpt.com/docs/build-skills |
| Gemini CLI | `~/.gemini/skills`、`~/.agents/skills` | geminicli.com/docs/cli/skills |
| Qwen Code | `~/.qwen/skills` | qwenlm.github.io/qwen-code-docs/en/users/features/skills |
| opencode | `~/.config/opencode/skills`、`~/.claude/skills`、`~/.agents/skills` | opencode.ai/docs/skills |
| Cursor | `~/.agents/skills`、`~/.cursor/skills`、`~/.claude/skills`、`~/.codex/skills` | cursor.com/docs/skills |
| Cline | `~/.cline/skills` | docs.cline.bot/customization/skills |
| Roo Code | `~/.roo/skills`、`~/.agents/skills` | roocodeinc.github.io/Roo-Code/features/skills |
| Kilo Code | `~/.kilo/skills`、`~/.agents/skills` | kilo.ai/docs/customize/skills |
| OpenClaw | `~/.agents/skills`（及其状态目录） | docs.openclaw.ai/tools/skills |

* **副本最少。** 只读一个目录的宿主先确定该目录；其他宿主加入已选中的目录，否则用 `~/.agents/skills`。Claude Code + opencode
  → 一份；Claude Code + Codex → 两份（此时 opencode/Cursor 可能把技能列出两次；见 KNOWN_ISSUES.md）。
* **检测**只做观察：PATH 上的命令、配置目录，或（Cline、Roo、Kilo）VS Code 系编辑器的扩展目录。
* **归属。** 每份副本带一个 `.vidroute-skill.cfg`（`installed_by=vidroute`、版本、内容摘要、解释器）。只有这样的目录才会被
  更新或移除；手工创建的 `vidroute` 目录会被跳过，或在 `--force` 时改名为 `vidroute.bak-<time>`——从不删除。`doctor` 报告每个
  宿主的副本，并在副本过时或其解释器不存在时给出警告。
* **记录解释器。** 标记中记录运行 vidroute 的 Python（未安装时还记录源码树），这样即使宿主是在 PATH 变化前启动的，副本也能用。
* **Claude Code 插件。** 当 `installed_plugins.json` 中列有 `vidroute@…` 时，插件已经带来了技能，跳过 `~/.claude/skills`。
  插件仍不带 `bin/` 目录：claude.ai 和 Cowork 会拒绝带 `bin/` 的插件（code.claude.com/docs/en/plugins-reference），
  而且启动器本来就能通过技能目录找到。
* **MCP 需主动开启**（`--mcp`）：技能已覆盖上面所有宿主，而 MCP 工具定义每一轮都要占用上下文。Claude Code 和 Codex 通过它们
  自己的命令修改（`claude mcp add --scope user`、`codex mcp add`；`~/.claude.json` 是宿主会重写的大状态文件，Codex 用 TOML）。
  Gemini CLI、Qwen Code、Cursor 和 opencode 在 JSON 中保存服务（`mcpServers` / opencode 的 `mcp`，`type: local`）；vidroute
  只改自己的键，保留文件缩进并做一次性的 `.vidroute-backup`，不碰非严格 JSON（带注释）的文件，改为打印配置片段。只有
  state.json 记录了由 vidroute 写入的条目才算 vidroute 自己的。服务命令使用绝对路径（`<python> -m vidroute.mcp_server`）。
  Cline、Roo、Kilo 和 OpenClaw 把 MCP 设置保存在编辑器内部存储中：只装技能。

## D-041 不需要 PATH 上有 `python` 的启动器（2026-10-23）

`skills/vidroute/scripts/` 有三个入口：`vidroute`（POSIX sh，只用 shell 内置命令，也适用于 Git Bash）、`vidroute.cmd`
（cmd/PowerShell，CRLF）和 `vidroute.py`。shell 启动器依次尝试：`setup` 记录的解释器，然后是 `python3`/`python`/`py -3`
——每个都会试运行，从而跳过 Microsoft Store 的占位 `python.exe`——然后是 uv，包括安装器在 PATH 刷新前放置的位置
（`~/.local/bin`、`WinGet\Links`、`~/.cargo/bin`、`VIDROUTE_HOME`），并以 `UV_PYTHON_INSTALL_DIR=$VIDROUTE_HOME/runtimes/python`
运行 `uv run --no-project --python 3.12 vidroute.py`，使下载的 Python 与模型 worker 用的是同一个。都找不到时，它们会打印一个
`needs_install` 外壳，提示中给出安装 uv 的命令（winget / PowerShell 安装器 / `curl … | sh`）。`vidroute.py` 优先使用自己所在
的源码树，然后是记录的安装位置，然后是可导入的包，最后是 PATH。在首次发布到 PyPI 之前，它的安装提示使用 GitHub 源码压缩包。

## D-042 软件包和 Python 运行时的镜像（2026-10-23）

worker 环境用 `--require-hashes` 安装，而 uv 会把每次托管 Python 的下载与其元数据中的 SHA-256 核对（`Error::HashMismatch`，
uv 0.13.4 的 `crates/uv-python-managed/src/downloads.rs`；uv 0.13.4 从 `releases.astral.sh` 下载，`UV_PYTHON_INSTALL_MIRROR`
会替换这个前缀——已用 `uv python list --show-urls` 核实）。因此镜像无法改变安装的内容。`--mirror auto` 的规则：保留官方来源；
官方来源无响应时，或 huggingface.co 不通而 hf-mirror.com 或 ModelScope 有响应时（这是中国大陆网络的特征，此时 GitHub 和 PyPI
下载也很慢），改用镜像。镜像：PyPI——先 `mirrors.aliyun.com/pypi/simple/`，再 `pypi.tuna.tsinghua.edu.cn/simple/`；
Python 构建——`registry.npmmirror.com/-/binary/python-build-standalone/`（列出到 20261021 的发布，2026-10-23 核实）；
模型——先用自带的，然后 ModelScope，然后 hf-mirror.com，最后其他来源。`--mirror cn` 强制使用这套顺序。用户的
`UV_DEFAULT_INDEX` / `UV_INDEX_URL` / `PIP_INDEX_URL` / `UV_PYTHON_INSTALL_MIRROR` 以及配置项 `pypi_index` /
`python_mirror`（`official` 关闭镜像）始终优先。计划中会显示所做的选择及原因。

## D-043 Windows 的 FFmpeg 便携包（细化 D-025，2026-10-23）

有 winget 时仍优先使用 winget。没有 winget（LTSC、Server 版、受管控的电脑）、winget 失败，或设置了
`vidroute config set ffmpeg_source portable` 时，计划会提供一个固定版本的构建，放在 `$VIDROUTE_HOME/runtimes/ffmpeg`
——不加入 PATH；`util.ffmpeg` 本来就会搜索该目录。构建：来自 `github.com/GyanD/codexffmpeg` 的 gyan.dev "essentials"
FFmpeg 9.0.2（与 BtbN 会被清理的自动构建不同，带版本号的标签会保留），114,768,076 字节，SHA-256 `60f46726…a2047ba`
= GitHub 发布附件 digest = 本地下载；`ffmpeg.exe`、`ffprobe.exe` 和 `LICENSE` 也各自固定了哈希（解压后 210 MB，跳过 `ffplay`）。
这是带 libx264、libmp3lame 和 freetype 的静态 GPLv3 构建（见压缩包内的 README.txt）——与 CI 用 `choco install ffmpeg
--version 9.0.2` 安装的是同一系列。GPL 义务适用于再分发；vidroute 只是替用户下载文件（在计划确认后），并作为独立程序运行它，
所以清单将其标为不受限并附上说明。x64 构建也用于 Windows on Arm（仿真运行）。Windows 上缺少 ffmpeg 时的提示现在是
`vidroute install --capability scene`，它恰好会计划这一项。

## D-044 Windows on Arm：缺少 wheel 时使用 x64 Python（2026-10-23）

2026-10-23 的 PyPI：`opencv-python-headless` 5.0.0.93 和 `pyclipper` 1.4.0（OCR 环境）以及 `ctranslate2` 4.8.3
（faster-whisper 环境）没有 `win_arm64` wheel；sherpa 环境中的所有包都有。worker 环境以 `--no-build` 构建，所以清单给
`ocr` 和 `whisper` 标上 `windows_arm64_arch: x86_64`：在 Windows on Arm 上，vidroute 向 uv 请求
`cpython-3.12-windows-x86_64-none`（uv 的请求语法，已在 uv 0.13.4 上用 `uv python list --all-platforms` 核实），由 Windows
仿真运行；sherpa 保持原生。系统架构取自 `IsWow64Process2`，因为仿真运行的 x64 Python 会报告 `AMD64`。尚未在真实 Arm 硬件上
验证（GitHub 不为私有仓库提供 Arm runner）。

## D-045 一行安装脚本（2026-10-23）

`install.ps1`（Windows PowerShell 5.1+）和 `install.sh`（POSIX sh）不需要 Python：它们找到 uv，或把 uv 0.13.4
（= `UV_VERSION`）放到 `$VIDROUTE_HOME/runtimes/uv`——来源依次是 GitHub 发布（固定附件 digest）、PyPI 的 wheel、阿里云 PyPI
镜像上的同一个 wheel（`packages/…` 路径相同，2026-10-23 核实；wheel 哈希 = `envs/uv.lock.txt`，由测试断言）。然后运行
`uv tool install --force --reinstall <source>`（重新运行即升级；没有 Python 时 uv 会提供一个）、`uv tool update-shell`，
并用绝对路径运行 `vidroute setup`（当前 shell 还是旧的 PATH）。检测到中国大陆网络（D-042）时，还会调整 uv 的下载顺序并传入
`--mirror cn`。在 `irm | iex` 下脚本从不调用 `exit`（否则会关掉用户的窗口）；`curl | sh` 从 `/dev/tty` 读取 setup 的回答。

## D-046 自检（2026-10-23）

`setup` 最后会用 ffmpeg 生成一段 6 秒的样片：一张写着 "VIDROUTE SELF TEST" 的白色幻灯片（有 `drawtext` 时），配上
SenseVoice 自带的中文示例录音（没有时用一个音调），通过 `prepare --route frames` 输出到 `$VIDROUTE_HOME/cache/selftest`。
检查项：输出已写出、有一张拼图、语音被转写（装了 ASR 时）、幻灯片文字被读回（装了 OCR 时）。在开发机上使用真实模型：6.0 秒通过
（"开放时间早上9点至下午5点。"、"VIDROUTE SELF TEST"）。

## D-047 全新机器上的安装脚本（2026-11-04）

在一个干净的 Ubuntu 容器中审查时发现：默认的 `git+https` 来源需要 git，而阿里云的 uv wheel 在没有 unzip 或 Python 时无法解开，
于是转而从 GitHub 下载，又因为没有超时而卡住。现在默认来源改为 GitHub 源码压缩包。`install.sh` 用 `od` 读取 wheel 的 zip 头，
最后的手段是用 `gzip` 解压；先校验 wheel 的 SHA-256，使用前先运行一次解出的二进制文件。传输速度低于 10 KB/s 持续 20 秒即视为卡住。

## D-048 在计划之前先检查 ffmpeg（2026-11-04）

在 Linux 和 macOS 上 vidroute 无法安装 ffmpeg，但 `setup` 曾先下载约 850 MB 后才失败。现在 `setup` 会在询问或下载前就以退出码 2
退出。`--no-install` 仍只给出警告。

## D-049 说话人标签（2026-11-04）

使用 sherpa-onnx 离线说话人区分（pyannote segmentation-3.0 + 3D-Speaker CAM++，已在 `sherpa` 环境中，34 MB；因为纪要需要它，
所以放在默认路径上）。在 `--task minutes` 或 `--diarize on` 时运行；`auto` 会跳过其他任务，因为在 16 线程上它大约要花语音时长的
0.08 倍。说话人分段在 ASR 之前计算，并在说话人切换处切分音频块，因此每个片段只有一个说话人。只检测到一个声音时不加标签。失败只会
给出警告。标签只是声音分组，绝不代表身份，指南中也这样说明。用不同真人的录音检查过（6 次发言：5 次正确，一次 3 秒的发言被单独
分成了一个标签）；尚未在真实会议上检查。

## D-050 报告关键帧预算漏掉的内容（2026-11-04）

60 帧的预算保持不变：它保护 token 和密集图片的限额。在屏幕上停留至少 `frames.omitted_min_seconds`（5 秒）却没有分到帧的镜头，
会写进 `omitted-shots.md`，并给出带 `drill` 命令的警告；这样快速剪辑不会增加噪声，120 张幻灯片的演示也不会在 60 帧时看起来就是完整的。
OCR 框过滤的高度上限从画面的 20 % 提高到 40 %：大号标题行曾被丢掉，导致内容稀疏的幻灯片跳过了 OCR。

## D-051 对同一输出目录的运行互相等待（2026-11-04）

以前对同一视频、同一目录同时运行三次，会有两次崩溃（一次运行在重建另一次正在读取的内容，导致 `FileNotFoundError`）。现在
`.work/lock` 上的内核锁（`flock`、`msvcrt.locking`）让每个输出目录上的 `prepare`、`run` 和 `drill` 串行执行。等待胜过失败：
第二次运行会拿到缓存的结果，而且 Agent 本来就会自己重试。任何方式退出（包括 `kill -9`）内核都会释放锁，所以不需要处理残留锁。
等待一小时后放弃。不支持锁的文件系统和以前一样不受保护地运行。

## D-052 提示要么是一条命令，要么为空（2026-11-04）

以前通用失败会建议 `vidroute doctor --refresh`，无法读取的文件会给出一条原始 `ffprobe` 命令，两者都没用。现在机器问题
（磁盘已满、没有权限）有各自的错误码。只能由用户自己解决的问题（文件损坏）`error.hint` 为空，由 `message` 说明该怎么做。
意外错误会请用户附上 `-v` 的输出提交报告。

## D-053 资源限额取自进程，而不是宿主机（2026-11-04）

`os.cpu_count()` 和 `/proc/meminfo` 描述的是宿主机，所以一个 2 GB 的容器看起来有 19.8 GB，被判为 `cpu-full`。现在内存使用最严格的
cgroup 限额（v1 和 v2，从本进程的 cgroup 逐级向上查找）；CPU 使用亲和性掩码和配额。档位、默认 OCR 模型和线程数随之调整。
没有限制的机器保持以前的线程数。在 2 CPU、2 GB 的容器中实测：`cpu-lite`，没有内存溢出，带说话人标签的 20 分钟视频用时 194 秒。

## D-054 续跑粒度（2026-11-04）

ASR 本来就按音频块续跑，但每 300 秒语音才保存一次。现在每 60 秒保存一次。说话人分段保存在 `.work/turns.json`，因为它们的开销与
ASR 相当，而且音频块缓存的键就是按这些分段切出的块。
