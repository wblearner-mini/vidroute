"""Portable tools in ``$VIDROUTE_HOME/runtimes`` — today a pinned FFmpeg build for Windows.

Used when the system has no ffmpeg and winget is unavailable or fails (or the
user prefers it: ``vidroute config set ffmpeg_source portable``). The archive
and every extracted file are checked against the manifest's SHA-256; nothing is
added to PATH, because :mod:`vidroute.core.util.ffmpeg` looks in
``runtimes/ffmpeg/bin`` itself.
"""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from typing import Any

from vidroute.core import paths
from vidroute.core.context import Ctx
from vidroute.core.doctor.system import platform_key
from vidroute.core.errors import RunFailed
from vidroute.core.install.archive import extract
from vidroute.core.install.download import Downloader, verify_file
from vidroute.core.install.manifest import MARKER, Manifest, RuntimeEntry
from vidroute.core.util.jsonio import write_json_atomic


def for_platform(manifest: Manifest, tool: str = "ffmpeg", key: str | None = None) -> RuntimeEntry | None:
    key = key or platform_key()
    return next((r for r in manifest.runtimes.values() if r.tool == tool and key in r.platforms), None)


def install(entry: RuntimeEntry, manifest: Manifest, ctx: Ctx, downloader: Downloader | None = None) -> dict[str, Any]:
    if entry.installed_status()["installed"]:
        return {"id": entry.id, "status": "already_installed", "path": str(entry.path)}
    downloader = downloader or Downloader(manifest.allowed_domains, ctx=ctx)
    source = next(s for s in entry.sources if s.kind == "archive")
    assert source.url and source.sha256
    archive = paths.downloads_dir() / f"{source.sha256}-{source.url.rsplit('/', 1)[-1]}"
    ctx.progress("download", None, None, f"{entry.id} ({entry.size_mb:.0f} MB)")
    downloader.fetch([source.url], archive, source.sha256, source.size, label=entry.id)
    staging = entry.path.with_name(entry.path.name + ".partial")
    shutil.rmtree(staging, ignore_errors=True)
    ctx.progress("extract", None, None, entry.id)
    extract(archive, staging, strip_prefix=source.strip_prefix, members=[f.path for f in entry.files])
    bad = [f.path for f in entry.files if not verify_file(staging / f.path, f.sha256, f.size)]
    if bad:
        shutil.rmtree(staging, ignore_errors=True)
        raise RunFailed(f"{entry.id}：解压出的文件与清单不符：{', '.join(bad)}",
                        code="checksum_mismatch", hint="vidroute install --yes   （会重新下载压缩包）")
    write_json_atomic(staging / MARKER, {"id": entry.id, "version": entry.version, "source": source.url,
                                         "installed_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    old = entry.path.with_name(entry.path.name + ".old")
    shutil.rmtree(old, ignore_errors=True)
    if entry.path.exists():
        entry.path.rename(old)
    staging.rename(entry.path)
    shutil.rmtree(old, ignore_errors=True)
    archive.unlink(missing_ok=True)  # 110 MB the user does not need twice
    return {"id": entry.id, "status": "installed", "path": str(entry.path), "version": entry.version}
