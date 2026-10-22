"""Timestamps: formatting truncates like a player's clock; parsing accepts SS, MM:SS, HH:MM:SS."""

import pytest

from vidroute.core.errors import UsageError
from vidroute.core.util.timefmt import fmt_compact, fmt_srt, fmt_ts, parse_range, parse_ts


def test_fmt_ts_truncates_and_switches_to_hours():
    assert [fmt_ts(t) for t in (0, 2.75, 137.5, 138.5, 138.99, 3599.9, 3600, 3725.2)] == [
        "00:00", "00:02", "02:17", "02:18", "02:18", "59:59", "1:00:00", "1:02:05"]
    assert fmt_ts(-3) == "00:00"


def test_fmt_compact_and_srt():
    assert fmt_compact(2.75) == "0m02s" and fmt_compact(3725.9) == "1h02m05s"
    assert fmt_srt(61.2345) == "00:01:01,234" and fmt_srt(3600.0005) == "01:00:00,000"


def test_parse_ts_and_range():
    assert parse_ts("90") == 90 and parse_ts("01:30") == 90 and parse_ts("1:01:30.5") == 3690.5
    assert parse_range("01:20-02:00") == (80, 120) and parse_range("10-14.5") == (10, 14.5)
    for bad in ("1:2:3:4", "abc", "02:00-01:00", "90"):
        with pytest.raises(UsageError):
            parse_range(bad) if "-" in bad or bad == "90" else parse_ts(bad)
