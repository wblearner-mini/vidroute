#!/usr/bin/env python3
"""Lists bundled LLM and host profiles whose ``verified_at`` is older than STALE_AFTER_DAYS.

Exit code 1 when any is stale, so the scheduled workflow fails and the maintainer re-checks them
against official docs. Profiles with ``verified_at: null`` are already marked unverified and are skipped.
Run: python -I tools/profile_freshness.py
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from vidroute.core.llm import profiles as P  # noqa: E402


def main() -> int:
    stale = [(kind, p["id"], p["verified_at"], age)
             for kind, items in (("llm", P.builtin_llm_profiles()), ("host", P.builtin_host_profiles()))
             for p in items if (age := P.stale_age_days(p.get("verified_at")))]
    for kind, pid, date, age in stale:
        print(f"{kind:4} {pid:30} verified {date} ({age} days ago)")
    if stale:
        print(f"{len(stale)} profile(s) older than {P.STALE_AFTER_DAYS} days: re-verify them against official docs.")
        return 1
    print(f"All dated profiles were verified within {P.STALE_AFTER_DAYS} days.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
