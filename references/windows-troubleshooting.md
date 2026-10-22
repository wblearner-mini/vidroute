# Windows troubleshooting

Known pitfalls on Windows 10/11 and what vidroute does about them. Each entry
was hit during development or in CI (`windows-latest`).

## ffmpeg

* **Not installed.** `vidroute doctor` reports it with the exact command:
  `winget install --id Gyan.FFmpeg`. vidroute only runs it with
  `vidroute install --yes`; afterwards open a new terminal so `PATH` is
  refreshed, or set `vidroute config set ffmpeg_path C:\path\to\ffmpeg.exe`.
* **Encoders differ between builds.** Native routes need an H.264 encoder.
  `libx264` is preferred; without it vidroute falls back to `libopenh264`, then
  MPEG-4 Part 2. Hardware encoders (`h264_nvenc`, `h264_qsv`, `h264_amf`,
  `h264_mf`) are detected but never used, so output size and quality stay
  predictable. Audio attachments use `libmp3lame`, else AAC in `.m4a`.
* **`drawtext` and fonts.** Contact-sheet labels use `C:\Windows\Fonts\arial.ttf`
  (or `segoeui.ttf`). The drive colon must be escaped inside the filter
  (`C\:/Windows/Fonts/arial.ttf`); vidroute does this. Builds without
  `drawtext` get unlabelled tiles plus `contact-sheets/index.md`.
* **Short sound bursts in "silent" audio.** Windows builds of the AAC encoder
  can leave a ~70 ms transient at the start of a silent track. The energy VAD
  ignores sounds shorter than 0.25 s, so it is not reported as speech.

## Paths and text

* **Spaces and non-ASCII paths** (`D:\会议 录屏\周会.mp4`) work: every
  subprocess gets an argument list (never `shell=True`), and output folders
  keep CJK characters in their slug. Tests cover a Chinese path with spaces.
* **Console code pages.** The CLI switches stdout/stderr to UTF-8; Python
  workers run with `PYTHONUTF8=1`. If a terminal still shows mojibake, use
  Windows Terminal or `chcp 65001`.
* **Line endings.** The repository forces LF (`.gitattributes`), except for
  `.ps1/.bat/.cmd`; generated Markdown is written with `\n`.
* **Long paths.** Output folders are short (`<stem, max 48 chars>-<hash>`); if a
  very deep `--out` still exceeds 260 characters, enable long paths
  (`LongPathsEnabled`) or choose a shorter `--out`.

## Python environments

* **No `uv` on PATH.** vidroute bootstraps a pinned `uv` into
  `%USERPROFILE%\.vidroute\runtimes\uv` from PyPI (hash-checked) and manages its
  own Python 3.12 for the model workers; the system Python is not touched.
* **Console windows flashing.** Child processes start with
  `CREATE_NO_WINDOW`.
* **Antivirus locks.** Replacing a file right after writing it can fail with
  `PermissionError` while a scanner holds it; atomic writes retry with a short
  back-off (about two seconds in total) before giving up.

## GPU

* **CUDA is an upgrade, never a requirement.** The CPU baseline always works.
  `faster-whisper` on CUDA is only used after a recorded, passed smoke test;
  any CUDA/cuDNN DLL problem falls back to CPU and is recorded in
  `state.json` (`gpu_smoke`). Per the project rules, a Windows GPU problem is
  tried twice at most, then the engine stays CPU-only and the issue goes to
  `KNOWN_ISSUES.md`.
* **`AdapterRAM` from WMI is unreliable** (32-bit counter): VRAM from
  `Win32_VideoController` is marked approximate; `nvidia-smi` wins when
  present.
