"""Known-vulnerability check for every locked Python package, via the OSV database (osv.dev).

Usage:
    python tools/vuln_check.py envs/*.lock.txt requirements-dev.lock.txt

Exit code 1 when a pinned version has a known vulnerability that is not listed
(with a reason) in ``tools/vuln-ignore.txt``. Standard library only.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

PIN = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s;\\]+)")
OSV_BATCH = "https://api.osv.dev/v1/querybatch"
IGNORE_FILE = Path(__file__).with_name("vuln-ignore.txt")


def pins(paths: list[Path]) -> list[tuple[str, str]]:
    seen: dict[tuple[str, str], None] = {}
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            m = PIN.match(line)
            if m:
                seen[(m.group(1).lower(), m.group(2))] = None
    return list(seen)


def ignored() -> set[str]:
    if not IGNORE_FILE.exists():
        return set()
    out = set()
    for line in IGNORE_FILE.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.add(line.split()[0])
    return out


def main(argv: list[str] | None = None) -> int:
    paths = [Path(p) for p in (argv if argv is not None else sys.argv[1:])]
    if not paths:
        sys.stderr.write(__doc__ or "")
        return 2
    packages = pins(paths)
    body = json.dumps({"queries": [{"package": {"name": n, "ecosystem": "PyPI"}, "version": v}
                                   for n, v in packages]}).encode("utf-8")
    req = urllib.request.Request(OSV_BATCH, data=body, headers={"Content-Type": "application/json",
                                                               "User-Agent": "vidroute-vuln-check"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        results = json.load(resp)["results"]
    skip = ignored()
    found = []
    for (name, version), res in zip(packages, results):
        ids = [v["id"] for v in res.get("vulns") or [] if v["id"] not in skip]
        if ids:
            found.append(f"{name}=={version}: {', '.join(ids)}")
    print(f"checked {len(packages)} pinned packages against osv.dev")
    if found:
        print("known vulnerabilities (see https://osv.dev/vulnerability/<id>):")
        print("  " + "\n  ".join(found))
        return 1
    print("no known vulnerabilities")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
