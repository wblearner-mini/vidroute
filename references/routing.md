# vidroute 如何为视频选路

路由是由 `routing/rules.yaml` 驱动的确定性代码；Agent 和 LLM 都不参与。`vidroute plan --json` 会列出每个决定及其原因。

## 第一层——LLM 拿到什么

1. **有效能力** `M = LLM ∩ 宿主`：LLM 档案（`routing/llm-profiles.yaml`、用户覆盖、可选的 OpenRouter 缓存、命令行参数）
   说明模型能接收什么；宿主档案（`routing/host-profiles.yaml`）说明 Agent 能附带什么。模型能接收但宿主挡住的内容，会被报告为
   *受宿主限制*（并给出 D5 建议）。
2. **候选顺序**：`M` 含视频时先考虑 `native-av`（带音频的视频）或 `native-v`（不带音频的视频），含图片时考虑 `frames`，最后是 `text`。
3. 原生路线的**准入检查**，依次为：`--prefer privacy` 或配置 `allow_raw_video_handoff=false` → 跳过；文件在转码
   （720/540/480/360 p，≤ 10 fps，单声道 AAC，从不放大）或切成最多 `max_segments`（8）段、相邻重叠 `segment_overlap_seconds`
   （3）秒之后，必须满足单次请求的限额；估算的输入 token 必须在预算内（上下文窗口的 `budget_fraction` 0.5，或 `budget_tokens`）。
   超出预算时：`--prefer cost` 会降级，`quality`/`privacy` 会询问（`needs_confirmation`）。
4. **降级**按 `native-av → frames → text` 和 `native-v → frames → text` 进行；每条被跳过的路线连同原因都记录在 `degraded_from` 中。
   `prepare` 还可能再降级一次：转码后的某部分经过三遍仍超出限额时，改用 `frames` 重新计划。
5. **转写的角色**：模型能听到音频时（`native-av`，或附带了音轨的 `frames`）为 `reference`，否则为 `sole_source`。对于
   `native-av`，视频长于 `native_only_max_minutes`（10）、被分段，或任务是 `minutes`/`transcript` 时会附带转写。

## 第二层——本地运行什么

| 规则 | 行为 | 配置项（`local:` / `frames:`） |
|---|---|---|
| R1 | 内嵌文本字幕覆盖了 ≥ 70 % 的检测到的语音时，直接用作转写 | `embedded_subtitle_min_coverage` |
| R2 | 语音少于时长的 5 % 时不做 ASR（完整 VAD 会确认抽样 probe 的结果） | `speech_ratio_skip_asr` |
| R3 | 按语言（`zh`/`yue` → `zh` 链，否则 `multi`）和档位选择 ASR 引擎；在前 30 秒语音上检测语言；失败时沿链条往下换 | `asr_chains`、`zh_languages`、`language_probe_seconds` |
| R4 | 文字密度 ≥ 0.3（或 `--ocr on`）时，只对关键帧做 OCR | `ocr_text_density_threshold`、`ocr_chain` |
| R5 | 关键帧：按画面变化切镜头，合并重复画面，预算 `min(max_frames, max_images)` 优先保证覆盖；先给拼图，单帧按需查看 | `max_frames`、`base_interval_seconds`、`change_threshold`、`min_interval_seconds`、`contact_sheet` |
| R6 | 长于 30 分钟：候选更稀疏，关键帧足够密时只解码关键帧，ASR 分批可续跑 | `long_video_minutes`、`max_candidates` |
| R8 | vidroute 从不调用云服务 | — |

档位来自 `vidroute doctor`：`cpu-lite`（内存 < 7.5 GB，没有 AVX2 或无法判断）、`cpu-full`、`gpu-cuda`（NVIDIA 显存 ≥ 4 GB
**且**通过 CUDA 冒烟测试）、`gpu-vulkan`（保留）。

## 估算

`rules.yaml` 中的 `estimates:` 只影响 `plan.json` 中的 `est_seconds` 和 `est_input_tokens`：转写约为每秒语音 3.5 token，OCR
约为每帧 120 token，各档位的 ASR 实时率、每帧 OCR 秒数、转码实时率。它们从不改变结果。

## 修改规则

在检出的仓库中编辑 `routing/rules.yaml`。每个用户还有少量设置保存在 `$VIDROUTE_HOME/config.json` 中（`vidroute config get`
会列出），例如 `vidroute config set budget_fraction 0.3`、`budget_tokens`、`allow_raw_video_handoff`、`default_llm`、
`default_host`。修改档案需要官方来源 URL 和核实日期；见 `docs/PROFILE_CONTRIBUTING.md`。
