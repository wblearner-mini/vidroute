"""Machine state in ``$VIDROUTE_HOME/state.json``: cached probe results and install records."""

from __future__ import annotations

import time
from typing import Any

from vidroute.core import paths
from vidroute.core.util.jsonio import read_json, write_json_atomic


def load() -> dict[str, Any]:
    data = read_json(paths.state_path(), default={})
    return data if isinstance(data, dict) else {}


def save(data: dict[str, Any]) -> None:
    paths.ensure_dir(paths.home())
    write_json_atomic(paths.state_path(), data)


def update(key: str, value: Any) -> None:
    data = load()
    data[key] = value
    save(data)


def cache_get(key: str, ttl_seconds: float) -> Any:
    entry = load().get("cache", {}).get(key)
    if not isinstance(entry, dict):
        return None
    if time.time() - float(entry.get("ts", 0)) > ttl_seconds:
        return None
    return entry.get("value")


def cache_put(key: str, value: Any) -> None:
    data = load()
    cache = data.setdefault("cache", {})
    cache[key] = {"ts": time.time(), "value": value}
    save(data)


def cache_clear(key: str | None = None) -> None:
    data = load()
    if key is None:
        data.pop("cache", None)
    else:
        data.get("cache", {}).pop(key, None)
    save(data)
