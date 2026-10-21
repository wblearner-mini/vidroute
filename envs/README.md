# Worker environment locks

vidroute's orchestrator has no dependencies. Heavy libraries run in isolated
virtual environments under `$VIDROUTE_HOME/envs/<group>`, created only after
the user confirms an install plan. Each group is pinned by a hash-locked
requirements file:

| group | purpose | lock |
|---|---|---|
| `sherpa` | sherpa-onnx (VAD, SenseVoice, Whisper) | `sherpa.lock.txt` |
| `ocr` | RapidOCR + onnxruntime (PP-OCRv6) | `ocr.lock.txt` |
| `whisper` | faster-whisper (CPU) / + CUDA 12, cuDNN 9 | `whisper.lock.txt`, `whisper-cuda.lock.txt` |

Regenerate a lock (Python 3.12 is the worker interpreter on every platform):

```bash
uv pip compile --universal --generate-hashes --python-version 3.12 envs/sherpa.in -o envs/sherpa.lock.txt
```

Installs use `--require-hashes`, so a tampered or substituted wheel fails.
