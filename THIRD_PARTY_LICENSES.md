# 第三方许可

vidroute 本身采用 MIT 许可，**没有任何运行时 Python 依赖**。本仓库和 Python 包都不包含第三方代码、二进制文件或模型权重。
下面列出的所有内容，都是用户确认安装计划（`vidroute install`）之后，在自己的机器上从 `models/manifest.yaml` 和
`envs/*.lock.txt` 中固定的来源下载的。每个输出目录的 `manifest.json` 都记录了生成它所用的模型。

用 `python tools/license_report.py <锁文件>` 重新生成包列表；CI 会加 `--check` 运行它，对照宽松许可与弱 copyleft 许可的白名单进行检查。

## 模型

| 模型 | 状态 | 许可 | 许可原文 | 下载来源 |
|---|---|---|---|---|
| `vad-silero` | 默认 | MIT | [Silero VAD](https://github.com/snakers4/silero-vad/blob/master/LICENSE) | sherpa-onnx 发布页（GitHub） |
| `diarize-pyannote-campplus` | 默认 | MIT (pyannote segmentation-3.0) + Apache-2.0 (3D-Speaker CAM++) | [3D-Speaker](https://github.com/modelscope/3D-Speaker/blob/main/LICENSE) | Hugging Face, hf-mirror （sherpa-onnx 导出版） |
| `asr-sensevoice-small-int8` | 默认 | FunASR Model Open Source License v1.1 | [MODEL_LICENSE](https://github.com/modelscope/FunASR/blob/main/MODEL_LICENSE) | sherpa-onnx 发布页（GitHub）, Hugging Face, hf-mirror |
| `asr-sensevoice-yue-int8` | 备选 | Apache-2.0（+ FunASR 署名） | [WSYue-ASR](https://huggingface.co/ASLP-lab/WSYue-ASR) | sherpa-onnx 发布页（GitHub） |
| `asr-whisper-small-int8` | 默认（非中文） | MIT | [OpenAI Whisper](https://github.com/openai/whisper/blob/main/LICENSE) | Hugging Face (`csukuangfj/sherpa-onnx-whisper-small`), hf-mirror |
| `asr-faster-whisper-small` | 备选 | MIT | [Systran/faster-whisper-small](https://huggingface.co/Systran/faster-whisper-small) | Hugging Face |
| `asr-faster-whisper-large-v3-turbo` | 备选（CUDA） | MIT | [模型卡](https://huggingface.co/dropbox-dash/faster-whisper-large-v3-turbo) | Hugging Face |
| `asr-funasr-nano-int8` | 实验 | FunASR Model Open Source License v1.1 | [MODEL_LICENSE](https://github.com/modelscope/FunASR/blob/main/MODEL_LICENSE) | sherpa-onnx 发布页（GitHub） |
| `asr-qwen3-asr-0.6b-int8` | 实验 | Apache-2.0 | [Qwen3-ASR](https://huggingface.co/Qwen/Qwen3-ASR-0.6B) | sherpa-onnx 发布页（GitHub） |
| `ocr-ppocrv6-small`, `ocr-ppocrv6-tiny` | 默认 / 备选 | Apache-2.0 | [PaddleOCR / RapidOCR](https://github.com/RapidAI/RapidOCR/blob/main/python/MODEL_LICENSES.md) | 随 `rapidocr` wheel 自带，否则来自 ModelScope (`RapidAI/RapidOCR@v3.10.0`) |

**署名。** SenseVoice-Small 与 Fun-ASR-Nano 来自阿里巴巴的 FunASR / FunAudioLLM 项目。其许可允许免费使用、修改和再分发
（含商用），前提是注明来源和模型名称；vidroute 在安装计划、`vidroute models list` 和每个输出的 `manifest.json`
中都保留了原始模型名。Whisper © OpenAI（MIT）。Silero VAD © Silero Team（MIT）。PP-OCR 模型 © PaddlePaddle 作者
（Apache-2.0），由 RapidAI 转换为 ONNX。

## 外部工具

| 工具 | 许可 | 获取方式 |
|---|---|---|
| FFmpeg / ffprobe | LGPL-2.1-or-later 或 GPL-2.0-or-later，取决于构建 | 由用户安装（`winget install --id Gyan.FFmpeg`、Homebrew、apt……）；vidroute 只负责调用 |
| gyan.dev 的 FFmpeg 9.0.2 essentials 构建（Windows，可选） | GPL-3.0-or-later（静态构建，含 libx264、libmp3lame、freetype） | 仅在用户确认计划后从 `github.com/GyanD/codexffmpeg` 下载（固定 SHA-256），连同其 `LICENSE` 放入 `$VIDROUTE_HOME/runtimes/ffmpeg`；vidroute 从不再分发；源码：FFmpeg 提交 946fcce07b |
| uv 0.13.4（安装脚本、worker 环境） | MIT OR Apache-2.0 | 由 `install.ps1` / `install.sh` 或 vidroute 的引导程序下载（固定 SHA-256） |
| worker 环境用的 CPython 3.12 | PSF-2.0 | python-build-standalone，由 `uv` 安装到 `VIDROUTE_HOME` |
| uv | MIT OR Apache-2.0 | PATH 上没有时从 PyPI 获取（在 `envs/uv.lock.txt` 中固定哈希） |

## worker 环境中的 Python 包

用 `uv pip install --require-hashes` 安装到 `VIDROUTE_HOME/envs` 下的隔离环境（`sherpa`：VAD/ASR，`ocr`：OCR，
`whisper`/`whisper-cuda`：faster-whisper）。NVIDIA 的库只在可选的 CUDA 路径中安装，受 NVIDIA 许可协议约束。

| 包 | 版本 | 许可 | 用于 |
|---|---|---|---|
| anyio | 4.15.1 | MIT | whisper, whisper-cuda |
| av | 19.0.2 | BSD-3-Clause | whisper, whisper-cuda |
| certifi | 2026.10.19 | MPL-2.0 | ocr |
| charset-normalizer | 3.5.2 | MIT | ocr |
| click | 8.5.0 | BSD-3-Clause | whisper, whisper-cuda |
| colorama | 0.4.6 | BSD-3-Clause | ocr, whisper, whisper-cuda |
| colorlog | 6.12.0 | MIT | ocr |
| ctranslate2 | 4.8.3 | MIT | whisper, whisper-cuda |
| faster-whisper | 1.2.1 | MIT | whisper, whisper-cuda |
| filelock | 4.2.4 | MIT | whisper, whisper-cuda |
| flatbuffers | 25.12.19 | Apache-2.0 | ocr, whisper, whisper-cuda |
| fsspec | 2026.9.0 | BSD-3-Clause | whisper, whisper-cuda |
| h11 | 0.16.0 | MIT | whisper, whisper-cuda |
| hf-xet | 1.7.0 | Apache-2.0 | whisper, whisper-cuda |
| httpcore2 | 2.16.0 | BSD-3-Clause | whisper, whisper-cuda |
| httpx2 | 2.16.0 | BSD-3-Clause | whisper, whisper-cuda |
| httpx2-jsfetch | 1.0 | BSD-3-Clause | whisper, whisper-cuda |
| huggingface-hub | 2.2.0 | Apache-2.0 | whisper, whisper-cuda |
| idna | 3.20 | BSD-3-Clause | ocr, whisper, whisper-cuda |
| numpy | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | ocr, sherpa, whisper, whisper-cuda |
| nvidia-cublas-cu12 | 12.9.2.10 | LicenseRef-NVIDIA-Software-License (optional CUDA path only) | whisper-cuda |
| nvidia-cuda-nvrtc-cu12 | 12.9.86 | LicenseRef-NVIDIA-Software-License (optional CUDA path only) | whisper-cuda |
| nvidia-cudnn-cu12 | 9.27.0.42 | LicenseRef-NVIDIA-cuDNN-SLA (optional CUDA path only) | whisper-cuda |
| omegaconf | 2.4.0 | BSD-3-Clause | ocr |
| onnxruntime | 1.31.0 | MIT | ocr, whisper, whisper-cuda |
| opencv-python-headless | 5.0.0.93 | Apache-2.0 | ocr |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | ocr, whisper, whisper-cuda |
| pillow | 13.0.0 | MIT-CMU | ocr |
| protobuf | 7.36.2 | BSD-3-Clause | ocr, whisper, whisper-cuda |
| pyclipper | 1.4.0 | MIT | ocr |
| pyyaml | 6.0.3 | MIT | ocr, whisper, whisper-cuda |
| rapidocr | 3.10.0 | Apache-2.0 | ocr |
| requests | 2.34.2 | Apache-2.0 | ocr |
| shapely | 2.2.0 | BSD-3-Clause | ocr |
| sherpa-onnx | 1.13.8 | Apache-2.0 | sherpa |
| sherpa-onnx-core | 1.13.8 | Apache-2.0 | sherpa |
| six | 1.17.0 | MIT | ocr |
| tokenizers | 0.23.3 | Apache-2.0 | whisper, whisper-cuda |
| tqdm | 4.70.1 | MPL-2.0 AND MIT | ocr, whisper, whisper-cuda |
| truststore | 0.10.5 | MIT | whisper, whisper-cuda |
| typing-extensions | 4.16.0 | PSF-2.0 | whisper, whisper-cuda |
| urllib3 | 2.8.0 | MIT | ocr |
| uv | 0.13.4 | MIT OR Apache-2.0 | uv |

## 开发与测试工具（不随包分发）

只用于构建和测试 vidroute（`requirements-dev.lock.txt`），包括作为测试客户端的官方 MCP SDK，以及用来校验 Agent Skill 的 `skills-ref`。

| 包 | 版本 | 许可 | 用于 |
|---|---|---|---|
| annotated-types | 0.8.0 | MIT | requirements-dev |
| anyio | 4.15.1 | MIT | requirements-dev |
| attrs | 26.1.0 | MIT | requirements-dev |
| build | 1.6.1 | MIT | requirements-dev |
| cffi | 2.1.1 | MIT-0 | requirements-dev |
| click | 8.5.0 | BSD-3-Clause | requirements-dev |
| colorama | 0.4.6 | BSD-3-Clause | requirements-dev |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause | requirements-dev |
| editables | 0.6 | MIT | requirements-dev |
| exceptiongroup | 1.3.1 | MIT | requirements-dev |
| h11 | 0.16.0 | MIT | requirements-dev |
| hatchling | 1.32.4 | MIT | requirements-dev |
| httpcore2 | 2.14.0 | BSD-3-Clause | requirements-dev |
| httpx2 | 2.14.0 | BSD-3-Clause | requirements-dev |
| httpx2-jsfetch | 1.0 | BSD-3-Clause | requirements-dev |
| idna | 3.20 | BSD-3-Clause | requirements-dev |
| importlib-metadata | 9.0.1 | Apache-2.0 | requirements-dev |
| iniconfig | 2.3.1 | MIT | requirements-dev |
| jsonschema | 4.26.0 | MIT | requirements-dev |
| jsonschema-specifications | 2025.9.1 | MIT | requirements-dev |
| mcp | 2.3.0 | MIT | requirements-dev |
| mcp-types | 2.3.0 | MIT | requirements-dev |
| opentelemetry-api | 1.45.1 | Apache-2.0 | requirements-dev |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | requirements-dev |
| pathspec | 1.1.1 | MPL-2.0 | requirements-dev |
| pluggy | 1.7.0 | MIT | requirements-dev |
| pycparser | 3.11 | BSD-3-Clause | requirements-dev |
| pydantic | 2.14.1 | MIT | requirements-dev |
| pydantic-core | 2.50.1 | MIT | requirements-dev |
| pygments | 2.21.0 | BSD-2-Clause | requirements-dev |
| pyjwt | 2.15.1 | MIT | requirements-dev |
| pyproject-hooks | 1.3.3 | MIT | requirements-dev |
| pytest | 9.1.1 | MIT | requirements-dev |
| python-dateutil | 3.0.0 | Apache-2.0 OR BSD-3-Clause | requirements-dev |
| python-multipart | 0.0.32 | Apache-2.0 | requirements-dev |
| pywin32 | 312 | PSF-2.0 | requirements-dev |
| pyyaml | 6.0.3 | MIT | requirements-dev |
| referencing | 0.37.0 | MIT | requirements-dev |
| rpds-py | 0.30.0 | MIT | requirements-dev |
| six | 1.17.0 | MIT | requirements-dev |
| skills-ref | 0.1.1 | Apache-2.0 | requirements-dev |
| sse-starlette | 3.5.0 | BSD-3-Clause | requirements-dev |
| starlette | 1.7.0 | BSD-3-Clause | requirements-dev |
| strictyaml | 1.7.3 | MIT | requirements-dev |
| tomli | 2.5.0 | MIT | requirements-dev |
| tomlkit | 0.15.2 | MIT | requirements-dev |
| trove-classifiers | 2026.9.21.13 | Apache-2.0 | requirements-dev |
| truststore | 0.10.5 | MIT | requirements-dev |
| typing-extensions | 4.16.0 | PSF-2.0 | requirements-dev |
| typing-inspection | 0.4.4 | MIT | requirements-dev |
| tzdata | 2026.5 | Apache-2.0 | requirements-dev |
| uvicorn | 0.54.0 | BSD-3-Clause | requirements-dev |
| zipp | 4.1.1 | MIT | requirements-dev |
