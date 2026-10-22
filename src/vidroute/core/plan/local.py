"""Layer 2 — local execution: which local engines run, and why (brief §8, R1–R8).

R1 embedded text subtitles first · R2 skip ASR without speech · R3 ASR chosen by
language selector and tier with fallbacks · R4 OCR only for text-heavy video ·
R5 keyframe budget (scene changes + grid + dedup, contact sheets first) · R6
long-video adjustments · R8 nothing here calls the cloud.
"""

from __future__ import annotations

from typing import Any

from vidroute.core.engines import get_engine, supported
from vidroute.core.install.manifest import Manifest


def _step(sid: str, engine: str | None, reason: str, *, status: str = "planned", alternatives: list[str] | None = None,
          est_seconds: float = 0.0, est_input_tokens: int = 0, uploads_raw_video: bool = False,
          hint: str | None = None, **extra: Any) -> dict[str, Any]:
    step = {
        "id": sid, "engine": engine, "status": status, "reason": reason, "alternatives": alternatives or [],
        "est_seconds": round(est_seconds, 1), "est_input_tokens": int(est_input_tokens),
        "uploads_raw_video": uploads_raw_video,
    }
    if hint:
        step["hint"] = hint
    step.update(extra)
    return step


def installed(model_id: str, manifest: Manifest) -> bool:
    try:
        if not supported(model_id, manifest):
            return False
        return bool(get_engine(model_id, manifest).probe()["installed"])
    except Exception:  # noqa: BLE001
        return False


def selector_for(language: str | None, rules: dict[str, Any]) -> str:
    if not language or language == "auto":
        return "auto"
    return "zh" if language.lower() in rules["local"]["zh_languages"] or language.lower().split("-")[0] in ("zh", "yue") \
        else "multi"


def choose_asr(language: str | None, tier: str, rules: dict[str, Any], manifest: Manifest) -> dict[str, Any]:
    """First installed engine of the selector's chain that supports the language (R3)."""
    sel = selector_for(language, rules)
    chains = rules["local"]["asr_chains"]
    order = ["zh", "multi"] if sel in ("auto", "zh") else ["multi", "zh"]
    tried: list[dict[str, str]] = []
    tier_key = tier if tier in chains["zh"] else "cpu-full"
    for chain_name in order:
        for mid in chains[chain_name][tier_key]:
            if any(t["id"] == mid for t in tried):
                continue
            entry = manifest.model(mid)
            lang = None if sel == "auto" else language
            if lang and not entry.supports_language(lang):
                tried.append({"id": mid, "status": "language not supported"})
                continue
            if not installed(mid, manifest):
                tried.append({"id": mid, "status": "not installed"})
                continue
            tried.append({"id": mid, "status": "selected"})
            return {"selected": mid, "selector": sel, "chain": chain_name, "tried": tried}
    return {"selected": None, "selector": sel, "chain": order[0], "tried": tried}


def first_installed(chain: list[str], manifest: Manifest) -> str | None:
    for mid in chain:
        if installed(mid, manifest):
            return mid
    return None


def plan_steps(
    probe: dict[str, Any],
    decision: dict[str, Any],
    tier: str,
    rules: dict[str, Any],
    manifest: Manifest,
    task: str,
    language: str,
    ocr_mode: str = "auto",
    force_asr: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    loc = rules["local"]
    est = rules["estimates"]
    route = decision["route"]
    duration = float(probe["duration"] or 0)
    speech_ratio = float(probe.get("speech_ratio") or 0)
    speech_seconds = speech_ratio * duration
    long_video = duration > float(loc["long_video_minutes"]) * 60
    steps: list[dict[str, Any]] = []
    needs: list[dict[str, str]] = []
    native = route.startswith("native")

    # ---------------------------------------------------------------- audio
    subs = probe.get("text_subtitles") or []
    if subs:
        steps.append(_step("subtitles", "ffmpeg",
                           f"R1: {len(subs)} embedded text subtitle track(s) ({', '.join(s['codec'] for s in subs)}) "
                           f"become the transcript (source=embedded_subtitle); ASR only runs if they cover "
                           f"< {int(loc['embedded_subtitle_min_coverage'] * 100)}% of the speech or with --force-asr",
                           est_seconds=1.0, tracks=[s["sub_index"] for s in subs]))
    transcript_needed = decision["transcript"]["role"] != "none" or task in ("transcript", "minutes")
    if not probe.get("has_audio"):
        steps.append(_step("asr", None, "no audio track", status="skipped"))
    elif speech_ratio < float(loc["speech_ratio_skip_asr"]) and not force_asr:
        steps.append(_step("asr", None, f"R2: no speech detected (speech_ratio {speech_ratio} < "
                           f"{loc['speech_ratio_skip_asr']}); SUMMARY will say so", status="skipped",
                           hint="vidroute prepare ... --force-asr"))
    elif not transcript_needed:
        steps.append(_step("asr", None, "the model hears the audio itself and no verbatim text was requested",
                           status="skipped", hint="--task transcript to get a reference transcript"))
    else:
        steps.append(_step("extract_audio", "ffmpeg", "16 kHz mono WAV for VAD/ASR (audio/16k.wav)",
                           est_seconds=duration * 0.01))
        vad_id = first_installed(loc["vad_chain"], manifest) or "vad-energy"
        steps.append(_step("vad", vad_id,
                           "speech segments for chunked ASR" + (" (energy fallback: music counts as sound)"
                                                                if vad_id == "vad-energy" else ""),
                           alternatives=[m for m in loc["vad_chain"] if m != vad_id],
                           est_seconds=duration * 0.005))
        choice = choose_asr(language, tier, rules, manifest)
        role = decision["transcript"]["role"]
        why = {"sole_source": "the only source of what was said (the LLM cannot hear the audio)",
               "reference": "reference/anchor with timestamps; the LLM hears the audio itself"}.get(role, "requested")
        lang_note = "language detected from the first 30 s of speech" if choice["selector"] == "auto" else \
            f"language {language} -> '{choice['selector']}' selector"
        rtf = float(est["asr_rtf"].get(tier, est["asr_rtf"]["cpu-full"]))
        if choice["selected"]:
            steps.append(_step("asr", choice["selected"], f"R3: {why}; {lang_note}; chunked (<= "
                               f"{loc['asr_chunk_max_seconds']} s) and resumable",
                               alternatives=[t["id"] for t in choice["tried"] if t["status"] != "selected"],
                               est_seconds=speech_seconds * rtf + 2, est_input_tokens=decision["transcript"]["est_tokens"],
                               tried=choice["tried"]))
        else:
            steps.append(_step("asr", None, f"R3: {why}; no ASR engine installed", status="needs_install",
                               hint="vidroute install --capability asr", tried=choice["tried"]))
            needs.append({"capability": "asr", "why": "speech must be transcribed for this route",
                          "hint": "vidroute install --capability asr"})

    # --------------------------------------------------------------- pictures
    frames = decision.get("frames")
    want_frames = route in ("frames", "text") and probe.get("has_video")
    if want_frames:
        target = (frames or {}).get("target_frames") or min(rules["frames"]["max_frames"], int(duration // 4) + 1)
        steps.append(_step("scene", "scene-ffmpeg", "scene-change candidates (ffmpeg scene score"
                           + (", keyframes only for a long video" if long_video else "") + ")",
                           est_seconds=duration * (0.005 if long_video else 0.02)))
        attach = route == "frames"
        steps.append(_step("keyframes", "ffmpeg",
                           f"R5: scene changes + periodic grid + dHash dedup, up to {target} frames"
                           + ("; attached via contact sheets first, single frames on demand (drill)" if attach
                              else "; used for OCR/timeline only (not attached)"),
                           est_seconds=target * float(est["frame_extract_seconds"]),
                           est_input_tokens=(frames or {}).get("est_tokens", 0) if attach else 0, frames=target))
        if attach:
            steps.append(_step("contact_sheets", "ffmpeg",
                               f"{frames['contact_sheets']} sheet(s) of {frames['per_sheet']} tiles with timestamps "
                               "(fewer images = fewer tokens; stays under dense-image limits)",
                               est_seconds=frames["contact_sheets"] * 0.5))
    density = float(probe.get("text_density") or 0)
    threshold = float(loc["ocr_text_density_threshold"])
    ocr_wanted = ocr_mode == "on" or (ocr_mode == "auto" and density >= threshold and want_frames)
    if ocr_mode == "off":
        steps.append(_step("ocr", None, "disabled with --ocr off", status="skipped"))
    elif not ocr_wanted:
        why = (f"R4: text_density {density} < {threshold}" if want_frames
               else "native route: the LLM reads on-screen text itself")
        steps.append(_step("ocr", None, why, status="skipped", hint="--ocr on"))
    else:
        ocr_id = first_installed(loc["ocr_chain"], manifest)
        n = (frames or {}).get("target_frames") or 30
        if ocr_id:
            secs = float(est["ocr_seconds_per_frame"].get(tier, 0.5))
            steps.append(_step("ocr", ocr_id, f"R4: on-screen text (text_density {density} >= {threshold})"
                               + ("; only at change points for a long video" if long_video else "")
                               + "; OCR is evidence for proofreading the transcript",
                               alternatives=[m for m in loc["ocr_chain"] if m != ocr_id],
                               est_seconds=n * secs, est_input_tokens=n * int(est["ocr_tokens_per_frame"])))
        else:
            steps.append(_step("ocr", None, f"R4: text_density {density} >= {threshold} but no OCR engine is installed",
                               status="needs_install", hint="vidroute install --capability ocr"))
            needs.append({"capability": "ocr", "why": "on-screen text would help (optional)",
                          "hint": "vidroute install --capability ocr", "optional": "true"})

    # ------------------------------------------------------------ native media
    if native:
        n = decision["native"]
        tc = n.get("transcode")
        segs = n["segments"]
        if tc is None:
            desc = "original file is within the limits: copied as is"
        elif tc.get("mode") == "copy":
            desc = "remux without the audio track (the model cannot hear it)"
        else:
            desc = (f"H.264 {tc['height']}p@{tc['fps']}fps"
                    + (f", {tc['video_kbps']} kbps" if tc.get("video_kbps") else f", CRF {tc.get('crf')}")
                    + (f" + AAC {tc['audio_kbps']} kbps" if tc.get("audio_kbps") else ", no audio"))
        if len(segs) > 1:
            desc += f"; {len(segs)} segments with {rules['delivery']['segment_overlap_seconds']} s overlap " \
                    "(segments.json keeps global offsets)"
        steps.append(_step("transcode", "ffmpeg", desc, est_seconds=(duration * float(est["transcode_rtf"])
                                                                     if tc and tc.get("mode") == "encode" else 2.0),
                           est_input_tokens=n["est_video_tokens"], uploads_raw_video=True,
                           segments=len(segs)))
    audio_att = decision.get("audio_attachment") or {}
    if audio_att.get("enabled"):
        steps.append(_step("audio_attachment", "ffmpeg",
                           f"16 kHz mono MP3 at {audio_att['kbps']} kbps"
                           + (f" in {audio_att['parts']} parts" if audio_att["parts"] > 1 else "")
                           + " so the model can listen (needs the user's consent, like raw video)",
                           est_seconds=duration * 0.02, est_input_tokens=audio_att.get("est_tokens", 0)))

    steps.append(_step("timeline", None, "timeline.md merges transcript, OCR and frames by global time", est_seconds=0.5))
    steps.append(_step("handoff", None, "SUMMARY.md, ANALYSIS_GUIDE.md and handoff.json for the agent", est_seconds=0.2))
    if long_video:
        steps.append(_step("long_video", None, f"R6: longer than {loc['long_video_minutes']} min: sparser frame grid, "
                           "OCR only at change points, ASR chunked and resumable", status="note"))
    return steps, needs
