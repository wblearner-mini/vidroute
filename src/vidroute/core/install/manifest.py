"""Typed access to ``models/manifest.yaml`` with validation and installed-state helpers."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from vidroute.core import paths
from vidroute.core.errors import DataFileError
from vidroute.core.resources import data_file
from vidroute.core.util.jsonio import read_json
from vidroute.core.util.yamlish import YamlError, load_file

MARKER = ".vidroute-installed.json"
CAPABILITIES = ("asr", "vad", "ocr", "scene", "vlm")


@dataclass(frozen=True)
class ModelFile:
    path: str
    size: int
    sha256: str
    optional: bool = False


@dataclass(frozen=True)
class Source:
    type: str
    kind: str
    base_url: str | None = None
    url: str | None = None
    size: int | None = None
    sha256: str | None = None
    strip_prefix: str = ""
    layout: dict[str, str] = field(default_factory=dict)
    env_group: str | None = None  # type "bundled": files shipped inside a hash-locked wheel
    package: str | None = None
    subdir: str = ""

    def file_url(self, rel: str) -> str:
        assert self.base_url is not None
        remote = self.layout.get(rel, rel)
        return self.base_url + remote


@dataclass(frozen=True)
class License:
    spdx: str
    restricted: bool
    gated: bool
    name: str
    url: str
    notes: str = ""

    @property
    def needs_acceptance(self) -> bool:
        return self.restricted or self.gated


@dataclass(frozen=True)
class ModelEntry:
    id: str
    capability: str
    engine: str
    env_group: str | None
    package: str
    status: str
    languages: tuple[str, ...]
    size_mb: float
    tiers: tuple[str, ...]
    license: License
    files: tuple[ModelFile, ...]
    sources: tuple[Source, ...]
    min_ram_gb: float | None = None
    min_vram_gb: float | None = None
    requires_gpu: str | None = None
    timestamps: str | None = None
    smoke_test: str | None = None
    whisper_prefix: str | None = None
    verified_at: str | None = None
    notes: str = ""

    @property
    def builtin(self) -> bool:
        return self.package == "builtin"

    @property
    def model_dir(self) -> Path:
        return paths.models_dir() / self.id

    def supports_language(self, lang: str | None) -> bool:
        if not lang or lang == "auto":
            return True
        langs = set(self.languages)
        if langs & {"multi", "any"}:
            return True
        base = lang.lower().split("-")[0]
        return lang.lower() in langs or base in langs

    def installed_status(self) -> dict[str, Any]:
        """Fast check (no hashing): marker present and every required file has the right size."""
        if self.builtin:
            return {"installed": True, "builtin": True}
        marker = read_json(self.model_dir / MARKER)
        missing = []
        for f in self.files:
            p = self.model_dir / f.path
            try:
                ok = p.stat().st_size == f.size
            except OSError:
                ok = False
            if not ok and not f.optional:
                missing.append(f.path)
        if not self.files:  # archive-only experimental entries: trust the marker
            installed = isinstance(marker, dict)
        else:
            installed = isinstance(marker, dict) and not missing
        return {
            "installed": installed,
            "path": str(self.model_dir),
            "missing": missing if marker else [f.path for f in self.files if not f.optional],
            "installed_at": marker.get("installed_at") if isinstance(marker, dict) else None,
        }


@dataclass(frozen=True)
class EnvGroup:
    name: str
    description: str
    python: str
    lock: str
    import_check: str
    lock_cuda: str | None = None
    approx_size_mb: float | None = None
    approx_size_mb_cuda: float | None = None

    @property
    def path(self) -> Path:
        return paths.envs_dir() / self.name

    @property
    def python_exe(self) -> Path:
        if os.name == "nt":
            return self.path / "Scripts" / "python.exe"
        return self.path / "bin" / "python"


@dataclass(frozen=True)
class Manifest:
    allowed_domains: tuple[str, ...]
    env_groups: dict[str, EnvGroup]
    models: dict[str, ModelEntry]

    def model(self, model_id: str) -> ModelEntry:
        try:
            return self.models[model_id]
        except KeyError:
            from vidroute.core.errors import NotFoundError

            raise NotFoundError(f"Unknown model id {model_id!r}", hint="vidroute models list") from None

    def by_capability(self, capability: str) -> list[ModelEntry]:
        return [m for m in self.models.values() if m.capability == capability]

    def domain_allowed(self, host: str) -> bool:
        host = host.lower().rstrip(".")
        return any(host == d or host.endswith("." + d) for d in self.allowed_domains)


def _req(obj: dict[str, Any], key: str, where: str) -> Any:
    if key not in obj:
        raise DataFileError(f"{where}: missing required field {key!r}")
    return obj[key]


def parse_manifest(raw: Any, source: str = "manifest.yaml") -> Manifest:
    if not isinstance(raw, dict) or raw.get("schema") != "vidroute/manifest/1":
        raise DataFileError(f"{source}: expected schema 'vidroute/manifest/1'")
    groups: dict[str, EnvGroup] = {}
    for name, g in (raw.get("env_groups") or {}).items():
        where = f"{source}: env_groups.{name}"
        groups[name] = EnvGroup(
            name=name,
            description=str(g.get("description", "")),
            python=str(_req(g, "python", where)),
            lock=str(_req(g, "lock", where)),
            import_check=str(_req(g, "import_check", where)),
            lock_cuda=g.get("lock_cuda"),
            approx_size_mb=g.get("approx_size_mb"),
            approx_size_mb_cuda=g.get("approx_size_mb_cuda"),
        )
    models: dict[str, ModelEntry] = {}
    for m in _req(raw, "models", source):
        mid = _req(m, "id", f"{source}: models[]")
        where = f"{source}: model {mid}"
        if mid in models:
            raise DataFileError(f"{where}: duplicate id")
        cap = _req(m, "capability", where)
        if cap not in CAPABILITIES:
            raise DataFileError(f"{where}: unknown capability {cap!r}")
        env_group = m.get("env_group")
        if env_group is not None and env_group not in groups:
            raise DataFileError(f"{where}: unknown env_group {env_group!r}")
        lic = _req(m, "license", where)
        models[mid] = ModelEntry(
            id=mid,
            capability=cap,
            engine=str(_req(m, "engine", where)),
            env_group=env_group,
            package=str(_req(m, "package", where)),
            status=str(_req(m, "status", where)),
            languages=tuple(_req(m, "languages", where)),
            size_mb=float(_req(m, "size_mb", where)),
            tiers=tuple(_req(m, "tiers", where)),
            license=License(
                spdx=str(lic["spdx"]),
                restricted=bool(lic["restricted"]),
                gated=bool(lic["gated"]),
                name=str(lic.get("name", lic["spdx"])),
                url=str(lic.get("url", "")),
                notes=str(lic.get("notes") or ""),
            ),
            files=tuple(
                ModelFile(str(f["path"]), int(f["size"]), str(f["sha256"]).lower(), bool(f.get("optional", False)))
                for f in m.get("files") or []
            ),
            sources=tuple(
                Source(
                    type=str(s["type"]),
                    kind=str(s["kind"]),
                    base_url=s.get("base_url"),
                    url=s.get("url"),
                    size=s.get("size"),
                    sha256=(s.get("sha256") or None) and str(s["sha256"]).lower(),
                    strip_prefix=str(s.get("strip_prefix") or ""),
                    layout=dict(s.get("layout") or {}),
                    env_group=s.get("env_group"),
                    package=s.get("package"),
                    subdir=str(s.get("subdir") or ""),
                )
                for s in m.get("sources") or []
            ),
            min_ram_gb=m.get("min_ram_gb"),
            min_vram_gb=m.get("min_vram_gb"),
            requires_gpu=m.get("requires_gpu"),
            timestamps=m.get("timestamps"),
            smoke_test=m.get("smoke_test"),
            whisper_prefix=m.get("whisper_prefix"),
            verified_at=m.get("verified_at"),
            notes=str(m.get("notes") or ""),
        )
    domains = tuple(str(d).lower() for d in _req(raw, "allowed_domains", source))
    return Manifest(allowed_domains=domains, env_groups=groups, models=models)


@lru_cache(maxsize=1)
def load_manifest() -> Manifest:
    path = data_file("models/manifest.yaml")
    try:
        raw = load_file(path)
    except YamlError as exc:
        raise DataFileError(str(exc)) from exc
    return parse_manifest(raw, source=str(path))
