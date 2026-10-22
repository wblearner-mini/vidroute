"""Layer 1 — delivery route: what to hand the LLM (brief §8).

Deterministic: effective capabilities (LLM ∩ host) give the candidate route;
admission checks (privacy, limits after transcode/segmentation, token budget)
either accept it or degrade along ``native-* -> frames -> text``. Every
degradation is recorded with its reason.
"""

from __future__ import annotations

import math
from typing import Any

SAFE_CONTAINERS = ("mov,mp4,m4a,3gp,3g2,mj2",)
SAFE_VIDEO_CODECS = ("h264",)
SAFE_AUDIO_CODECS = ("aac", "mp3")
# Rough "good quality" H.264 bitrates at <= 10 fps for each output height.
QUALITY_KBPS = {720: 700, 540: 450, 480: 350, 360: 220}
DEFAULT_TOKENS_PER_SECOND = 300.0
DEFAULT_TOKENS_PER_IMAGE = 1500


def _inf(v: float | None) -> float:
    return float("inf") if v in (None, 0) else float(v)


def native_plan(probe: dict[str, Any], limits: dict[str, Any], rules: dict[str, Any], with_audio: bool) -> dict[str, Any]:
    """Can the video be delivered within the per-request limits (after transcode and/or segmentation)?"""
    d = rules["delivery"]
    tc = d["transcode"]
    duration = float(probe["duration"] or 0)
    size_mb = float(probe["size_mb"] or 0)
    max_s = _inf(limits.get("max_seconds"))
    max_mb = _inf(limits.get("max_file_mb"))
    # Segments may go into separate requests, so only max_segments caps their number; the
    # per-request file limit is reported for the handoff instead.
    max_files = int(d["max_segments"])
    overlap = float(d["segment_overlap_seconds"])
    safety = float(tc["size_safety"])
    v = probe.get("video") or {}
    audio_codecs = {a.get("codec") for a in probe.get("audio") or []}
    compatible = (probe.get("format") in SAFE_CONTAINERS and v.get("codec") in SAFE_VIDEO_CODECS
                  and (not with_audio or not probe.get("has_audio") or audio_codecs <= set(SAFE_AUDIO_CODECS)))

    if duration <= max_s and size_mb <= max_mb * safety and compatible:
        if not with_audio and probe.get("has_audio"):
            # native-v: the model cannot hear, so do not ship the audio track (remux, no re-encode).
            return {"feasible": True, "transcode": {"mode": "copy", "drop_audio": True},
                    "segments": [[0.0, round(duration, 3)]], "why": "fits the limits; audio track dropped",
                    "per_file_mb": size_mb}
        return {"feasible": True, "transcode": None, "segments": [[0.0, round(duration, 3)]],
                "why": "fits the limits as is", "per_file_mb": size_mb}

    audio_kbps = float(tc["audio_kbps"]) if with_audio and probe.get("has_audio") else 0.0
    min_audio = float(tc["min_audio_kbps"]) if audio_kbps else 0.0
    min_video = float(tc["min_video_kbps"])

    def seconds_fitting(kbps: float) -> float:
        return float("inf") if max_mb == float("inf") else max_mb * safety * 8000.0 / kbps

    max_by_size = seconds_fitting(min_video + min_audio)
    per_file = min(max_s, max_by_size)
    if per_file < 30:
        return {"feasible": False, "why": f"even at the lowest quality a file can hold only {per_file:.0f} s "
                f"within {limits.get('max_file_mb')} MB"}
    if duration <= per_file:
        segments = [[0.0, round(duration, 3)]]
    else:
        step = per_file - overlap
        n = math.ceil((duration - overlap) / step)
        if n > max_files:
            return {"feasible": False, "why": f"would need {n} segments (limit {max_files}): "
                    f"{duration / 60:.0f} min video, {per_file / 60:.1f} min per request"}
        step = (duration - overlap) / n  # balance segment lengths
        segments = []
        for i in range(n):
            s = i * step
            e = duration if i == n - 1 else s + step + overlap
            segments.append([round(s, 3), round(min(duration, e), 3)])
    seg_len = max(e - s for s, e in segments)
    budget_kbps = (max_mb * safety * 8000.0 / seg_len) if max_mb != float("inf") else float("inf")
    if budget_kbps - audio_kbps < min_video and audio_kbps:
        audio_kbps = max(min_audio, budget_kbps - min_video)
    video_kbps = budget_kbps - audio_kbps
    src_h = int(v.get("height") or 720)
    heights = [h for h in tc["target_heights"] if h <= src_h] or [src_h]  # never upscale
    height = heights[-1]
    for h in heights:
        if QUALITY_KBPS.get(h, 0) <= video_kbps:
            height = h
            break
    fps = min(float(v.get("fps") or tc["max_fps"]), float(tc["max_fps"]))
    target_video = min(video_kbps, QUALITY_KBPS.get(height, 700) * 1.5)
    est_mb = (target_video + audio_kbps) * seg_len / 8000.0
    return {
        "feasible": True,
        "per_request_max_files": limits.get("max_files"),
        "why": "transcode" + (f" + {len(segments)} segments" if len(segments) > 1 else "") + " to fit the limits",
        "segments": segments,
        "transcode": {
            "mode": "encode",
            "container": "mp4", "video_codec": tc["video_codec"], "audio_codec": tc["audio_codec"] if audio_kbps else None,
            "height": height, "fps": round(max(float(tc["min_fps"]), fps), 2),
            "video_kbps": int(target_video) if budget_kbps != float("inf") else None,
            "audio_kbps": int(audio_kbps) if audio_kbps else None,
            "crf": tc["crf"] if budget_kbps == float("inf") else None,
            "max_file_mb": None if max_mb == float("inf") else max_mb,
        },
        "per_file_mb": round(est_mb, 2) if budget_kbps != float("inf") else None,
    }


def video_tokens(seconds: float, llm_video: dict[str, Any] | None, include_audio: bool, llm_audio: dict[str, Any] | None,
                 files: int = 1) -> tuple[int, bool]:
    """Estimated input tokens for native video; second value says whether the rate was known."""
    v = llm_video or {}
    tps = v.get("tokens_per_second")
    known = tps is not None
    tps = float(tps if known else DEFAULT_TOKENS_PER_SECOND)
    visual = tps * seconds
    cap = v.get("max_video_tokens")
    if cap:
        visual = min(visual, float(cap) * max(1, files))
    audio = 0.0
    if include_audio and llm_audio and llm_audio.get("tokens_per_second") and (llm_video or {}).get("max_video_tokens"):
        audio = float(llm_audio["tokens_per_second"]) * seconds  # Qwen bills audio separately
    return int(visual + audio), known


def image_tokens(width: int, height: int, image_limits: dict[str, Any] | None) -> int:
    lim = image_limits or {}
    side = lim.get("max_side_px")
    w, h = float(width), float(height)
    if side and max(w, h) > side:
        scale = side / max(w, h)
        w, h = w * scale, h * scale
    patch = lim.get("patch_px")
    if patch:
        tokens = math.ceil(w / patch) * math.ceil(h / patch)
        cap = lim.get("tokens_per_image")
        return int(min(tokens, cap) if cap else tokens)
    return int(lim.get("tokens_per_image") or DEFAULT_TOKENS_PER_IMAGE)


def contact_sheet_size(rules: dict[str, Any], aspect: float = 16 / 9) -> tuple[int, int]:
    cs = rules["frames"]["contact_sheet"]
    tile_w = int(cs["tile_width"])
    tile_h = int(round(tile_w / aspect))
    return tile_w * int(cs["cols"]), tile_h * int(cs["rows"])


def frames_plan(probe: dict[str, Any], eff: dict[str, Any], rules: dict[str, Any]) -> dict[str, Any]:
    lim = eff.get("image_limits") or {}
    max_frames = int(min(rules["frames"]["max_frames"], _inf(lim.get("max_images"))))
    duration = float(probe["duration"] or 0)
    base = float(rules["frames"]["base_interval_seconds"])
    # Fewer frames for short videos: one every base interval, at least 1.
    target = max(1, min(max_frames, int(duration // base) + 1))
    cs = rules["frames"]["contact_sheet"]
    per_sheet = int(cs["cols"]) * int(cs["rows"])
    sheets = math.ceil(target / per_sheet)
    v = probe.get("video") or {}
    aspect = (v.get("width") or 16) / float(v.get("height") or 9)
    sw, sh = contact_sheet_size(rules, aspect)
    tokens = sheets * image_tokens(sw, sh, lim)
    return {"max_frames": max_frames, "target_frames": target, "contact_sheets": sheets, "per_sheet": per_sheet,
            "sheet_size": [sw, sh], "est_tokens": tokens, "frame_tokens": image_tokens(1280, int(1280 / aspect), lim)}


def audio_attachment_plan(probe: dict[str, Any], eff: dict[str, Any], rules: dict[str, Any]) -> dict[str, Any]:
    cfg = rules["delivery"]["audio_attachment"]
    lim = eff.get("audio_limits") or {}
    if not cfg.get("enabled"):
        return {"enabled": False, "why": "disabled in rules"}
    duration = float(probe["duration"] or 0)
    max_s = _inf(lim.get("max_seconds"))
    max_mb = _inf(lim.get("max_file_mb"))
    for kbps in cfg["mp3_kbps_ladder"]:
        size_mb = kbps * duration / 8000.0
        if size_mb <= max_mb * 0.95:
            parts = max(1, math.ceil(duration / max_s)) if max_s != float("inf") else 1
            if parts > rules["delivery"]["max_segments"]:
                return {"enabled": False, "why": f"audio would need {parts} parts"}
            return {"enabled": True, "format": "mp3", "sample_rate": cfg["sample_rate"], "kbps": kbps,
                    "parts": parts, "est_mb": round(size_mb, 2),
                    "est_tokens": int(float(lim.get("tokens_per_second") or 32) * duration)}
    return {"enabled": False, "why": f"even {cfg['mp3_kbps_ladder'][-1]} kbps exceeds {lim.get('max_file_mb')} MB"}


def transcript_tokens(probe: dict[str, Any], rules: dict[str, Any]) -> int:
    speech = float(probe.get("speech_ratio") or 0) * float(probe["duration"] or 0)
    return int(speech * float(rules["estimates"]["transcript_tokens_per_speech_second"]))


def decide(
    probe: dict[str, Any],
    resolution: dict[str, Any],
    rules: dict[str, Any],
    task: str,
    prefer: str,
    allow_raw: bool,
    budget: int,
    ocr_frames: int = 0,
    forced: str | None = None,
) -> dict[str, Any]:
    eff = resolution["effective"]
    llm = resolution["llm"]
    host = resolution["host"]
    d = rules["delivery"]
    reasons: list[str] = []
    degraded: list[dict[str, str]] = []
    confirm: list[dict[str, str]] = []
    suggestions: list[dict[str, str]] = []
    over_action = d["over_budget_action"].get(prefer, "confirm")
    speech = probe.get("has_audio") and float(probe.get("speech_ratio") or 0) >= rules["local"]["speech_ratio_skip_asr"]
    t_tokens = transcript_tokens(probe, rules) if speech else 0
    ocr_tokens = ocr_frames * int(rules["estimates"]["ocr_tokens_per_frame"])
    overhead = int(rules["estimates"]["timeline_overhead_tokens"])

    order: list[str] = []
    if eff["video"]:
        order.append("native-av" if eff["audio_in_video"] else "native-v")
        reasons.append(
            f"{llm['model']} accepts video{' with its audio track' if eff['audio_in_video'] else ' (cannot hear the audio)'}"
            f" and host {host['id']} attaches video natively")
    else:
        for b in eff["blocked_by_host"]:
            if b["modality"] == "video":
                reasons.append(f"{llm['model']} understands video, but {b['why']}")
                suggestions.append({
                    "route": "delegate",
                    "why": f"The model can watch video but the host cannot attach it (host-limited, not model-limited).",
                    "how": "Planned (D5): configure a video-model endpoint so vidroute calls it and returns text; "
                           "or run the agent in a host that attaches video (e.g. gemini-cli, qwen-code).",
                })
        if "video" not in llm["modalities_in"]:
            reasons.append(f"{llm['model']} does not accept video input")
    if eff["image"]:
        order.append("frames")
    else:
        reasons.append(f"no image input ({'model' if 'image' not in llm['modalities_in'] else 'host'})")
    order.append("text")

    if forced and forced not in ("auto",):
        if forced not in order:
            from vidroute.core.errors import UsageError

            raise UsageError(
                f"Route {forced!r} is not possible here (available: {', '.join(order)}).",
                hint="Re-run plan with the right --host/--llm instead of forcing a route",
            )
        idx = order.index(forced)
        for r in order[:idx]:
            degraded.append({"route": r, "why": f"--route {forced} requested"})
        order = order[idx:]

    chosen = None
    native = None
    frames = None
    audio_att: dict[str, Any] = {"enabled": False}
    est = 0
    for r in order:
        if r.startswith("native"):
            if prefer == "privacy":
                degraded.append({"route": r, "why": "--prefer privacy: the raw video stays local (only frames/text leave)"})
                continue
            if not allow_raw:
                degraded.append({"route": r, "why": "config allow_raw_video_handoff=false"})
                continue
            npl = native_plan(probe, eff["video_limits"] or {}, rules, with_audio=(r == "native-av"))
            if not npl["feasible"]:
                degraded.append({"route": r, "why": npl["why"]})
                continue
            secs = float(probe["duration"] or 0)
            vt, known = video_tokens(secs, llm.get("video"), r == "native-av", llm.get("audio"), len(npl["segments"]))
            attach_t = (r == "native-v") or task in d["transcript_tasks"] or len(npl["segments"]) > 1 \
                or secs > d["native_only_max_minutes"] * 60
            total = vt + (t_tokens if attach_t else 0) + overhead
            if total > budget:
                if over_action == "degrade":
                    degraded.append({"route": r, "why": f"~{total:,} input tokens > budget {budget:,} (--prefer cost)"})
                    continue
                confirm.append({"code": "over_budget", "message": f"~{total:,} input tokens exceed the budget of {budget:,}"})
            if not known:
                reasons.append("video token rate unknown for this model: estimate uses 300 tokens/s")
            native = {**npl, "est_video_tokens": vt, "transcript_attached": attach_t}
            confirm.append({"code": "raw_video_handoff",
                            "message": "The full video will be handed to the LLM provider. Ask the user first; "
                                       "prepare needs --accept-raw-video-handoff."})
            chosen, est = r, total
            break
        if r == "frames":
            frames = frames_plan(probe, eff, rules)
            if eff["audio"] and probe.get("has_audio") and speech:
                audio_att = audio_attachment_plan(probe, eff, rules)
            total = frames["est_tokens"] + t_tokens + ocr_tokens + overhead + (audio_att.get("est_tokens", 0)
                                                                              if audio_att.get("enabled") else 0)
            if total > budget:
                if audio_att.get("enabled"):
                    audio_att = {"enabled": False, "why": "dropped to stay within the token budget"}
                    total = frames["est_tokens"] + t_tokens + ocr_tokens + overhead
                if total > budget:
                    if over_action == "degrade":
                        degraded.append({"route": r, "why": f"~{total:,} input tokens > budget {budget:,} (--prefer cost)"})
                        continue
                    confirm.append({"code": "over_budget", "message": f"~{total:,} input tokens exceed the budget of {budget:,}"})
            chosen, est = r, total
            break
        chosen, est = "text", t_tokens + ocr_tokens + overhead
        if est > budget:
            confirm.append({"code": "over_budget", "message": f"~{est:,} input tokens exceed the budget of {budget:,}"})
        break

    assert chosen is not None
    if chosen == "frames":
        reasons.append(f"keyframes + contact sheets ({frames['contact_sheets']} sheet(s), up to {frames['max_frames']} frames)"
                       + (" + audio track (the model can listen)" if audio_att.get("enabled") else "")
                       + " + local transcript")
    if chosen == "text":
        reasons.append("text only: transcript/OCR timeline; picture information is limited")
    role = "none"
    attach_transcript = False
    if speech:
        if chosen == "native-av":
            role, attach_transcript = "reference", bool(native and native["transcript_attached"])
        elif chosen == "frames" and audio_att.get("enabled"):
            role, attach_transcript = "reference", True
        else:
            role, attach_transcript = "sole_source", True
    return {
        "route": chosen,
        "candidates": order,
        "reasons": reasons,
        "degraded_from": degraded,
        "uploads_raw_video": chosen.startswith("native"),
        "uploads_raw_audio": chosen.startswith("native-av") or bool(audio_att.get("enabled")),
        "native": native,
        "frames": frames,
        "audio_attachment": audio_att,
        "transcript": {"role": role, "attach": attach_transcript, "est_tokens": t_tokens if attach_transcript else 0},
        "budget": {"budget_tokens": budget, "est_input_tokens": est, "over_budget": est > budget,
                   "over_budget_action": over_action},
        "needs_confirmation": confirm,
        "suggestions": suggestions,
    }
