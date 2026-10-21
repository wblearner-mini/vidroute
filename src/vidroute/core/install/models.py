"""Install, verify and remove manifest models.

A model is assembled in a staging directory and swapped into
``$VIDROUTE_HOME/models/<id>`` only after every file passed its SHA-256 check;
a marker file records where it came from. Per-file sources are tried mirror by
mirror for each file; archive sources are downloaded, hash-checked, safely
extracted and then checked file by file again.
"""

from __future__ import annotations

import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from vidroute.core import paths
from vidroute.core.context import Ctx
from vidroute.core.errors import RunFailed
from vidroute.core.install.archive import extract
from vidroute.core.install.download import Downloader, verify_file
from vidroute.core.install.manifest import MARKER, Manifest, ModelEntry, Source
from vidroute.core.util.hashing import sha256_file
from vidroute.core.util.jsonio import read_json, write_json_atomic

MIRROR_TYPES = {"github": "github", "hf": "hf", "modelscope": "modelscope"}


def order_sources(entry: ModelEntry, mirror: str = "auto") -> list[Source]:
    sources = list(entry.sources)
    if mirror in MIRROR_TYPES:
        sources.sort(key=lambda s: 0 if s.type == MIRROR_TYPES[mirror] else 1)
    return sources


def _verify_dir(entry: ModelEntry, root: Path) -> list[str]:
    bad = []
    for f in entry.files:
        p = root / f.path
        if not p.exists():
            if not f.optional:
                bad.append(f"{f.path}: missing")
            continue
        if not verify_file(p, f.sha256, f.size):
            bad.append(f"{f.path}: sha256/size mismatch")
    return bad


def _swap_into_place(staging: Path, target: Path) -> None:
    old = target.with_name(target.name + ".old")
    if old.exists():
        shutil.rmtree(old, ignore_errors=True)
    if target.exists():
        os.replace(target, old)
    os.replace(staging, target)
    shutil.rmtree(old, ignore_errors=True)


def _from_reuse(entry: ModelEntry, reuse_path: Path, staging: Path, ctx: Ctx, layout: dict[str, str] | None = None) -> bool:
    """Copy verified files from a local directory (wheel-bundled assets or a model cache)."""
    layout = layout or {}
    for f in entry.files:
        candidate = reuse_path / layout.get(f.path, f.path)
        if not candidate.exists():
            if f.optional:
                continue
            return False
        ctx.progress("verify", None, None, f"{entry.id}/{f.path} (local cache)")
        if not verify_file(candidate, f.sha256, f.size):
            return False
        dest = staging / f.path
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(candidate, dest)
    return True


def bundled_dir(src: Source, manifest: Manifest) -> Path | None:
    """Directory of files shipped inside a package of a worker env (``type: bundled``)."""
    group = manifest.env_groups.get(src.env_group or "")
    if group is None or not group.python_exe.is_file() or not src.package:
        return None
    from vidroute.core.util import proc

    res = proc.run([group.python_exe, "-I", "-c",
                    "import importlib.util, os, sys; s = importlib.util.find_spec(sys.argv[1]); "
                    "print(os.path.dirname(s.origin) if s and s.origin else '')", src.package], timeout=60)
    if not res.ok or not res.stdout.strip():
        return None
    return Path(res.stdout.strip()) / src.subdir


def _from_files(entry: ModelEntry, sources: list[Source], staging: Path, dl: Downloader) -> None:
    for f in entry.files:
        urls = [s.file_url(f.path) for s in sources]
        try:
            dl.fetch(urls, staging / f.path, f.sha256, f.size, label=f"{entry.id}/{f.path}")
        except RunFailed:
            if f.optional:
                continue
            raise


def _from_archive(entry: ModelEntry, src: Source, staging: Path, dl: Downloader, ctx: Ctx) -> None:
    assert src.url and src.sha256
    archive = paths.downloads_dir() / Path(src.url).name
    dl.fetch([src.url], archive, src.sha256, src.size, label=f"{entry.id} ({archive.name})")
    ctx.progress("extract", None, None, archive.name)
    if entry.files:
        required = [f.path for f in entry.files if not f.optional]
        optional = [f.path for f in entry.files if f.optional]
        extract(archive, staging, src.strip_prefix, members=required, optional=optional)
    else:
        extract(archive, staging, src.strip_prefix)
    archive.unlink(missing_ok=True)


def install_model(
    entry: ModelEntry,
    manifest: Manifest,
    ctx: Ctx,
    mirror: str = "auto",
    reuse_path: str | None = None,
    downloader: Downloader | None = None,
    force: bool = False,
) -> dict[str, Any]:
    if entry.builtin:
        return {"id": entry.id, "status": "builtin"}
    target = entry.model_dir
    if not force and entry.installed_status()["installed"] and not _verify_dir(entry, target):
        return {"id": entry.id, "status": "already_installed", "path": str(target)}
    paths.ensure_dir(paths.models_dir())
    staging = paths.models_dir() / f".staging-{entry.id}"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    dl = downloader or Downloader(manifest.allowed_domains, ctx=ctx)
    used = None
    errors: list[str] = []
    try:
        bundled = [s for s in entry.sources if s.type == "bundled"]
        for src in bundled:
            where = bundled_dir(src, manifest)
            if where is not None and _from_reuse(entry, where, staging, ctx):
                used = f"bundled:{src.env_group}/{src.package}"
                break
        if used is None and reuse_path:
            cache_src = next((s for s in entry.sources if s.kind == "files" and s.type != "bundled"), None)
            if _from_reuse(entry, Path(reuse_path), staging, ctx, cache_src.layout if cache_src else None):
                used = f"reused:{reuse_path}"
        if used is None:
            ordered = [s for s in order_sources(entry, mirror) if s.type != "bundled"]
            file_sources = [s for s in ordered if s.kind == "files"]
            archive_sources = [s for s in ordered if s.kind == "archive"]
            # Respect the preferred mirror type: an archive first if it is the preferred kind.
            plan: list[tuple[str, Any]] = []
            for s in ordered:
                if s.kind == "archive":
                    plan.append(("archive", s))
                elif ("files", None) not in plan:
                    plan.append(("files", None))
            for kind, src in plan:
                ctx.check_cancelled()
                try:
                    if kind == "files":
                        _from_files(entry, file_sources, staging, dl)
                        used = ",".join(sorted({s.type for s in file_sources}))
                    else:
                        _from_archive(entry, src, staging, dl, ctx)
                        used = f"{src.type}:archive"
                    bad = _verify_dir(entry, staging)
                    if bad:
                        raise RunFailed(f"verification failed: {bad}", code="checksum_mismatch")
                    break
                except RunFailed as exc:
                    if exc.code == "cancelled":
                        raise
                    errors.append(f"{kind}: {exc.message}")
                    for child in staging.iterdir():
                        shutil.rmtree(child, ignore_errors=True) if child.is_dir() else child.unlink()
                    used = None
            if used is None:
                raise RunFailed(
                    f"Could not install {entry.id}: every source failed.",
                    code="download_failed",
                    hint=f"vidroute install --model {entry.id} --mirror hf   (re-run resumes partial downloads)",
                    data={"errors": errors},
                )
            del archive_sources
        write_json_atomic(
            staging / MARKER,
            {
                "id": entry.id,
                "installed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "source": used,
                "license": entry.license.spdx,
                "files": {f.path: f.sha256 for f in entry.files if (staging / f.path).exists()},
            },
        )
        _swap_into_place(staging, target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return {"id": entry.id, "status": "installed", "path": str(target), "source": used}


def verify_model(entry: ModelEntry) -> dict[str, Any]:
    if entry.builtin:
        return {"id": entry.id, "ok": True, "builtin": True}
    marker = read_json(entry.model_dir / MARKER)
    if not isinstance(marker, dict):
        return {"id": entry.id, "ok": False, "problems": ["not installed"]}
    problems = _verify_dir(entry, entry.model_dir)
    if not entry.files:  # archive-only experimental entries: check recorded file hashes
        for rel, digest in (marker.get("files") or {}).items():
            p = entry.model_dir / rel
            if not p.exists() or sha256_file(p) != digest:
                problems.append(f"{rel}: changed since install")
    return {"id": entry.id, "ok": not problems, "problems": problems, "path": str(entry.model_dir)}


def remove_model(entry: ModelEntry) -> bool:
    target = entry.model_dir
    if not target.exists():
        return False
    if not paths.is_within(target, paths.models_dir()):
        raise RunFailed(f"Refusing to delete outside {paths.models_dir()}: {target}")
    shutil.rmtree(target)
    return True
