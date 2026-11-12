"""Where uv and pip fetch packages and the Python runtime from, and which model mirror goes first.

Worker environments are hash-locked (``--require-hashes``) and uv checks every
Python download against the SHA-256 it ships with, so a mirror can only make an
install faster, never change what gets installed (docs/DECISIONS.md D-042).

``auto`` keeps the official sources and switches to well-known mirrors only
when the official one does not answer, or when the network looks like mainland
China: huggingface.co unreachable while hf-mirror.com or ModelScope answer.
``cn`` forces the mirrors. Anything the user configured — ``UV_DEFAULT_INDEX``,
``UV_INDEX_URL``, ``PIP_INDEX_URL``, ``UV_PYTHON_INSTALL_MIRROR`` or the config
keys ``pypi_index`` / ``python_mirror`` (``official`` disables mirrors) — wins.
"""

from __future__ import annotations

import os
from typing import Any, Callable

from vidroute.core import config
from vidroute.core.doctor.external import check_mirror
from vidroute.core.util.proc import run_parallel

PYPI_OFFICIAL = "https://pypi.org/simple/"
PYTHON_OFFICIAL = "https://releases.astral.sh/"
PYPI_MIRRORS = ("https://mirrors.aliyun.com/pypi/simple/", "https://pypi.tuna.tsinghua.edu.cn/simple/")
PYTHON_MIRRORS = ("https://registry.npmmirror.com/-/binary/python-build-standalone/",)
CN_SIGNALS = {"huggingface": "https://huggingface.co/", "hf-mirror": "https://hf-mirror.com/",
              "modelscope": "https://www.modelscope.cn/"}
INDEX_ENV = ("UV_DEFAULT_INDEX", "UV_INDEX_URL", "PIP_INDEX_URL")
PYTHON_ENV = ("UV_PYTHON_INSTALL_MIRROR",)
PROBE_TIMEOUT = 3.0
MODEL_MIRRORS = ("github", "hf", "modelscope")

Probe = Callable[[str, float], dict[str, Any]]


def _user_setting(env_keys: tuple[str, ...], config_key: str) -> tuple[str | None, str | None]:
    for key in env_keys:
        if os.environ.get(key):
            return os.environ[key], f"${key}"
    value = config.get(config_key)
    return (str(value), f"config {config_key}") if value else (None, None)


def _probe_all(urls: dict[str, str], probe: Probe) -> dict[str, bool]:
    results = run_parallel({name: (lambda u=url: probe(u, PROBE_TIMEOUT)) for name, url in urls.items()},
                           timeout=PROBE_TIMEOUT + 2)
    return {name: bool(r.ok and isinstance(r.value, dict) and r.value.get("ok")) for name, r in results.items()}


def resolve(mirror: str = "auto", need_packages: bool = True, need_python: bool = True, need_models: bool = True,
            probe: Probe | None = None) -> dict[str, Any]:
    """Decide the package index, Python mirror and model-mirror preference for one install."""
    probe = probe or check_mirror
    out: dict[str, Any] = {"mode": mirror, "region": None, "pypi_index": None, "python_mirror": None,
                           "model_mirror": mirror if mirror in MODEL_MIRRORS else "auto", "reasons": []}
    user_index, index_src = _user_setting(INDEX_ENV, "pypi_index")
    user_python, python_src = _user_setting(PYTHON_ENV, "python_mirror")
    want_index = need_packages and user_index is None
    want_python = need_python and user_python is None
    want_models = need_models and out["model_mirror"] == "auto"
    up: dict[str, bool] = {}
    if mirror == "cn":
        out["region"] = "cn"
        out["reasons"].append("--mirror cn：使用中国大陆镜像")
    elif mirror == "auto" and (want_index or want_python or want_models) and not config.get("offline"):
        urls = dict(CN_SIGNALS)
        if want_index:
            urls.update({"pypi": PYPI_OFFICIAL, **{f"pypi-mirror-{i}": u for i, u in enumerate(PYPI_MIRRORS)}})
        if want_python:
            urls.update({"python": PYTHON_OFFICIAL, **{f"python-mirror-{i}": u for i, u in enumerate(PYTHON_MIRRORS)}})
        up = _probe_all(urls, probe)
        if not up.get("huggingface") and (up.get("hf-mirror") or up.get("modelscope")):
            out["region"] = "cn"
            out["reasons"].append("huggingface.co 不通，而 hf-mirror.com/ModelScope 有响应："
                                  "使用中国大陆镜像")

    def pick(mirrors: tuple[str, ...], prefix: str) -> str | None:
        if not up:  # forced, nothing probed
            return mirrors[0]
        return next((u for i, u in enumerate(mirrors) if up.get(f"{prefix}-mirror-{i}")), None)

    if user_index is not None:
        out["pypi_index"] = None if user_index == "official" else user_index
        out["reasons"].append(f"软件包索引来自 {index_src}")
    elif want_index and (out["region"] == "cn" or (up and not up.get("pypi"))):
        out["pypi_index"] = pick(PYPI_MIRRORS, "pypi")
        if out["region"] != "cn" and out["pypi_index"]:
            out["reasons"].append("pypi.org 不通：使用 PyPI 镜像")
    if user_python is not None:
        out["python_mirror"] = None if user_python == "official" else user_python
        out["reasons"].append(f"Python 运行时镜像来自 {python_src}")
    elif want_python and (out["region"] == "cn" or (up and not up.get("python"))):
        out["python_mirror"] = pick(PYTHON_MIRRORS, "python")
        if out["region"] != "cn" and out["python_mirror"]:
            out["reasons"].append("releases.astral.sh 不通：使用 Python 运行时镜像")
    if want_models and out["region"] == "cn":
        out["model_mirror"] = "cn"
    out["user_index"] = user_index is not None
    out["user_python_mirror"] = user_python is not None
    return out


def uv_env(net: dict[str, Any] | None) -> dict[str, str]:
    """Environment for uv: only what vidroute chose; the user's own variables are inherited anyway."""
    if not net:
        return {}
    env = {}
    if net.get("pypi_index") and not any(os.environ.get(k) for k in INDEX_ENV):
        env["UV_DEFAULT_INDEX"] = net["pypi_index"]
    if net.get("python_mirror") and not os.environ.get("UV_PYTHON_INSTALL_MIRROR"):
        env["UV_PYTHON_INSTALL_MIRROR"] = net["python_mirror"]
    return env


def pip_args(net: dict[str, Any] | None) -> list[str]:
    if net and net.get("pypi_index") and not os.environ.get("PIP_INDEX_URL"):
        return ["--index-url", net["pypi_index"]]
    return []


def describe(net: dict[str, Any] | None) -> str | None:
    if not net or not (net.get("pypi_index") or net.get("python_mirror") or net.get("model_mirror") != "auto"):
        return None
    parts = []
    if net.get("pypi_index"):
        parts.append(f"软件包来自 {net['pypi_index']}")
    if net.get("python_mirror"):
        parts.append(f"Python 来自 {net['python_mirror']}")
    if net.get("model_mirror") == "cn":
        parts.append("模型优先来自 ModelScope / hf-mirror.com")
    elif net.get("model_mirror") not in (None, "auto"):
        parts.append(f"模型优先来自 {net['model_mirror']}")
    why = f"（{'；'.join(net['reasons'])}）" if net.get("reasons") else ""
    return "网络：" + "，".join(parts) + why
