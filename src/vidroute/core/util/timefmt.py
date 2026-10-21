"""Timestamp formatting and parsing. All timestamps vidroute emits are *global* video time."""

from __future__ import annotations

import re

from vidroute.core.errors import UsageError


def fmt_ts(seconds: float) -> str:
    """``mm:ss`` below one hour, ``h:mm:ss`` above (used in transcripts and minutes)."""
    total = max(0, int(round(seconds)))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def fmt_srt(seconds: float) -> str:
    ms_total = max(0, int(round(seconds * 1000)))
    h, rem = divmod(ms_total, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def fmt_compact(seconds: float) -> str:
    """Filesystem-friendly timestamp such as ``0m05s`` or ``1h02m03s``."""
    total = max(0, int(round(seconds)))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s"


_TS = re.compile(r"^(?:(\d+):)?(?:(\d+):)?(\d+(?:\.\d+)?)$")


def parse_ts(text: str) -> float:
    """Accept ``SS``, ``SS.s``, ``MM:SS``, ``HH:MM:SS`` (fractions allowed on seconds)."""
    raw = text.strip()
    m = _TS.match(raw)
    if not m:
        raise UsageError(f"Invalid timestamp {text!r}; use SS, MM:SS or HH:MM:SS")
    a, b, c = m.groups()
    parts = [p for p in (a, b) if p is not None]
    seconds = float(c)
    if len(parts) == 2:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + seconds
    if len(parts) == 1:
        return int(parts[0]) * 60 + seconds
    return seconds


def parse_range(text: str) -> tuple[float, float]:
    """Parse ``START-END`` (e.g. ``01:30-02:45`` or ``90-165``)."""
    if "-" not in text:
        raise UsageError(f"Invalid range {text!r}; use START-END, e.g. 01:30-02:45")
    start_s, end_s = text.split("-", 1)
    start, end = parse_ts(start_s), parse_ts(end_s)
    if end <= start:
        raise UsageError(f"Invalid range {text!r}: END must be after START")
    return start, end
