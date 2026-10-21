"""uv, worker environments, installed models and reusable model caches."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any

from vidroute.core import paths, state
from vidroute.core.install.manifest import EnvGroup, Manifest, ModelEntry
from vidroute.core.util import proc

UV_VERSION = "0.13.4"  # pinned version vidroute installs into $VIDROUTE_HOME/runtimes/uv when absent


def uv_candidates() -> list[Path]:
    base = paths.runtimes_dir() / "uv"
    names = ["uv.exe", "uv"] if os.name == "nt" else ["uv"]
    managed = [base / sub / n for sub in ("bin", "Scripts", "") for n in names]
    # `pip install uv` into the same environment as vidroute puts uv next to python.
    beside = [Path(sys.executable).parent / n for n in names]
    return managed + beside


def find_uv() -> Path | None:
    for c in uv_candidates():
        if c.is_file():
            return c
    return proc.which("uv")


def uv_info(timeout: float = 5.0) -> dict[str, Any]:
    exe = find_uv()
    if exe is None:
        return {"found": False, "path": None, "version": None,
                "note": f"vidroute can install uv=={UV_VERSION} into {paths.runtimes_dir() / 'uv'} during `vidroute install`."}
    res = proc.run([exe, "--version"], timeout=timeout)
    version = None
    m = re.search(r"uv (\d+\.\d+\.\d+)", res.stdout)
    if m:
        version = m.group(1)
    return {"found": res.ok, "path": str(exe), "version": version, "managed": paths.is_within(exe, paths.home())}


def env_status(group: EnvGroup, check_import: bool = True, timeout: float = 5.0) -> dict[str, Any]:
    record = state.load().get("envs", {}).get(group.name, {})
    exists = group.python_exe.is_file()
    out: dict[str, Any] = {
        "path": str(group.path),
        "exists": exists,
        "python": record.get("python"),
        "installed_at": record.get("installed_at"),
        "lock": record.get("lock"),
        "variant": record.get("variant"),
        "import_ok": None,
    }
    if exists and check_import:
        res = proc.run([group.python_exe, "-I", "-c", group.import_check], timeout=timeout)
        out["import_ok"] = res.ok
        if not res.ok:
            if res.timed_out:
                out["import_error"] = "timed out"
            else:
                lines = (res.stderr or "").strip().splitlines()
                out["import_error"] = (lines[-1] if lines else "import failed")[:300]
    return out


def installed_models(manifest: Manifest) -> list[dict[str, Any]]:
    rows = []
    for m in manifest.models.values():
        if m.builtin:
            continue
        st = m.installed_status()
        if st["installed"] or st.get("installed_at"):
            rows.append({"id": m.id, "capability": m.capability, "engine": m.engine, **st})
    return rows


# ------------------------------------------------------------ reusable caches

_HF_RE = re.compile(r"^https://(?:huggingface\.co|hf-mirror\.com)/([^/]+)/([^/]+)/resolve/([^/]+)/$")


def hf_hub_dir() -> Path:
    if os.environ.get("HF_HUB_CACHE"):
        return Path(os.environ["HF_HUB_CACHE"])
    if os.environ.get("HF_HOME"):
        return Path(os.environ["HF_HOME"]) / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


def modelscope_dir() -> Path:
    if os.environ.get("MODELSCOPE_CACHE"):
        return Path(os.environ["MODELSCOPE_CACHE"])
    return Path.home() / ".cache" / "modelscope" / "hub"


def find_reusable(model: ModelEntry) -> dict[str, Any] | None:
    """Look for the exact files of ``model`` in a Hugging Face or ModelScope cache.

    Only per-file sources pinned to a revision qualify. Reuse still goes through
    sha256 verification at install time; this only avoids re-downloading.
    """
    required = [f for f in model.files if not f.optional]
    if not required:
        return None
    for src in model.sources:
        if src.kind != "files" or not src.base_url:
            continue
        m = _HF_RE.match(src.base_url)
        candidates: list[Path] = []
        if m:
            org, repo, rev = m.groups()
            candidates.append(hf_hub_dir() / f"models--{org}--{repo}" / "snapshots" / rev)
        elif src.type == "modelscope":
            mm = re.match(r"^https://www\.modelscope\.cn/models/([^/]+)/([^/]+)/resolve/[^/]+/(.*)$", src.base_url)
            if mm:
                org, repo, sub = mm.groups()
                for root in (modelscope_dir() / "models" / org / repo, modelscope_dir() / org / repo):
                    candidates.append(root / sub)
        for root in candidates:
            ok = True
            for f in required:
                p = root / src.layout.get(f.path, f.path)
                try:
                    if p.stat().st_size != f.size:
                        ok = False
                        break
                except OSError:
                    ok = False
                    break
            if ok:
                return {"id": model.id, "source": src.type, "path": str(root)}
    return None


def reusable_models(manifest: Manifest) -> list[dict[str, Any]]:
    out = []
    for m in manifest.models.values():
        if m.builtin or m.installed_status()["installed"]:
            continue
        hit = find_reusable(m)
        if hit:
            out.append(hit)
    return out
