"""Loading and matching LLM / host profiles.

Sources, highest priority first: command-line flags > user config
(``vidroute llm set`` / ``host set``) > provider API cache (``vidroute llm
sync``) > bundled profiles > conservative defaults.
"""

from __future__ import annotations

import copy
import fnmatch
import os
import re
from functools import lru_cache
from typing import Any

from vidroute.core import config, paths
from vidroute.core.errors import DataFileError
from vidroute.core.resources import data_file
from vidroute.core.util.jsonio import read_json
from vidroute.core.util.yamlish import YamlError, load_file

MODALITIES = ("text", "image", "audio", "video")


def _load(rel: str, key: str, schema: str) -> list[dict[str, Any]]:
    path = data_file(rel)
    try:
        raw = load_file(path)
    except YamlError as exc:
        raise DataFileError(str(exc)) from exc
    if not isinstance(raw, dict) or raw.get("schema") != schema or not isinstance(raw.get(key), list):
        raise DataFileError(f"{path}: expected schema {schema!r} with a '{key}' list")
    return raw[key]


@lru_cache(maxsize=1)
def builtin_llm_profiles() -> tuple[dict[str, Any], ...]:
    profiles = _load("routing/llm-profiles.yaml", "profiles", "vidroute/llm-profiles/1")
    for p in profiles:
        bad = set(p.get("modalities_in", [])) - set(MODALITIES)
        if bad or "text" not in p.get("modalities_in", []):
            raise DataFileError(f"llm profile {p.get('id')}: bad modalities_in {p.get('modalities_in')}")
    return tuple(profiles)


@lru_cache(maxsize=1)
def builtin_host_profiles() -> tuple[dict[str, Any], ...]:
    return tuple(_load("routing/host-profiles.yaml", "hosts", "vidroute/host-profiles/1"))


# ------------------------------------------------------------------ matching


def normalize_model_id(model_id: str) -> list[str]:
    """Candidate spellings: as given, without ``provider/`` prefixes, without ``~`` or ``:tags``."""
    raw = model_id.strip().lower().lstrip("~")
    cands = [raw]
    if "/" in raw:
        cands.append(raw.rsplit("/", 1)[1])
    for c in list(cands):
        if ":" in c:
            cands.append(c.split(":", 1)[0])  # openrouter ":batch", bedrock ":0"
    return list(dict.fromkeys(cands))


def _specificity(pattern: str) -> float:
    literal = re.sub(r"\[[^\]]*\]", "#", pattern)
    return len(literal.replace("*", "").replace("?", "")) + 0.5 * pattern.count("?")


def match_llm(model_id: str, profiles: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> tuple[dict[str, Any] | None, str | None]:
    """Most specific glob match across ``id`` and ``aliases``; returns (profile, pattern)."""
    best: tuple[float, dict[str, Any], str] | None = None
    for cand in normalize_model_id(model_id):
        for prof in profiles:
            for pattern in [prof["id"], *prof.get("aliases", [])]:
                pat = pattern.lower()
                if fnmatch.fnmatchcase(cand, pat):
                    score = _specificity(pat)
                    if best is None or score > best[0]:
                        best = (score, prof, pattern)
    return (best[1], best[2]) if best else (None, None)


def find_host(host_id: str, hosts: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> dict[str, Any] | None:
    key = host_id.strip().lower()
    for h in hosts:
        if key == h["id"] or key in [a.lower() for a in h.get("aliases", [])]:
            return h
    return None


# ------------------------------------------------------------- user overrides


def user_llm_profiles() -> list[dict[str, Any]]:
    return [p for p in (config.get("llm_profiles") or []) if isinstance(p, dict) and p.get("id")]


def user_host_profiles() -> list[dict[str, Any]]:
    return [h for h in (config.get("host_profiles") or []) if isinstance(h, dict) and h.get("id")]


def provider_cache_path():
    return paths.cache_dir() / "provider-models.json"


def provider_lookup(model_id: str) -> dict[str, Any] | None:
    """Modalities/context from a cached provider listing (OpenRouter format), if the user synced one."""
    data = read_json(provider_cache_path())
    if not isinstance(data, dict):
        return None
    index = data.get("models") or {}
    for cand in normalize_model_id(model_id):
        hit = index.get(cand)
        if hit:
            return {**hit, "provider": data.get("provider"), "fetched_at": data.get("fetched_at")}
    return None


def deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


# ------------------------------------------------------------ host detection

HOST_ENV_MARKERS = (
    ("QWEN_CODE_SESSION_ID", "qwen-code"),
    ("QWEN_CODE_PROJECT_DIR", "qwen-code"),
    ("CLAUDECODE", "claude-code"),
    ("GEMINI_CLI", "gemini-cli"),
    ("CODEX_SANDBOX", "codex-cli"),
)


def detect_host() -> tuple[str | None, str | None]:
    """Best-effort guess from environment markers hosts set for commands they run."""
    for var, host in HOST_ENV_MARKERS:
        if os.environ.get(var):
            return host, var
    return None, None
