"""Allow ``python -m vidroute`` (useful on Windows where console scripts may not be on PATH)."""

from vidroute.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
