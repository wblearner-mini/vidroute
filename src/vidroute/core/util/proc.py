"""Subprocess helpers.

Rules (see references/windows-troubleshooting.md):
  * arguments are always passed as a list; ``shell=True`` is never used;
  * children get ``PYTHONUTF8=1`` so Python workers speak UTF-8 on Windows;
  * on Windows no console window is flashed (``CREATE_NO_WINDOW``);
  * every call that probes the environment has a timeout.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout  # distinct from TimeoutError on 3.10
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

IS_WINDOWS = os.name == "nt"
CREATE_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0


def child_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    if extra:
        env.update(extra)
    return env


@dataclass
class Result:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    elapsed: float = 0.0

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out


def _decode(data: Any) -> str:
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", "replace")
    return str(data)


def run(
    args: Sequence[str | os.PathLike[str]],
    *,
    timeout: float | None = None,
    cwd: str | os.PathLike[str] | None = None,
    env: Mapping[str, str] | None = None,
    input_text: str | None = None,
) -> Result:
    """Run a command and capture text output; never raises for non-zero exit."""
    argv = [os.fspath(a) for a in args]
    start = time.monotonic()
    try:
        cp = subprocess.run(
            argv,
            capture_output=True,
            timeout=timeout,
            cwd=cwd,
            env=child_env(env),
            input=input_text.encode("utf-8") if input_text is not None else None,
            stdin=subprocess.DEVNULL if input_text is None else None,
            creationflags=CREATE_NO_WINDOW,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return Result(argv, -1, _decode(exc.stdout), _decode(exc.stderr), True, time.monotonic() - start)
    except (FileNotFoundError, PermissionError, OSError) as exc:
        return Result(argv, 127, "", str(exc), False, time.monotonic() - start)
    return Result(argv, cp.returncode, _decode(cp.stdout), _decode(cp.stderr), False, time.monotonic() - start)


def run_bytes(
    args: Sequence[str | os.PathLike[str]],
    *,
    timeout: float | None = None,
    env: Mapping[str, str] | None = None,
) -> tuple[int, bytes, str]:
    """Like :func:`run` but returns raw stdout bytes (for rawvideo/rawaudio pipes)."""
    argv = [os.fspath(a) for a in args]
    try:
        cp = subprocess.run(argv, capture_output=True, timeout=timeout, env=child_env(env),
                            stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW, check=False)
    except subprocess.TimeoutExpired:
        return -1, b"", "超时"
    except OSError as exc:
        return 127, b"", str(exc)
    return cp.returncode, cp.stdout, _decode(cp.stderr)


def popen(
    args: Sequence[str | os.PathLike[str]],
    *,
    cwd: str | os.PathLike[str] | None = None,
    env: Mapping[str, str] | None = None,
    stdin: Any = subprocess.DEVNULL,
    stdout: Any = subprocess.PIPE,
    stderr: Any = subprocess.PIPE,
) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [os.fspath(a) for a in args],
        cwd=cwd,
        env=child_env(env),
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        creationflags=CREATE_NO_WINDOW,
    )


def which(name: str, extra_dirs: Iterable[Path] = ()) -> Path | None:
    for d in extra_dirs:
        for candidate in (d / name, d / f"{name}.exe"):
            if candidate.is_file():
                return candidate
    found = shutil.which(name)
    return Path(found) if found else None


@dataclass
class TaskOutcome:
    ok: bool
    value: Any = None
    error: str | None = None
    timed_out: bool = False
    elapsed: float = 0.0


def run_parallel(tasks: Mapping[str, Callable[[], Any]], timeout: float) -> dict[str, TaskOutcome]:
    """Run independent probes concurrently; each gets ``timeout`` seconds.

    A probe that overruns is reported as timed out and never blocks the caller
    (its thread is abandoned; probes must use their own subprocess timeouts).
    """
    results: dict[str, TaskOutcome] = {}
    pool = ThreadPoolExecutor(max_workers=max(1, len(tasks)), thread_name_prefix="vidroute-probe")
    started = time.monotonic()
    futures: dict[str, Future[Any]] = {name: pool.submit(fn) for name, fn in tasks.items()}
    try:
        for name, fut in futures.items():
            remaining = max(0.0, timeout - (time.monotonic() - started))
            try:
                value = fut.result(timeout=remaining)
                results[name] = TaskOutcome(True, value, elapsed=time.monotonic() - started)
            except (TimeoutError, FuturesTimeout):
                results[name] = TaskOutcome(False, error=f"{timeout:.0f} 秒后超时", timed_out=True)
            except Exception as exc:  # noqa: BLE001 - probe failures are data
                results[name] = TaskOutcome(False, error=f"{type(exc).__name__}: {exc}")
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return results
