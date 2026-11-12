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
    "allow_raw_video_handoff": (True, (bool,), "允许把原始视频交给 LLM 服务商的原生路线。"),
    "budget_fraction": (0.5, (int, float), "输入 token 预算占 LLM 上下文窗口的比例。"),
    "budget_tokens": (None, (int,), "输入 token 预算的绝对值；会覆盖 budget_fraction。"),
    "default_llm": (None, (str,), "省略 --llm 时使用的模型 id。"),
    "default_host": (None, (str,), "省略 --host 时使用的宿主 id。"),
    "mirror": ("auto", (str,), "下载镜像偏好：auto|cn|github|hf|modelscope。"),
    "pypi_index": (None, (str,), "worker 环境使用的软件包索引（PyPI 镜像 URL，或 'official'）。"),
    "python_mirror": (None, (str,), "uv 管理的 Python 下载镜像（URL，或 'official'）。"),
    "ffmpeg_source": ("auto", (str,), "Windows 上缺少 ffmpeg 时的安装方式：auto|winget|portable。"),
    "ffmpeg_path": (None, (str,), "ffmpeg 可执行文件的明确路径。"),
    "ffprobe_path": (None, (str,), "ffprobe 可执行文件的明确路径。"),
    "out_root": (None, (str,), "省略 --out 时的默认输出根目录。"),
    "offline": (False, (bool,), "从不访问网络（doctor 跳过镜像检查）。"),
    "delegate": (None, (dict,), "D5 委托端点配置（实验性）。"),
    "llm_profiles": ([], (list,), "用户的 LLM 档案覆盖（由 `vidroute llm set` 管理）。"),
    "host_profiles": ([], (list,), "用户的宿主档案覆盖（由 `vidroute host set` 管理）。"),
}

MIRRORS = ("auto", "cn", "github", "hf", "modelscope")
FFMPEG_SOURCES = ("auto", "winget", "portable")


def load() -> dict[str, Any]:
    data = read_json(paths.config_path(), default={})
    return data if isinstance(data, dict) else {}


def save(data: dict[str, Any]) -> None:
    paths.ensure_dir(paths.home())
    write_json_atomic(paths.config_path(), data)


def get(key: str) -> Any:
    if key not in KNOWN_KEYS:
        raise UsageError(f"未知配置项 {key!r}", hint="vidroute config get")
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
            f"未知配置项 {key!r}。已知的配置项：{', '.join(sorted(KNOWN_KEYS))}",
            hint="vidroute config get",
        )
    _, types, _ = KNOWN_KEYS[key]
    if value is not None and not isinstance(value, types):
        raise UsageError(f"配置项 {key!r} 应为 {'/'.join(t.__name__ for t in types)}，实际为 {value!r}")
    if isinstance(value, bool) and bool not in types:
        raise UsageError(f"配置项 {key!r} 不接受布尔值")
    if key == "mirror" and value not in MIRRORS:
        raise UsageError(f"mirror 必须是 {', '.join(MIRRORS)} 之一")
    if key == "ffmpeg_source" and value not in FFMPEG_SOURCES:
        raise UsageError(f"ffmpeg_source 必须是 {', '.join(FFMPEG_SOURCES)} 之一")
    if key in ("pypi_index", "python_mirror") and value not in (None, "official") and not str(value).startswith("https://"):
        raise UsageError(f"{key} 必须是 https:// URL 或 'official'")
    if key == "budget_fraction" and value is not None and not (0 < float(value) <= 1):
        raise UsageError("budget_fraction 必须在 (0, 1] 范围内")
    data = load()
    data[key] = value
    save(data)
    return data


def unset(key: str) -> dict[str, Any]:
    data = load()
    data.pop(key, None)
    save(data)
    return data
