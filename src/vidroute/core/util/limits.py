"""What this process may really use: containers and CPU affinity hide behind the host's numbers.

``os.cpu_count()`` and ``/proc/meminfo`` describe the machine, not a container's cgroup limit
or a ``taskset`` mask.
"""

from __future__ import annotations

import math
import os
from pathlib import Path

CGROUP_ROOT = Path("/sys/fs/cgroup")


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="ascii").strip()
    except (OSError, ValueError):
        return None


def _v2_dirs(root: Path, proc_cgroup: Path) -> list[Path]:
    """This process's cgroup and its parents; a limit on any of them applies. Inside a container: the root."""
    text = _read(proc_cgroup) or ""
    rel = next((line.split("::", 1)[1] for line in text.splitlines() if line.startswith("0::")), "/")
    current = root / rel.strip("/")
    dirs = [current, *[p for p in current.parents if root == p or root in p.parents]]
    return dirs if current.is_dir() else [root]


def cgroup_cpus(root: Path = CGROUP_ROOT, proc_cgroup: Path = Path("/proc/self/cgroup")) -> float | None:
    quotas = []
    for d in _v2_dirs(root, proc_cgroup):
        parts = (_read(d / "cpu.max") or "").split()
        if len(parts) == 2 and parts[0] != "max" and float(parts[1]) > 0:
            quotas.append(float(parts[0]) / float(parts[1]))
    quota, period = _read(root / "cpu" / "cpu.cfs_quota_us"), _read(root / "cpu" / "cpu.cfs_period_us")
    if quota and period and int(quota) > 0 and int(period) > 0:  # cgroup v1
        quotas.append(int(quota) / int(period))
    return min(quotas) if quotas else None


def cgroup_memory(root: Path = CGROUP_ROOT, proc_cgroup: Path = Path("/proc/self/cgroup")) -> tuple[int, int] | None:
    """(limit, usage) in bytes for the tightest memory limit on this process, if any."""
    best: tuple[int, int] | None = None
    for d in _v2_dirs(root, proc_cgroup):
        limit, usage = _read(d / "memory.max"), _read(d / "memory.current")
        if limit and limit.isdigit() and (best is None or int(limit) < best[0]):
            best = (int(limit), int(usage) if usage and usage.isdigit() else 0)
    limit, usage = _read(root / "memory" / "memory.limit_in_bytes"), _read(root / "memory" / "memory.usage_in_bytes")
    # cgroup v1 reports "no limit" as a huge number, which never wins against the host's RAM.
    if limit and limit.isdigit() and (best is None or int(limit) < best[0]):
        best = (int(limit), int(usage) if usage and usage.isdigit() else 0)
    return best


def usable_cpus() -> int:
    """CPUs this process can use: the affinity mask and the cgroup quota, whichever is tighter."""
    try:
        count = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        count = os.cpu_count() or 1
    quota = cgroup_cpus()
    if quota:
        count = min(count, max(1, math.ceil(quota)))
    return max(1, count)


def engine_threads() -> int:
    """Threads for one model: about half of a roomy machine (hyperthreads add little), all of a small one."""
    n = usable_cpus()
    return n if n < 4 else min(8, n // 2)
