"""License report for every locked Python package (worker envs and dev tools).

Usage:
    python tools/license_report.py envs/*.lock.txt            # Markdown table on stdout
    python tools/license_report.py --check envs/*.lock.txt    # exit 1 on a license outside the allow-list

Licenses come from PyPI metadata (PEP 639 ``license_expression``, else the
``License ::`` classifiers, else the free-text ``license`` field). Standard
library only; used in CI and to refresh THIRD_PARTY_LICENSES.md.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

PIN = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s;\\]+)")

# Permissive and weak-copyleft licenses that are fine for a MIT project that does not redistribute them.
ALLOWED = {
    "MIT", "MIT-CMU", "BSD-2-Clause", "BSD-3-Clause", "Apache-2.0", "ISC", "PSF-2.0", "Python-2.0",
    "MPL-2.0", "LGPL-2.1-or-later", "LGPL-3.0-or-later", "Unlicense", "0BSD", "MIT-0", "Zlib", "HPND", "CC0-1.0",
    "Apache-2.0 OR BSD-3-Clause", "Apache-2.0 OR MIT", "MIT OR Apache-2.0", "BSD-3-Clause OR Apache-2.0",
}
CLASSIFIERS = {
    "MIT License": "MIT", "BSD License": "BSD-3-Clause", "Apache Software License": "Apache-2.0",
    "ISC License (ISCL)": "ISC", "Python Software Foundation License": "PSF-2.0",
    "Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0", "The Unlicense (Unlicense)": "Unlicense",
    "Historical Permission Notice and Disclaimer (HPND)": "HPND", "zlib/libpng License": "Zlib",
}
TEXT = {"3-Clause BSD License": "BSD-3-Clause", "BSD 3-Clause": "BSD-3-Clause", "Apache 2.0": "Apache-2.0"}
# Reviewed by hand: packages whose metadata is missing or not SPDX.
OVERRIDES = {
    "nvidia-cublas-cu12": "LicenseRef-NVIDIA-Software-License (optional CUDA path only)",
    "nvidia-cuda-nvrtc-cu12": "LicenseRef-NVIDIA-Software-License (optional CUDA path only)",
    "nvidia-cudnn-cu12": "LicenseRef-NVIDIA-cuDNN-SLA (optional CUDA path only)",
    # metadata says "Apache licensed, as found in the LICENSE file"; the repository LICENSE is Apache-2.0
    "sherpa-onnx": "Apache-2.0",
}
REVIEWED = {v for v in OVERRIDES.values() if v not in ALLOWED}


def pins(paths: list[Path]) -> dict[str, tuple[str, set[str]]]:
    out: dict[str, tuple[str, set[str]]] = {}
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            m = PIN.match(line)
            if m:
                name = m.group(1).lower()
                ver, used = out.get(name, (m.group(2), set()))
                used.add(path.name.replace(".lock.txt", ""))
                out[name] = (ver, used)
    return out


def pypi_license(name: str, version: str) -> tuple[str, str]:
    if name in OVERRIDES:
        return OVERRIDES[name], ""
    url = f"https://pypi.org/pypi/{name}/{version}/json"
    req = urllib.request.Request(url, headers={"User-Agent": "vidroute-license-report"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        info = json.load(resp)["info"]
    expr = (info.get("license_expression") or "").strip()
    if expr:
        return expr, info.get("project_url") or ""
    found = [CLASSIFIERS.get(c.split(" :: ")[-1], c.split(" :: ")[-1])
             for c in info.get("classifiers") or [] if c.startswith("License ::") and c != "License :: OSI Approved"]
    if found:
        return " OR ".join(dict.fromkeys(found)), info.get("project_url") or ""
    text = (info.get("license") or "").strip().splitlines()[0] if info.get("license") else "UNKNOWN"
    return TEXT.get(text, text[:60]), info.get("project_url") or ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("locks", nargs="+", type=Path)
    ap.add_argument("--check", action="store_true", help="fail on licenses outside the allow-list")
    args = ap.parse_args(argv)
    table = pins(args.locks)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = dict(zip(table, pool.map(lambda n: pypi_license(n, table[n][0]), table)))
    bad = []
    print("| 包 | 版本 | 许可 | 用于 |")
    print("|---|---|---|---|")
    for name in sorted(table):
        ver, used = table[name]
        lic, _ = results[name]
        normal = lic.replace("Apache Software License", "Apache-2.0")
        ok = normal in ALLOWED or lic in REVIEWED or all(part.strip() in ALLOWED for part in re.split(r"\s+(?:OR|AND)\s+", normal))
        if not ok:
            bad.append(f"{name}=={ver}: {lic}")
        print(f"| {name} | {ver} | {lic} | {', '.join(sorted(used))} |")
    if bad:
        sys.stderr.write("Licenses needing review:\n  " + "\n  ".join(bad) + "\n")
        return 1 if args.check else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
