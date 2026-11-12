"""An advisory lock on an output folder, so two runs never rewrite the same files at once.

The operating system drops the lock when the holder exits, also after ``kill -9``, so a
crash never leaves a stale lock to clean up.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from vidroute.core.context import Ctx
from vidroute.core.errors import RunFailed

WAIT_SECONDS = 3600.0  # a long video on a slow machine can legitimately take this long


def _try_lock(fd: int) -> bool:
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, PermissionError):
        return False
    except OSError as exc:
        if os.name == "nt" and exc.errno in (13, 36):  # EACCES, EDEADLOCK: held by another process
            return False
        return True  # this filesystem has no locks (some network mounts): carry on unprotected
    return True


@contextmanager
def exclusive(path: Path, ctx: Ctx, waiting: str, wait_seconds: float = WAIT_SECONDS, poll: float = 0.5) -> Iterator[None]:
    """Hold ``path`` exclusively; wait for another run instead of failing, since its results get reused."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        deadline = time.monotonic() + wait_seconds
        told = False
        while not _try_lock(fd):
            if not told:
                ctx.progress("wait", None, None, f"{waiting}：正在等待它完成")
                told = True
            ctx.check_cancelled()
            if time.monotonic() >= deadline:
                raise RunFailed(f"{waiting}，等了 {wait_seconds / 60:.0f} 分钟仍未结束。请停止那个进程，"
                                "或用 --out 换一个输出目录。", code="output_busy")
            time.sleep(poll)
        yield
    finally:
        os.close(fd)
