"""Isolated worker environments built with uv from hash-locked requirement files.

Everything uv writes is redirected into ``$VIDROUTE_HOME``: the managed Python
(``runtimes/python``), uv's cache (``cache/uv``) and the environments
(``envs/<group>``). The Python install is never registered in the Windows
registry, user uv config files are ignored, and source builds are forbidden
(``--no-build``): worker environments only use prebuilt wheels.

Package indexes and mirrors are taken from the user's environment
(``UV_DEFAULT_INDEX``, ``UV_PYTHON_INSTALL_MIRROR`` ...); ``--require-hashes``
makes any mirror safe.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from vidroute.core import paths, state
from vidroute.core.context import Ctx
from vidroute.core.doctor.runtimes import UV_VERSION, find_uv
from vidroute.core.errors import NeedsInstall, RunFailed
from vidroute.core.install.manifest import EnvGroup
from vidroute.core.resources import data_file
from vidroute.core.util import proc

UV_TIMEOUT = 1800.0  # large wheels (CUDA libraries) on slow links


def uv_env() -> dict[str, str]:
    return {
        "UV_PYTHON_INSTALL_DIR": str(paths.runtimes_dir() / "python"),
        "UV_CACHE_DIR": str(paths.cache_dir() / "uv"),
        "UV_PYTHON_INSTALL_REGISTRY": "0",
        "UV_NO_CONFIG": "1",
        "UV_NO_PROGRESS": "1",
        "UV_LINK_MODE": "copy",
    }


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


def _run_uv(args: list[str], ctx: Ctx, what: str, timeout: float = UV_TIMEOUT) -> proc.Result:
    uv = find_uv()
    if uv is None:
        raise NeedsInstall("uv is required to create worker environments.", code="uv_missing",
                           hint="vidroute install  (it offers to install uv into $VIDROUTE_HOME)")
    ctx.check_cancelled()
    res = proc.run([uv, *args], timeout=timeout, env=uv_env())
    if not res.ok:
        tail = "\n".join((res.stderr or res.stdout).strip().splitlines()[-12:])
        raise RunFailed(f"{what} failed:\n{tail}" if tail else f"{what} failed (timed out)", code="env_failed",
                        hint="vidroute install --retry  (check network/proxy; set UV_DEFAULT_INDEX for a PyPI mirror)")
    return res


def bootstrap_uv(ctx: Ctx) -> Path:
    """Install a pinned uv into $VIDROUTE_HOME/runtimes/uv using pip of the current interpreter."""
    existing = find_uv()
    if existing:
        return existing
    target = paths.runtimes_dir() / "uv"
    lock = data_file("envs/uv.lock.txt")
    ctx.progress("uv", None, None, f"installing uv=={UV_VERSION} into {target}")
    res = proc.run(
        [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
         "--require-hashes", "--only-binary", ":all:", "--target", str(target), "-r", str(lock)],
        timeout=600,
    )
    uv = find_uv()
    if not res.ok or uv is None:
        raise NeedsInstall(
            "Could not install uv automatically (pip unavailable or offline).",
            code="uv_missing",
            hint="pipx install uv   (or: winget install astral-sh.uv / brew install uv)",
            data={"pip_error": (res.stderr or "").strip()[-600:]},
        )
    return uv


def create_env(group: EnvGroup, ctx: Ctx, variant: str = "cpu", force: bool = False) -> dict[str, Any]:
    if not force and env_ready(group, variant):
        return {"group": group.name, "status": "ready", "path": str(group.path)}
    lock = lock_path(group, variant)
    if group.path.exists():
        shutil.rmtree(group.path)
    ctx.progress("env", None, None, f"{group.name}: installing Python {group.python} (uv-managed)")
    _run_uv(["python", "install", group.python, "--no-registry", "--install-dir", str(paths.runtimes_dir() / "python")],
            ctx, f"Installing Python {group.python}")
    ctx.progress("env", None, None, f"{group.name}: creating environment")
    _run_uv(["venv", "--python", group.python, "--managed-python", "--no-project", str(group.path)], ctx,
            f"Creating environment {group.name}")
    ctx.progress("env", None, None, f"{group.name}: installing locked packages ({lock.name})")
    _run_uv(["pip", "install", "--python", str(group.python_exe), "--require-hashes", "--no-build",
             "-r", str(lock)], ctx, f"Installing packages for {group.name}")
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
            f"Environment {group.name} was created but `{group.import_check}` failed: "
            f"{(check.stderr or '').strip().splitlines()[-1:] or ['?']}",
            code="env_import_failed",
            hint=f"vidroute install --repair {group.name}",
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
