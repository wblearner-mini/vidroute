# Third-party licenses

vidroute itself is MIT-licensed and has **no runtime Python dependencies**. Neither this repository
nor the Python package contains third-party code, binaries or model weights. Everything below is
downloaded by the user, on their own machine, after they confirm an install plan
(`vidroute install`), from the sources pinned in `models/manifest.yaml` and `envs/*.lock.txt`.
Each output folder's `manifest.json` records which models produced it.

Regenerate the package tables with `python tools/license_report.py <lock files>`; CI runs it with
`--check` against an allow-list of permissive and weak-copyleft licenses.

## Models

| model | status | license | license text | downloaded from |
|---|---|---|---|---|
| `vad-silero` | default | MIT | [Silero VAD](https://github.com/snakers4/silero-vad/blob/master/LICENSE) | sherpa-onnx releases (GitHub) |
| `asr-sensevoice-small-int8` | default | FunASR Model Open Source License v1.1 | [MODEL_LICENSE](https://github.com/modelscope/FunASR/blob/main/MODEL_LICENSE) | sherpa-onnx releases (GitHub), Hugging Face, hf-mirror |
| `asr-sensevoice-yue-int8` | alternative | Apache-2.0 (+ FunASR attribution) | [WSYue-ASR](https://huggingface.co/ASLP-lab/WSYue-ASR) | sherpa-onnx releases (GitHub) |
| `asr-whisper-small-int8` | default (non-Chinese) | MIT | [OpenAI Whisper](https://github.com/openai/whisper/blob/main/LICENSE) | Hugging Face (`csukuangfj/sherpa-onnx-whisper-small`), hf-mirror |
| `asr-faster-whisper-small` | alternative | MIT | [Systran/faster-whisper-small](https://huggingface.co/Systran/faster-whisper-small) | Hugging Face |
| `asr-faster-whisper-large-v3-turbo` | alternative (CUDA) | MIT | [model card](https://huggingface.co/dropbox-dash/faster-whisper-large-v3-turbo) | Hugging Face |
| `asr-funasr-nano-int8` | experimental | FunASR Model Open Source License v1.1 | [MODEL_LICENSE](https://github.com/modelscope/FunASR/blob/main/MODEL_LICENSE) | sherpa-onnx releases (GitHub) |
| `asr-qwen3-asr-0.6b-int8` | experimental | Apache-2.0 | [Qwen3-ASR](https://huggingface.co/Qwen/Qwen3-ASR-0.6B) | sherpa-onnx releases (GitHub) |
| `ocr-ppocrv6-small`, `ocr-ppocrv6-tiny` | default / alternative | Apache-2.0 | [PaddleOCR / RapidOCR](https://github.com/RapidAI/RapidOCR/blob/main/python/MODEL_LICENSES.md) | bundled in the `rapidocr` wheel, else ModelScope (`RapidAI/RapidOCR@v3.10.0`) |

**Attribution.** SenseVoice-Small and Fun-ASR-Nano come from Alibaba's FunASR / FunAudioLLM
projects. Their license allows free use, modification and redistribution, including
commercial use, provided the source and the model name are credited; vidroute keeps the original
model names in install plans, `vidroute models list` and every output `manifest.json`. Whisper is
© OpenAI (MIT). Silero VAD is © Silero Team (MIT). PP-OCR models are © PaddlePaddle authors
(Apache-2.0), converted to ONNX by RapidAI.

## External tools

| tool | license | how it gets there |
|---|---|---|
| FFmpeg / ffprobe | LGPL-2.1-or-later or GPL-2.0-or-later, depending on the build | installed by the user (`winget install --id Gyan.FFmpeg`, Homebrew, apt, ...); vidroute only runs it |
| CPython 3.12 for worker environments | PSF-2.0 | python-build-standalone, installed by `uv` into `VIDROUTE_HOME` |
| uv | MIT OR Apache-2.0 | PyPI (hash-pinned in `envs/uv.lock.txt`) when not already on PATH |

## Python packages in the worker environments

Installed with `uv pip install --require-hashes` into isolated environments under
`VIDROUTE_HOME/envs` (`sherpa`: VAD/ASR, `ocr`: OCR, `whisper`/`whisper-cuda`: faster-whisper).
The NVIDIA libraries are only installed for the optional CUDA path and are governed by NVIDIA's
license agreements.

| package | version | license | used in |
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

## Development and test tools (not shipped)

Used only to build and test vidroute (`requirements-dev.lock.txt`), including the official MCP SDK
as a test client and `skills-ref` to validate the Agent Skill.

| package | version | license | used in |
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
