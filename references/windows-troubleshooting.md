# Windows 故障排查

Windows 10/11 上已知的坑，以及 vidroute 的应对办法。每一条都是在开发中或 CI（`windows-latest`）里实际遇到过的。

## ffmpeg

* **没有安装。** `vidroute setup`（或 `vidroute install --capability scene`）会把 ffmpeg 放进计划：有 winget 时用
  `winget install --id Gyan.FFmpeg`；没有 winget、winget 失败，或设置了 `vidroute config set ffmpeg_source portable` 时，
  把固定版本的 gyan.dev 9.0.2 essentials 构建（下载 110 MB，校验 SHA-256）放到 `%USERPROFILE%\.vidroute\runtimes\ffmpeg`。
  你确认之前什么都不会运行。两种方式都不需要新开终端 vidroute 就能找到（会搜索 `WinGet\Links` 和它自己的运行时目录）；
  `vidroute config set ffmpeg_path C:\path\to\ffmpeg.exe` 可以指定任意其他构建。
* **不同构建的编码器不同。** 原生路线需要 H.264 编码器。优先用 `libx264`；没有时 vidroute 退回到 `libopenh264`，再退回到
  MPEG-4 Part 2。硬件编码器（`h264_nvenc`、`h264_qsv`、`h264_amf`、`h264_mf`）会被检测到但从不使用，这样输出的大小和质量
  才可预期。音频附件用 `libmp3lame`，否则用 `.m4a` 中的 AAC。
* **`drawtext` 与字体。** 拼图标签使用 `C:\Windows\Fonts\arial.ttf`（或 `segoeui.ttf`）。在滤镜中盘符后的冒号必须转义
  （`C\:/Windows/Fonts/arial.ttf`）；vidroute 会处理。没有 `drawtext` 的构建生成不带标签的小图，另附 `contact-sheets/index.md`。
* **"静音"音频中的短促声音。** Windows 版 AAC 编码器可能在静音音轨开头留下约 70 毫秒的瞬态。能量 VAD 会忽略短于 0.25 秒的声音，
  所以不会被当作语音。

## 路径与文本

* **含空格和非 ASCII 字符的路径**（`D:\会议 录屏\周会.mp4`）可以正常使用：每个子进程都使用参数列表（从不 `shell=True`），输出目录
  的 slug 也保留中日韩字符。测试覆盖了带空格的中文路径。
* **控制台代码页。** CLI 会把 stdout/stderr 切换为 UTF-8；Python worker 以 `PYTHONUTF8=1` 运行。如果终端仍显示乱码，请使用
  Windows Terminal 或 `chcp 65001`。
* **换行符。** 仓库强制使用 LF（`.gitattributes`），`.ps1/.bat/.cmd` 除外；生成的 Markdown 使用 `\n`。
* **长路径。** 输出目录名很短（`<文件名，最多 48 字符>-<哈希>`）；如果很深的 `--out` 仍超过 260 个字符，请启用长路径
  （`LongPathsEnabled`）或选一个更短的 `--out`。

## Python 环境

* **完全没有 Python。** `install.ps1` 只需要 PowerShell：它获取一个固定版本的 uv（校验 SHA-256），uv 自带 Python。在 Agent 宿主中，
  技能的 `scripts\vidroute.cmd` 依次尝试 `py -3`、`python`、uv（也会查找 `%USERPROFILE%\.local\bin` 和 `WinGet\Links`，安装器会在
  PATH 刷新前把它放在那里）；都没有时返回 `needs_install`，并给出安装 uv 的命令。
* **`python` 打开了 Microsoft Store。** 那是"应用执行别名"的占位程序；启动器会试运行每个解释器，因此会跳过它（可在
  *设置 → 应用 → 高级应用设置 → 应用执行别名* 中关闭）。
* **在安装 vidroute 之前就启动的 Agent 宿主**看不到新的 PATH。`vidroute setup` 会把运行 vidroute 的解释器写进复制的技能
  （`.vidroute-skill.cfg`），启动器会直接使用它。
* **PATH 上没有 `uv`。** vidroute 会从 PyPI 把固定版本的 `uv` 引导安装到 `%USERPROFILE%\.vidroute\runtimes\uv`（校验哈希），并为模型
  worker 管理自己的 Python 3.12；不会动系统 Python。
* **Windows on Arm（骁龙 X）。** `opencv-python-headless`、`pyclipper`（OCR）和 `ctranslate2`（faster-whisper）没有发布 `win_arm64`
  wheel，所以这些 worker 环境使用 x64 Python，由 Windows 仿真运行；语音/VAD 环境保持原生 Arm64。尚未在真实 Arm 硬件上验证
  （见 KNOWN_ISSUES.md）。
* **在中国大陆下载慢或被墙。** 当 huggingface.co 不通而 hf-mirror.com 或 ModelScope 有响应时，安装会切换到阿里云 PyPI 镜像、npmmirror
  的 Python 构建，以及用于模型的 ModelScope/hf-mirror（`--mirror cn` 可强制启用）。所有文件仍会校验哈希；你自己设置的
  `UV_DEFAULT_INDEX` / `UV_PYTHON_INSTALL_MIRROR` 始终优先。
* **控制台窗口一闪而过。** 子进程以 `CREATE_NO_WINDOW` 启动。
* **杀毒软件锁定文件。** 扫描程序占用文件时，写入后立即替换可能因 `PermissionError` 失败；原子写入会短暂退避重试（总共约两秒）后才放弃。

## GPU

* **CUDA 只是升级，从不是必需。** CPU 基线始终可用。CUDA 上的 `faster-whisper` 只在冒烟测试通过并记录后才使用；任何 CUDA/cuDNN DLL
  问题都会回退到 CPU，并记录在 `state.json`（`gpu_smoke`）中。按项目规则，Windows 上的 GPU 问题最多尝试两次，之后该引擎保持只用 CPU，
  问题记入 `KNOWN_ISSUES.md`。
* **WMI 的 `AdapterRAM` 不可靠**（32 位计数器）：来自 `Win32_VideoController` 的显存标为近似值；有 `nvidia-smi` 时以它为准。
