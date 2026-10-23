"""Local model servers (Ollama, LM Studio) and download-mirror reachability.

Local servers are probed on 127.0.0.1 only. Mirror checks send a HEAD request
with no user data; they are skipped when ``offline`` is configured.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from vidroute import __version__

USER_AGENT = f"vidroute/{__version__}"
MIRRORS = {
    "github": "https://github.com/",
    "pypi": "https://pypi.org/simple/",
    "huggingface": "https://huggingface.co/",
    "hf-mirror": "https://hf-mirror.com/",
    "modelscope": "https://www.modelscope.cn/",
}

_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _get_json_local(url: str, timeout: float) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with _NO_PROXY_OPENER.open(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def ollama(timeout: float = 1.5) -> dict[str, Any]:
    try:
        data = _get_json_local("http://127.0.0.1:11434/api/tags", timeout)
    except (OSError, ValueError, urllib.error.URLError):
        return {"running": False, "models": []}
    names = [m.get("name") for m in data.get("models", []) if isinstance(m, dict)]
    return {"running": True, "url": "http://127.0.0.1:11434", "models": names[:50]}


def lmstudio(timeout: float = 1.5) -> dict[str, Any]:
    try:
        data = _get_json_local("http://127.0.0.1:1234/v1/models", timeout)
    except (OSError, ValueError, urllib.error.URLError):
        return {"running": False, "models": []}
    names = [m.get("id") for m in data.get("data", []) if isinstance(m, dict)]
    return {"running": True, "url": "http://127.0.0.1:1234/v1", "models": names[:50]}


def check_mirror(url: str, timeout: float = 4.0) -> dict[str, Any]:
    start = time.monotonic()
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
    except urllib.error.HTTPError as exc:  # reachable, just not happy with HEAD
        status = exc.code
    except (OSError, urllib.error.URLError) as exc:
        return {"ok": False, "ms": None, "error": str(getattr(exc, "reason", exc))[:200]}
    return {"ok": status < 500, "ms": int((time.monotonic() - start) * 1000), "status": status}
