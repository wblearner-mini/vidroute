"""Safe extraction of model archives (.tar.bz2/.tar.gz/.tar.xz/.zip).

Only regular files are extracted. Absolute paths, ``..`` components, links and
device files are rejected, so a malicious archive cannot write outside the
target directory (tarfile's own filters are not available on every supported
Python patch release, so the checks are explicit).
"""

from __future__ import annotations

import shutil
import tarfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Iterable

from vidroute.core.errors import RunFailed


class UnsafeArchive(RunFailed):
    default_code = "unsafe_archive"


def _safe_rel(name: str, strip_prefix: str) -> PurePosixPath | None:
    name = name.replace("\\", "/")
    if strip_prefix:
        if not name.startswith(strip_prefix):
            return None
        name = name[len(strip_prefix):]
    if not name or name.endswith("/"):
        return None
    rel = PurePosixPath(name)
    if rel.is_absolute() or name.startswith("/") or ":" in rel.parts[0] or any(p in ("..", "") for p in rel.parts):
        raise UnsafeArchive(f"拒绝不安全的压缩包成员路径：{name!r}")
    return rel


def extract(
    archive: Path,
    dest: Path,
    strip_prefix: str = "",
    members: Iterable[str] | None = None,
    optional: Iterable[str] = (),
) -> list[str]:
    """Extract (selected) regular files into ``dest``; returns the relative paths written.

    ``members`` must all be present; ``optional`` members are extracted when present.
    """
    required = set(members) if members is not None else None
    wanted = (required | set(optional)) if required is not None else None
    written: list[str] = []
    dest.mkdir(parents=True, exist_ok=True)
    name = archive.name.lower()
    if name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                rel = _safe_rel(info.filename, strip_prefix)
                if rel is None or (wanted is not None and str(rel) not in wanted):
                    continue
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise UnsafeArchive(f"拒绝压缩包中的符号链接：{info.filename!r}")
                target = dest.joinpath(*rel.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, target.open("wb") as out:
                    shutil.copyfileobj(src, out, 1024 * 1024)
                written.append(str(rel))
    else:
        with tarfile.open(archive, mode="r:*") as tf:
            for member in tf:
                if member.isdir():
                    continue
                rel = _safe_rel(member.name, strip_prefix)
                if rel is None or (wanted is not None and str(rel) not in wanted):
                    continue
                if not member.isfile():
                    raise UnsafeArchive(f"拒绝非普通文件的压缩包成员：{member.name!r}")
                target = dest.joinpath(*rel.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                src = tf.extractfile(member)
                if src is None:
                    raise UnsafeArchive(f"无法读取的压缩包成员：{member.name!r}")
                with src, target.open("wb") as out:
                    shutil.copyfileobj(src, out, 1024 * 1024)
                written.append(str(rel))
    if required is not None:
        missing = required - set(written)
        if missing:
            raise RunFailed(f"压缩包 {archive.name} 缺少预期的文件：{sorted(missing)}", code="archive_incomplete")
    return written
