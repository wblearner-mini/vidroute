"""Human-readable logging. Always stderr: stdout is reserved for JSON (CLI) and JSON-RPC (MCP).

Logs never include video content (transcript text, OCR text); only paths,
counts, timings and engine names.
"""

from __future__ import annotations

import logging
import sys

_LOGGER_NAME = "vidroute"
_configured = False


def get_logger() -> logging.Logger:
    global _configured
    logger = logging.getLogger(_LOGGER_NAME)
    if not _configured:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("vidroute: %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
        _configured = True
    return logger


def set_verbosity(verbose: bool = False, quiet: bool = False) -> None:
    logger = get_logger()
    if quiet:
        logger.setLevel(logging.WARNING)
    elif verbose:
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)
