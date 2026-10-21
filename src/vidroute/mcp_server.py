"""Shell 2: MCP server over stdio (implemented in Phase 6).

The entry point exists from Phase 0 so packaging and console scripts are stable.
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    sys.stderr.write("vidroute-mcp: the MCP server is not implemented yet (Phase 6).\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
