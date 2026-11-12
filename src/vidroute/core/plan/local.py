"""Layer 2 — local execution: which local engines run, and why (rules R1–R8).

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


def want_speakers(diarize: str, task: str) -> bool:
    return diarize == "on" or (diarize == "auto" and task == "minutes")


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
                tried.append({"id": mid, "status": "不支持该语言"})
                continue
            if not installed(mid, manifest):
                tried.append({"id": mid, "status": "未安装"})
                continue
            tried.append({"id": mid, "status": "selected"})
            return {"selected": mid, "selector": sel, "chain": chain_name, "tried": tried}
    return {"selected": None, "selector": sel, "chain": order[0], "tried": tried}


def asr_candidates(language: str | None, tier: str, rules: dict[str, Any], manifest: Manifest) -> list[str]:
    """Every installed ASR engine that supports the language, in preference order (the fallback chain)."""
    sel = selector_for(language, rules)
    chains = rules["local"]["asr_chains"]
    tier_key = tier if tier in chains["zh"] else "cpu-full"
    out: list[str] = []
    for chain_name in (["zh", "multi"] if sel in ("auto", "zh") else ["multi", "zh"]):
        for mid in chains[chain_name][tier_key]:
            if mid in out:
                continue
            lang = None if sel == "auto" else language
            if lang and not manifest.model(mid).supports_language(lang):
                continue
            if installed(mid, manifest):
                out.append(mid)
    return out


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
    diarize: str = "auto",
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
                           f"R1：{len(subs)} 条内嵌文本字幕（{', '.join(s['codec'] for s in subs)}）"
                           f"直接用作转写（source=embedded_subtitle）；只有当字幕覆盖"
                           f"不到 {int(loc['embedded_subtitle_min_coverage'] * 100)}% 的语音或使用 --force-asr 时才运行 ASR",
                           est_seconds=1.0, tracks=[s["sub_index"] for s in subs]))
    transcript_needed = decision["transcript"]["role"] != "none" or task in ("transcript", "minutes")
    if not probe.get("has_audio"):
        steps.append(_step("asr", None, "没有音轨", status="skipped"))
    elif speech_ratio < float(loc["speech_ratio_skip_asr"]) and not force_asr:
        steps.append(_step("asr", None, f"R2：未检测到语音（speech_ratio {speech_ratio} < "
                           f"{loc['speech_ratio_skip_asr']}）；SUMMARY 中会说明", status="skipped",
                           hint="vidroute prepare ... --force-asr"))
    elif not transcript_needed:
        steps.append(_step("asr", None, "模型自己能听音频，且没有要求逐字文本",
                           status="skipped", hint="--task transcript 可获得参考转写"))
    else:
        steps.append(_step("extract_audio", "ffmpeg", "供 VAD/ASR 使用的 16 kHz 单声道 WAV（audio/16k.wav）",
                           est_seconds=duration * 0.01))
        vad_id = first_installed(loc["vad_chain"], manifest) or "vad-energy"
        steps.append(_step("vad", vad_id,
                           "为分块 ASR 切分语音片段" + ("（能量回退：音乐也会算作声音）"
                                                                if vad_id == "vad-energy" else ""),
                           alternatives=[m for m in loc["vad_chain"] if m != vad_id],
                           est_seconds=duration * 0.005))
        choice = choose_asr(language, tier, rules, manifest)
        role = decision["transcript"]["role"]
        why = {"sole_source": "说话内容的唯一来源（LLM 听不到音频）",
               "reference": "带时间戳的参考/锚点；LLM 自己能听到音频"}.get(role, "按要求")
        lang_note = "根据前 30 秒语音检测语言" if choice["selector"] == "auto" else \
            f"语言 {language} -> '{choice['selector']}' 选择器"
        rtf = float(est["asr_rtf"].get(tier, est["asr_rtf"]["cpu-full"]))
        if choice["selected"]:
            steps.append(_step("asr", choice["selected"], f"R3：{why}；{lang_note}；分块（<= "
                               f"{loc['asr_chunk_max_seconds']} 秒）且可续跑",
                               alternatives=[t["id"] for t in choice["tried"] if t["status"] != "selected"],
                               est_seconds=speech_seconds * rtf + 2, est_input_tokens=decision["transcript"]["est_tokens"],
                               tried=choice["tried"]))
        else:
            steps.append(_step("asr", None, f"R3：{why}；未安装 ASR 引擎", status="needs_install",
                               hint="vidroute install --capability asr", tried=choice["tried"]))
            needs.append({"capability": "asr", "why": "这条路线需要转写语音",
                          "hint": "vidroute install --capability asr"})
        if choice["selected"] and want_speakers(diarize, task):
            model = "diarize-pyannote-campplus"
            if installed(model, manifest):
                steps.append(_step("diarize", model, "在转写中标出谁在什么时候说话（Speaker 1、2……）",
                                   est_seconds=speech_seconds * float(est.get("diarize_rtf", 0.1))))
            else:
                steps.append(_step("diarize", None, "说话人标签需要说话人区分模型", status="needs_install",
                                   hint="vidroute install --capability diarize"))
                needs.append({"capability": "diarize", "why": "没有它，转写中就没有说话人标签",
                              "hint": "vidroute install --capability diarize", "optional": "true"})

    # --------------------------------------------------------------- pictures
    frames = decision.get("frames")
    want_frames = route in ("frames", "text") and probe.get("has_video")
    if want_frames:
        target = (frames or {}).get("target_frames") or min(rules["frames"]["max_frames"], int(duration // 4) + 1)
        steps.append(_step("scene", "scene-ffmpeg", "一次解码成小缩略图（视频自身关键帧足够密时直接用）；"
                           "任一分块出现可见变化就开始一个新镜头",
                           est_seconds=duration * (0.005 if long_video else 0.02)))
        attach = route == "frames"
        steps.append(_step("keyframes", "ffmpeg",
                           f"R5：镜头 + 覆盖网格 + 重复画面去重，最多 {target} 帧"
                           + ("；先以拼图附带，单帧按需查看（drill）" if attach
                              else "；只用于 OCR/时间线（不附带）"),
                           est_seconds=target * float(est["frame_extract_seconds"]),
                           est_input_tokens=(frames or {}).get("est_tokens", 0) if attach else 0, frames=target))
        if attach:
            steps.append(_step("contact_sheets", "ffmpeg",
                               f"{frames['contact_sheets']} 张拼图，每张 {frames['per_sheet']} 格，带时间戳"
                               "（图片越少 token 越少；不超出密集图片限额）",
                               est_seconds=frames["contact_sheets"] * 0.5))
    density = float(probe.get("text_density") or 0)
    threshold = float(loc["ocr_text_density_threshold"])
    ocr_wanted = ocr_mode == "on" or (ocr_mode == "auto" and density >= threshold and want_frames)
    if ocr_mode == "off":
        steps.append(_step("ocr", None, "已用 --ocr off 关闭", status="skipped"))
    elif not ocr_wanted:
        why = (f"R4：text_density {density} < {threshold}" if want_frames
               else "原生路线：LLM 自己读取屏幕文字")
        steps.append(_step("ocr", None, why, status="skipped", hint="--ocr on"))
    else:
        ocr_id = first_installed(loc["ocr_chain"], manifest)
        n = (frames or {}).get("target_frames") or 30
        if ocr_id:
            secs = float(est["ocr_seconds_per_frame"].get(tier, 0.5))
            steps.append(_step("ocr", ocr_id, f"R4：屏幕文字（text_density {density} >= {threshold}）"
                               + ("；长视频只在变化点做" if long_video else "")
                               + "；OCR 是校对转写的证据",
                               alternatives=[m for m in loc["ocr_chain"] if m != ocr_id],
                               est_seconds=n * secs, est_input_tokens=n * int(est["ocr_tokens_per_frame"])))
        else:
            steps.append(_step("ocr", None, f"R4：text_density {density} >= {threshold}，但未安装 OCR 引擎",
                               status="needs_install", hint="vidroute install --capability ocr"))
            needs.append({"capability": "ocr", "why": "屏幕文字会有帮助（可选）",
                          "hint": "vidroute install --capability ocr", "optional": "true"})

    # ------------------------------------------------------------ native media
    if native:
        n = decision["native"]
        tc = n.get("transcode")
        segs = n["segments"]
        if tc is None:
            desc = "原文件在限额内：原样复制"
        elif tc.get("mode") == "copy":
            desc = "去掉音轨重新封装（模型听不到）"
        else:
            desc = (f"H.264 {tc['height']}p@{tc['fps']}fps"
                    + (f", {tc['video_kbps']} kbps" if tc.get("video_kbps") else f", CRF {tc.get('crf')}")
                    + (f" + AAC {tc['audio_kbps']} kbps" if tc.get("audio_kbps") else "，无音频"))
        if len(segs) > 1:
            desc += f"；切成 {len(segs)} 段，相邻重叠 {rules['delivery']['segment_overlap_seconds']} 秒" \
                    "（segments.json 记录全局偏移）"
        steps.append(_step("transcode", "ffmpeg", desc, est_seconds=(duration * float(est["transcode_rtf"])
                                                                     if tc and tc.get("mode") == "encode" else 2.0),
                           est_input_tokens=n["est_video_tokens"], uploads_raw_video=True,
                           segments=len(segs)))
    audio_att = decision.get("audio_attachment") or {}
    if audio_att.get("enabled"):
        steps.append(_step("audio_attachment", "ffmpeg",
                           f"16 kHz 单声道 MP3，{audio_att['kbps']} kbps"
                           + (f"，分 {audio_att['parts']} 段" if audio_att["parts"] > 1 else "")
                           + "，让模型能听（与原始视频一样需要用户同意）",
                           est_seconds=duration * 0.02, est_input_tokens=audio_att.get("est_tokens", 0)))

    steps.append(_step("timeline", None, "timeline.md 按全局时间合并转写、OCR 和帧", est_seconds=0.5))
    steps.append(_step("handoff", None, "给 Agent 的 SUMMARY.md、ANALYSIS_GUIDE.md 和 handoff.json", est_seconds=0.2))
    if long_video:
        steps.append(_step("long_video", None, f"R6：长于 {loc['long_video_minutes']} 分钟：帧网格更稀疏，"
                           "OCR 只在变化点做，ASR 分块且可续跑", status="note"))
    return steps, needs
