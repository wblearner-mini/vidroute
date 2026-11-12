# 安装 vidroute——给 AI Agent 的说明

你是一个编程 Agent（Claude Code、Codex CLI、Gemini CLI、Qwen Code、opencode、Cursor……），你的用户请你安装
vidroute，或把它接入他们的工具。请按顺序执行下面的步骤。每条 vidroute 命令都加 `--json`，并读取 **stdout**
（一个 JSON 外壳；日志在 stderr）。在用户同意你展示的计划之前，绝不能加 `--yes`。

## 1. vidroute 装好了吗？

运行 `vidroute version --json`。能运行就直接跳到第 3 步。

## 2. 安装（只需要 uv）

vidroute 唯一的前提是 [uv](https://docs.astral.sh/uv/)；uv 自带 Python。

1. 运行 `uv --version`。如果没有 uv，先征求用户同意，然后安装：
   - Windows：`winget install -e --id astral-sh.uv`
     （没有 winget 时：`powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`）
   - macOS：`brew install uv` · Linux 与 macOS：`curl -LsSf https://astral.sh/uv/install.sh | sh`

   你的 shell 还是旧的 PATH，所以之后请用完整路径调用 uv：Windows 上是
   `%USERPROFILE%\.local\bin\uv.exe` 或 `%LOCALAPPDATA%\Microsoft\WinGet\Links\uv.exe`，其他系统是
   `~/.local/bin/uv`。
2. 从 [v0.1.0 Release](https://github.com/wblearner-mini/vidroute/releases/tag/v0.1.0) 安装稳定版。
   运行 `uv tool install https://github.com/wblearner-mini/vidroute/releases/download/v0.1.0/vidroute-0.1.0-py3-none-any.whl`
   （不需要 git；在中国大陆请为这条命令设置
   `UV_DEFAULT_INDEX=https://mirrors.aliyun.com/pypi/simple/`）。
3. vidroute 现在位于 `~/.local/bin`（Windows：`%USERPROFILE%\.local\bin\vidroute.exe`）。本次会话剩下的步骤都用这个完整路径。

没有 Agent 的用户可以改用一个脚本完成第 2–4 步：仓库里的 `install.ps1`（Windows）或 `install.sh`（Linux、macOS），见 README。

## 3. 生成安装计划，只问一次

运行 `vidroute setup --json`。常用选项：

- `--host <id>`（可重复）只接入这些宿主；默认接入本机找到的所有宿主。
  可用 id：`claude-code`、`codex-cli`、`gemini-cli`、`qwen-code`、`opencode`、`cursor`、`cline`、`roo`、
  `kilo`、`openclaw`。
- `--mcp` 同时注册 MCP 服务（对上面所有宿主来说，只装技能就够了）。
- `--mirror cn` 使用中国大陆镜像（huggingface.co 不通时会自动选择）。

这一步不会下载任何东西。如果缺少 ffmpeg 而且这里无法代为安装（Linux、macOS），它会以 **2** 退出并报告
`ffmpeg_missing`：把 `error.hint` 告诉用户，装好后再执行这一步。否则它会以 **3** 退出。`data.plan` 包含：

- `install.items`：ffmpeg（缺少时）、uv、Python 运行时、worker 环境和模型，附大小、来源和许可；
  `install.total_download_mb` 是总下载量（首次约 0.85 GB）。
- `integration.items`：将要创建或更新的技能目录和 MCP 配置项。它们位于各宿主自己的配置目录，在 vidroute 的数据目录之外。

用几行话向用户总结这份计划，然后**询问**。只有得到同意后，才运行 `error.hint` 中的命令（即加上 `--yes` 的同一条命令）。

## 4. 检查结果

- 退出码 0：`data.selftest.status` 为 `passed`（一段生成的样片走完了转写、OCR 和关键帧）。把 `data.next_steps`
  告诉用户：通常是让每个宿主新开一个会话，以加载技能。
- 退出码 5：报告 `error.message`；再次运行同一条命令会从中断处继续。

任何时候都可以用 `vidroute doctor --json` 查看状态：`data.capabilities`（本机能运行什么）和
`data.integrations.hosts`（哪些宿主能看到技能，以及是否是最新版本）。

## 撤销

`vidroute setup --remove --json` 会以 3 退出，并列出它将从各宿主中移除的内容（只包括 vidroute 添加的部分）。
先询问用户，再加 `--yes` 运行。模型仍会保留；用 `vidroute models remove` 删除模型。

## 规则

- 让 vidroute 去修改宿主配置，不要自己编辑那些文件。它会为改动的每个 JSON 文件保留备份，也从不碰不是它创建的配置项。
- 不要去调试 vidroute 的内部实现。某一步失败时，把外壳里的 `error.message` 和 `error.hint` 报告给用户。
