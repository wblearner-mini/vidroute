"""User configuration in ``$VIDROUTE_HOME/config.json``.

Only known keys can be set (typos fail loudly). LLM/host overrides written by
``vidroute llm set`` / ``vidroute host set`` live under ``llm_profiles`` and
``host_profiles`` and take precedence over the bundled profiles.
"""

from __future__ import annotations

import json
from typing import Any

from vidroute.core import paths
from vidroute.core.errors import UsageError
from vidroute.core.util.jsonio import read_json, write_json_atomic

# key -> (default, accepted python types, description)
KNOWN_KEYS: dict[str, tuple[Any, tuple[type, ...], str]] = {
    "allow_raw_video_handoff": (True, (bool,), "Allow native routes that hand the raw video to the LLM provider."),
    "budget_fraction": (0.5, (int, float), "Input-token budget as a fraction of the LLM context window."),
    "budget_tokens": (None, (int,), "Absolute input-token budget; overrides budget_fraction."),
    "default_llm": (None, (str,), "Model id used when --llm is omitted."),
    "default_host": (None, (str,), "Host id used when --host is omitted."),
    "mirror": ("auto", (str,), "Download mirror preference: auto|github|hf|modelscope."),
    "ffmpeg_path": (None, (str,), "Explicit path to the ffmpeg executable."),
    "ffprobe_path": (None, (str,), "Explicit path to the ffprobe executable."),
    "out_root": (None, (str,), "Default output root when --out is omitted."),
    "offline": (False, (bool,), "Never touch the network (doctor skips mirror checks)."),
    "delegate": (None, (dict,), "D5 delegate endpoint configuration (experimental, Phase 7)."),
    "llm_profiles": ([], (list,), "User LLM profile overrides (managed by `vidroute llm set`)."),
    "host_profiles": ([], (list,), "User host profile overrides (managed by `vidroute host set`)."),
}

MIRRORS = ("auto", "github", "hf", "modelscope")


def load() -> dict[str, Any]:
    data = read_json(paths.config_path(), default={})
    return data if isinstance(data, dict) else {}


def save(data: dict[str, Any]) -> None:
    paths.ensure_dir(paths.home())
    write_json_atomic(paths.config_path(), data)


def get(key: str) -> Any:
    if key not in KNOWN_KEYS:
        raise UsageError(f"Unknown config key {key!r}", hint="vidroute config get")
    return load().get(key, KNOWN_KEYS[key][0])


def effective() -> dict[str, Any]:
    stored = load()
    return {k: stored.get(k, default) for k, (default, _, _) in KNOWN_KEYS.items()}


def parse_value(raw: str) -> Any:
    """CLI values are JSON when they parse as JSON, otherwise plain strings."""
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def set_value(key: str, value: Any) -> dict[str, Any]:
    if key not in KNOWN_KEYS:
        raise UsageError(
            f"Unknown config key {key!r}. Known keys: {', '.join(sorted(KNOWN_KEYS))}",
            hint="vidroute config get",
        )
    _, types, _ = KNOWN_KEYS[key]
    if value is not None and not isinstance(value, types):
        raise UsageError(f"Config key {key!r} expects {'/'.join(t.__name__ for t in types)}, got {value!r}")
    if isinstance(value, bool) and bool not in types:
        raise UsageError(f"Config key {key!r} does not accept booleans")
    if key == "mirror" and value not in MIRRORS:
        raise UsageError(f"mirror must be one of {', '.join(MIRRORS)}")
    if key == "budget_fraction" and value is not None and not (0 < float(value) <= 1):
        raise UsageError("budget_fraction must be in (0, 1]")
    data = load()
    data[key] = value
    save(data)
    return data


def unset(key: str) -> dict[str, Any]:
    data = load()
    data.pop(key, None)
    save(data)
    return data
