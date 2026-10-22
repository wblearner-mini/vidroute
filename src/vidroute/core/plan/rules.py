"""Routing rules (routing/rules.yaml) plus user overrides from config."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from vidroute.core import config
from vidroute.core.errors import DataFileError
from vidroute.core.resources import data_file
from vidroute.core.util.yamlish import YamlError, load_file

REQUIRED = {
    "delivery": ("native_only_max_minutes", "max_segments", "segment_overlap_seconds", "budget_fraction",
                 "over_budget_action", "transcode", "audio_attachment", "transcript_tasks"),
    "frames": ("max_frames", "base_interval_seconds", "max_candidates", "scene_threshold", "contact_sheet"),
    "local": ("embedded_subtitle_min_coverage", "speech_ratio_skip_asr", "ocr_text_density_threshold",
              "long_video_minutes", "asr_chains", "ocr_chain", "vad_chain", "zh_languages"),
    "estimates": ("transcript_tokens_per_speech_second", "asr_rtf", "ocr_seconds_per_frame"),
}


@lru_cache(maxsize=1)
def load_rules() -> dict[str, Any]:
    path = data_file("routing/rules.yaml")
    try:
        raw = load_file(path)
    except YamlError as exc:
        raise DataFileError(str(exc)) from exc
    if not isinstance(raw, dict) or raw.get("schema") != "vidroute/rules/1":
        raise DataFileError(f"{path}: expected schema 'vidroute/rules/1'")
    for section, keys in REQUIRED.items():
        missing = [k for k in keys if k not in (raw.get(section) or {})]
        if missing:
            raise DataFileError(f"{path}: section {section!r} lacks {missing}")
    return raw


def budget_tokens(context_tokens: int, rules: dict[str, Any] | None = None) -> int:
    rules = rules or load_rules()
    absolute = config.get("budget_tokens")
    if absolute:
        return int(absolute)
    fraction = config.get("budget_fraction") or rules["delivery"]["budget_fraction"]
    return int(context_tokens * float(fraction))
