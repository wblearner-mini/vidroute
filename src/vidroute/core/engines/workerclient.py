"""Orchestrator side of the worker protocol (see vidroute/workers/__init__.py).

A WorkerClient owns one worker process. Calls are sequential; progress lines
are forwarded to the Ctx; cancellation kills the process. Worker stderr goes to
a log file under ``$VIDROUTE_HOME/cache/logs`` (never into memory, so a chatty
native library cannot deadlock the pipe) and its tail is attached to errors.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from vidroute.core import paths
from vidroute.core.context import Ctx
from vidroute.core.errors import Cancelled, NeedsInstall, RunFailed
from vidroute.core.install.manifest import EnvGroup
from vidroute.core.util import proc
from vidroute.workers import script

READY_TIMEOUT = 120.0


class WorkerError(RunFailed):
    default_code = "worker_error"


def _nvidia_ld_path(group: EnvGroup) -> str | None:
    """Linux: CTranslate2 finds the cuBLAS/cuDNN wheels' libraries via LD_LIBRARY_PATH."""
    if os.name == "nt":
        return None
    lib_dirs = []
    for site in group.path.glob("lib/python*/site-packages/nvidia"):
        for sub in ("cublas", "cudnn", "cuda_runtime", "cuda_nvrtc"):
            d = site / sub / "lib"
            if d.is_dir():
                lib_dirs.append(str(d))
    if not lib_dirs:
        return None
    existing = os.environ.get("LD_LIBRARY_PATH")
    return os.pathsep.join(lib_dirs + ([existing] if existing else []))


class WorkerClient:
    def __init__(self, group: EnvGroup, worker: str, ctx: Ctx | None = None, stage: str | None = None) -> None:
        self.group = group
        self.worker = worker
        self.ctx = ctx or Ctx()
        self.stage = stage or worker
        self.proc: subprocess.Popen[bytes] | None = None
        self.lines: queue.Queue[str | None] = queue.Queue()
        self.next_id = 0
        self.log_path: Path | None = None
        self.info: dict[str, Any] = {}

    # ------------------------------------------------------------ lifecycle
    def start(self) -> "WorkerClient":
        if not self.group.python_exe.is_file():
            raise NeedsInstall(
                f"worker 环境 '{self.group.name}' 未安装。",
                code="env_missing",
                hint=f"vidroute install --capability {'ocr' if self.group.name == 'ocr' else 'asr'}",
            )
        logs = paths.ensure_dir(paths.cache_dir() / "logs")
        self.log_path = logs / f"worker-{self.worker}-{int(time.time())}-{os.getpid()}.log"
        env = {}
        ld = _nvidia_ld_path(self.group)
        if ld:
            env["LD_LIBRARY_PATH"] = ld
        self._log_fh = self.log_path.open("wb")
        self.proc = proc.popen(
            [self.group.python_exe, "-I", str(script(self.worker))],
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._log_fh,
        )
        threading.Thread(target=self._read, daemon=True, name=f"vidroute-{self.worker}-reader").start()
        ready = self._next_message(READY_TIMEOUT, expect_id=None)
        if ready.get("event") != "ready":
            raise WorkerError(f"{self.worker} worker 未能启动：{ready}")
        self.info = ready
        return self

    def __enter__(self) -> "WorkerClient":
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def close(self) -> None:
        if self.proc is None:
            return
        if self.proc.poll() is None:
            try:
                self._send({"id": -1, "method": "shutdown"})
                self.proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                pass
        self._kill()
        try:
            self._log_fh.close()
        except Exception:  # noqa: BLE001
            pass
        # Keep logs only when something went wrong.
        if self.log_path and self.log_path.exists() and self.log_path.stat().st_size == 0:
            self.log_path.unlink(missing_ok=True)

    def _kill(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass

    # -------------------------------------------------------------- plumbing
    def _read(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        for raw in self.proc.stdout:
            self.lines.put(raw.decode("utf-8", "replace"))
        self.lines.put(None)

    def _send(self, obj: dict[str, Any]) -> None:
        assert self.proc is not None and self.proc.stdin is not None
        self.proc.stdin.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def _stderr_tail(self, n: int = 15) -> str:
        if not self.log_path or not self.log_path.exists():
            return ""
        try:
            self._log_fh.flush()
        except Exception:  # noqa: BLE001
            pass
        text = self.log_path.read_text(encoding="utf-8", errors="replace")
        return "\n".join(text.strip().splitlines()[-n:])

    def _next_message(self, timeout: float | None, expect_id: Any) -> dict[str, Any]:
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            if self.ctx.cancelled:
                self._kill()
                raise Cancelled(f"{self.worker} worker 已取消")
            try:
                line = self.lines.get(timeout=0.2)
            except queue.Empty:
                if deadline is not None and time.monotonic() > deadline:
                    self._kill()
                    raise WorkerError(f"{self.worker} worker 超时", data={"stderr": self._stderr_tail()})
                continue
            if line is None:
                raise WorkerError(
                    f"{self.worker} worker 意外退出（退出码 {self.proc.poll() if self.proc else '?'}）",
                    data={"stderr": self._stderr_tail(), "log": str(self.log_path)},
                )
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue  # not protocol output
            if expect_id is not None and "progress" in msg and msg.get("id") == expect_id:
                pr = msg["progress"]
                self.ctx.progress(self.stage, pr.get("done"), pr.get("total"), pr.get("message"))
                continue
            return msg

    # ------------------------------------------------------------------ call
    def call(self, method: str, params: dict[str, Any] | None = None, timeout: float | None = None) -> Any:
        if self.proc is None:
            self.start()
        self.next_id += 1
        rid = self.next_id
        self._send({"id": rid, "method": method, "params": params or {}})
        while True:
            msg = self._next_message(timeout, expect_id=rid)
            if msg.get("id") != rid:
                continue
            if "error" in msg:
                err = msg["error"]
                raise WorkerError(
                    f"{self.worker}.{method} failed: {err.get('type')}: {err.get('message')}",
                    data={"traceback": err.get("traceback"), "stderr": self._stderr_tail()},
                )
            return msg.get("result")
