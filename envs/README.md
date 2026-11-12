# worker 环境锁文件

vidroute 的编排器没有依赖。重型库运行在 `$VIDROUTE_HOME/envs/<组>` 下的隔离虚拟环境中，只在用户确认安装计划后才创建。
每个组都由一个带哈希锁定的 requirements 文件固定：

| 组 | 用途 | 锁文件 |
|---|---|---|
| `sherpa` | sherpa-onnx（VAD、SenseVoice、Whisper） | `sherpa.lock.txt` |
| `ocr` | RapidOCR + onnxruntime（PP-OCRv6） | `ocr.lock.txt` |
| `whisper` | faster-whisper（CPU）/ + CUDA 12、cuDNN 9 | `whisper.lock.txt`、`whisper-cuda.lock.txt` |

重新生成锁文件（各平台的 worker 解释器都是 Python 3.12）：

```bash
uv pip compile --universal --generate-hashes --python-version 3.12 envs/sherpa.in -o envs/sherpa.lock.txt
```

安装时使用 `--require-hashes`，所以被篡改或替换的 wheel 会安装失败。
