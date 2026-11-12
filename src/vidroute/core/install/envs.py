"""Isolated worker environments built with uv from hash-locked requirement files.

Everything uv writes is redirected into ``$VIDROUTE_HOME``: the managed Python
(``runtimes/python``), uv's cache (``cache/uv``) and the environments
(``envs/<group>``). The Python install is never registered in the Windows
registry, user uv config files are ignored, and source builds are forbidden
(``--no-build``): worker environments only use prebuilt wheels.

Package indexes and mirrors come from the user's environment
(``UV_DEFAULT_INDEX``, ``UV_PYTHON_INSTALL_MIRROR`` ...) or from the install
plan's network choice (:mod:`vidroute.core.install.network`);
``--require-hashes`` and uv's own Python checksums make any mirror safe.

On Windows on Arm, groups whose locked wheels have no ``win_arm64`` build
(``windows_arm64_arch`` in the manifest) get an x64 Python, which Windows runs
under emulation.
"""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from vidroute.core import paths, state
from vidroute.core.context import Ctx
from vidroute.core.doctor.runtimes import UV_VERSION, find_uv
from vidroute.core.doctor.system import native_machine
from vidroute.core.errors import NeedsInstall, RunFailed
from vidroute.core.install import network
from vidroute.core.install.manifest import EnvGroup
from vidroute.core.resources import data_file
from vidroute.core.util import proc

UV_TIMEOUT = 1800.0  # large wheels (CUDA libraries) on slow links


def uv_env(net: dict[str, Any] | None = None) -> dict[str, str]:
    return {
        "UV_PYTHON_INSTALL_DIR": str(paths.runtimes_dir() / "python"),
        "UV_CACHE_DIR": str(paths.cache_dir() / "uv"),
        "UV_PYTHON_INSTALL_REGISTRY": "0",
        "UV_NO_CONFIG": "1",
        "UV_NO_PROGRESS": "1",
        "UV_LINK_MODE": "copy",
        **network.uv_env(net),
    }


def python_request(group: EnvGroup) -> str:
    """What to ask uv for: the version, or a pinned architecture on Windows on Arm (see module docstring)."""
    if group.windows_arm64_arch and platform.system() == "Windows" and native_machine() == "arm64":
        return f"cpython-{group.python}-windows-{group.windows_arm64_arch}-none"
    return group.python


def lock_path(group: EnvGroup, variant: str = "cpu") -> Path:
    rel = group.lock_cuda if variant == "cuda" and group.lock_cuda else group.lock
    return data_file(rel)


def lock_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def env_ready(group: EnvGroup, variant: str = "cpu") -> bool:
    record = state.load().get("envs", {}).get(group.name)
    if not record or not group.python_exe.is_file():
        return False
    try:
        current = lock_digest(lock_path(group, variant))
    except Exception:  # noqa: BLE001
        return False
    return record.get("lock") == current and record.get("variant") == variant and bool(record.get("import_ok"))


def _run_uv(args: list[str], ctx: Ctx, what: str, timeout: float = UV_TIMEOUT,
            net: dict[str, Any] | None = None) -> proc.Result:
    uv = find_uv()
    if uv is None:
        raise NeedsInstall("创建 worker 环境需要 uv。", code="uv_missing",
                           hint="vidroute install  （它会提议把 uv 安装到 $VIDROUTE_HOME）")
    ctx.check_cancelled()
    res = proc.run([uv, *args], timeout=timeout, env=uv_env(net))
    if not res.ok:
        tail = "\n".join((res.stderr or res.stdout).strip().splitlines()[-12:])
        raise RunFailed(f"{what} 失败：\n{tail}" if tail else f"{what} 失败（超时）", code="env_failed",
                        hint="vidroute install --yes  （重新运行即可：已完成的部分会保留；如果 PyPI 很慢或不通，"
                             "请加 --mirror cn，或把 UV_DEFAULT_INDEX 设为 PyPI 镜像，或把 HTTPS_PROXY 设为你的"
                             "代理）")
    return res


def bootstrap_uv(ctx: Ctx, net: dict[str, Any] | None = None) -> Path:
    """Install a pinned uv into $VIDROUTE_HOME/runtimes/uv using pip of the current interpreter."""
    existing = find_uv()
    if existing:
        return existing
    target = paths.runtimes_dir() / "uv"
    lock = data_file("envs/uv.lock.txt")
    ctx.progress("uv", None, None, f"正在把 uv=={UV_VERSION} 安装到 {target}")
    res = proc.run(
        [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
         "--require-hashes", "--only-binary", ":all:", *network.pip_args(net), "--target", str(target), "-r", str(lock)],
        timeout=600,
    )
    uv = find_uv()
    if not res.ok or uv is None:
        raise NeedsInstall(
            "无法自动安装 uv（没有 pip 或处于离线状态）。",
            code="uv_missing",
            hint="pipx install uv   （或：winget install astral-sh.uv / brew install uv）",
            data={"pip_error": (res.stderr or "").strip()[-600:]},
        )
    return uv


def create_env(group: EnvGroup, ctx: Ctx, variant: str = "cpu", force: bool = False,
               net: dict[str, Any] | None = None) -> dict[str, Any]:
    if not force and env_ready(group, variant):
        return {"group": group.name, "status": "ready", "path": str(group.path)}
    lock = lock_path(group, variant)
    request = python_request(group)
    if group.path.exists():
        shutil.rmtree(group.path)
    ctx.progress("env", None, None, f"{group.name}: installing Python {request} (uv-managed)")
    _run_uv(["python", "install", request, "--no-registry", "--install-dir", str(paths.runtimes_dir() / "python")],
            ctx, f"Installing Python {request}", net=net)
    ctx.progress("env", None, None, f"{group.name}：正在创建环境")
    _run_uv(["venv", "--python", request, "--managed-python", "--no-project", str(group.path)], ctx,
            f"创建环境 {group.name}", net=net)
    ctx.progress("env", None, None, f"{group.name}：正在安装锁定的软件包（{lock.name}）")
    _run_uv(["pip", "install", "--python", str(group.python_exe), "--require-hashes", "--no-build",
             "-r", str(lock)], ctx, f"为 {group.name} 安装软件包", net=net)
    check = proc.run([group.python_exe, "-I", "-c", group.import_check], timeout=120)
    version = proc.run([group.python_exe, "-I", "-c", "import platform; print(platform.python_version())"], timeout=60)
    record = {
        "python": version.stdout.strip() if version.ok else None,
        "installed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "lock": lock_digest(lock),
        "lock_file": lock.name,
        "variant": variant,
        "import_ok": check.ok,
    }
    data = state.load()
    data.setdefault("envs", {})[group.name] = record
    state.save(data)
    if not check.ok:
        raise RunFailed(
            f"环境 {group.name} 已创建，但 `{group.import_check}` 失败："
            f"{(check.stderr or '').strip().splitlines()[-1:] or ['?']}",
            code="env_import_failed",
            hint=f"vidroute install --yes  （重新运行你的安装命令：损坏的 {group.name} "
                 "环境会被重建）",
        )
    return {"group": group.name, "status": "created", "path": str(group.path), **record}


def remove_env(group: EnvGroup) -> bool:
    existed = group.path.exists()
    if existed:
        shutil.rmtree(group.path, ignore_errors=os.name == "nt")
    data = state.load()
    data.get("envs", {}).pop(group.name, None)
    state.save(data)
    return existed
