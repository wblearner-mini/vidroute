"""Two-layer routing: table-driven over LLM profile x host profile x video features x prefer x task.

Acceptance: D1–D4 and every degradation are covered; the same
video gets different, explainable plans under different LLM/host profiles.
"""

import pytest

from vidroute.core import config
from vidroute.core.context import Ctx
from vidroute.core.errors import UsageError
from vidroute.core.llm import manage
from vidroute.core.llm import profiles as P
from vidroute.core.llm.resolve import resolve, resolve_host, resolve_llm
from vidroute.core.plan import route
from vidroute.core.plan.rules import budget_tokens, load_rules

RULES = load_rules()


def video(duration=120.0, size_mb=8.0, codec="h264", fmt="mov,mp4,m4a,3gp,3g2,mj2", has_audio=True, speech=0.6,
          text=0.5, height=720, fps=30.0, audio_codec="aac"):
    return {
        "duration": duration, "size_mb": size_mb, "format": fmt, "has_audio": has_audio, "has_video": True,
        "video": {"codec": codec, "width": int(height * 16 / 9), "height": height, "fps": fps},
        "audio": [{"codec": audio_codec}] if has_audio else [], "speech_ratio": speech if has_audio else 0.0,
        "text_density": text, "text_subtitles": [],
    }


def decide(llm, host, v=None, prefer="quality", task="summary", allow_raw=True, budget=None, forced=None, **res_kw):
    res = resolve(llm, host, ctx=Ctx(), **res_kw)
    b = budget if budget is not None else budget_tokens(res["effective"]["context_tokens"], RULES)
    return route.decide(v or video(), res, RULES, task, prefer, allow_raw, b, forced=forced)


CASES = [
    # id, llm, host, video kwargs, prefer, task, expected route, expected degraded_from routes
    ("D1 gemini on gemini-cli", "gemini-3.8-flash", "gemini-cli", {}, "quality", "summary", "native-av", []),
    ("D1 qwen omni on qwen-code", "qwen3.8-omni-flash", "qwen-code", {"size_mb": 5}, "quality", "summary", "native-av", []),
    ("D2 qwen vision (no audio) on qwen-code", "qwen3.6-plus", "qwen-code", {}, "quality", "summary", "native-v", []),
    ("D3 claude on claude-code", "claude-opus-5-5", "claude-code", {}, "quality", "summary", "frames", []),
    ("D3 gpt on codex", "gpt-5.6-sol", "codex-cli", {}, "quality", "summary", "frames", []),
    ("D3 host-limited gemini on claude-code", "gemini-3.8-flash", "claude-code", {}, "quality", "summary", "frames", []),
    ("D3 gemini on opencode (images only)", "gemini-3.8-flash", "opencode", {}, "quality", "summary", "frames", []),
    ("D3 frame-sampled host", "gemma-4-31b-it", "local-mistralrs", {}, "quality", "summary", "frames", []),
    ("D4 text-only model", "deepseek-v4-pro", "opencode", {}, "quality", "summary", "text", []),
    ("D1 -> D3 privacy", "gemini-3.8-flash", "gemini-cli", {}, "privacy", "summary", "frames", ["native-av"]),
    ("D2 -> D3 privacy", "qwen3.6-plus", "qwen-code", {}, "privacy", "summary", "frames", ["native-v"]),
    ("D1 -> D3 too many segments", "gemini-3.8-flash", "gemini-cli", {"duration": 4 * 3600, "size_mb": 3000},
     "quality", "summary", "frames", ["native-av"]),
    ("D1 Qwen3-Omni-Flash 150 s limit -> segments", "qwen3-omni-flash", "qwen-code", {"duration": 600, "size_mb": 40},
     "quality", "summary", "native-av", []),
]


@pytest.mark.parametrize("case", CASES, ids=[c[0] for c in CASES])
def test_route_table(case):
    _, llm, host, vkw, prefer, task, expected, degraded = case
    d = decide(llm, host, video(**vkw), prefer=prefer, task=task)
    assert d["route"] == expected, d["reasons"] + [str(x) for x in d["degraded_from"]]
    assert [g["route"] for g in d["degraded_from"]] == degraded
    assert d["uploads_raw_video"] == expected.startswith("native")
    if prefer == "privacy":
        assert not d["uploads_raw_audio"] and not d["audio_attachment"]["enabled"]
        assert d["transcript"]["role"] == "sole_source"
    assert d["reasons"], "every decision must be explained"


def test_allow_raw_video_handoff_false_degrades():
    d = decide("gemini-3.8-flash", "gemini-cli", allow_raw=False)
    assert d["route"] == "frames"
    assert "allow_raw_video_handoff" in d["degraded_from"][0]["why"]


def test_native_v_without_image_falls_to_text():
    d = decide("qwen3.6-plus", "qwen-code", prefer="privacy", modalities="text,video")
    assert d["route"] == "text"
    assert [g["route"] for g in d["degraded_from"]] == ["native-v"]


def test_frames_to_text_when_over_budget_and_prefer_cost():
    d = decide("claude-opus-5-5", "claude-code", video(duration=3600), prefer="cost", budget=500)
    assert d["route"] == "text" and d["degraded_from"][0]["route"] == "frames"


def test_full_chain_native_frames_text_with_prefer_cost():
    d = decide("gemini-3.8-flash", "gemini-cli", video(duration=600), prefer="cost", budget=100)
    assert d["route"] == "text"
    assert [g["route"] for g in d["degraded_from"]] == ["native-av", "frames"]


def test_over_budget_with_quality_asks_for_confirmation():
    d = decide("gemini-3.8-flash", "gemini-cli", video(duration=600), prefer="quality", budget=100)
    assert d["route"] == "native-av"
    assert {c["code"] for c in d["needs_confirmation"]} >= {"over_budget", "raw_video_handoff"}


def test_native_always_requires_handoff_consent():
    d = decide("gemini-3.8-flash", "gemini-cli")
    assert any(c["code"] == "raw_video_handoff" for c in d["needs_confirmation"])


def test_host_limited_gives_delegate_suggestion():
    d = decide("gemini-3.8-flash", "claude-code")
    assert d["suggestions"] and d["suggestions"][0]["route"] == "delegate"
    assert any("受宿主限制，而非模型限制" in r for r in d["reasons"])
    assert not decide("claude-opus-5-5", "claude-code")["suggestions"]


def test_segmentation_keeps_global_offsets_and_overlap():
    d = decide("qwen3-omni-flash", "qwen-code", video(duration=600, size_mb=40))
    segs = d["native"]["segments"]
    assert len(segs) > 1 and segs[0][0] == 0.0 and segs[-1][1] == 600.0
    overlap = RULES["delivery"]["segment_overlap_seconds"]
    for (a0, a1), (b0, b1) in zip(segs, segs[1:]):
        assert a1 - b0 == pytest.approx(overlap, abs=0.01)  # neighbours overlap
        assert a1 - a0 <= 150.0 + 1e-6  # Qwen3-Omni-Flash: 150 s per video


def test_transcode_fits_size_limit():
    d = decide("gemini-3.8-flash", "gemini-cli", video(duration=900, size_mb=400, height=1080))
    n = d["native"]
    assert n["transcode"]["mode"] == "encode" and n["per_file_mb"] <= n["transcode"]["max_file_mb"]
    assert n["transcode"]["height"] <= 720


def test_incompatible_codec_is_transcoded_even_if_small():
    d = decide("gemini-3.8-flash", "gemini-cli", video(codec="hevc", fmt="matroska,webm", size_mb=3))
    assert d["native"]["transcode"]["mode"] == "encode"


def test_native_v_drops_audio_track():
    d = decide("qwen3.6-plus", "qwen-code", video(size_mb=4))
    assert d["native"]["transcode"] == {"mode": "copy", "drop_audio": True}
    assert d["transcript"]["role"] == "sole_source" and d["transcript"]["attach"]


@pytest.mark.parametrize("task,duration,attach", [("summary", 120, False), ("minutes", 120, True),
                                                   ("transcript", 120, True), ("summary", 1200, True)])
def test_d1_transcript_attach_rules(task, duration, attach):
    d = decide("gemini-3.8-flash", "gemini-cli", video(duration=duration, size_mb=10), task=task)
    assert d["route"] == "native-av" and d["transcript"]["role"] == "reference"
    assert d["transcript"]["attach"] is attach


def test_no_speech_means_no_transcript_role():
    d = decide("claude-opus-5-5", "claude-code", video(speech=0.0))
    assert d["transcript"]["role"] == "none"


def test_frames_budget_respects_max_images():
    d = decide("some-unknown-model", "unknown", video(duration=3600))
    assert d["frames"]["max_frames"] == 20  # conservative default profile


def test_frames_plus_audio_variant():
    d = decide("custom-audio-model", "codex-cli", modalities="text,image,audio")
    assert d["route"] == "frames" and d["audio_attachment"]["enabled"]
    assert d["transcript"]["role"] == "reference" and d["uploads_raw_audio"]


def test_forced_route_records_degradation_and_rejects_impossible():
    d = decide("gemini-3.8-flash", "gemini-cli", forced="frames")
    assert d["route"] == "frames" and d["degraded_from"][0]["why"].startswith("--route frames")
    with pytest.raises(UsageError):
        decide("claude-opus-5-5", "claude-code", forced="native-av")


def test_same_video_different_plans():
    v = video()
    routes = {decide(llm, host, v)["route"] for llm, host in [("gemini-3.8-flash", "gemini-cli"),
                                                               ("qwen3.6-plus", "qwen-code"),
                                                               ("claude-opus-5-5", "claude-code"),
                                                               ("deepseek-v4-pro", "opencode")]}
    assert routes == {"native-av", "native-v", "frames", "text"}


# --------------------------------------------------------- profile resolution


def test_profile_specificity_and_prefixes():
    assert resolve_llm("openrouter/qwen/qwen3.8-omni-flash:batch")["matched"] == "qwen3.8-omni-flash*"
    assert resolve_llm("qwen3.8-flash")["matched"] == "qwen3.8-flash*"
    assert resolve_llm("kimi-k2.6")["matched"] == "kimi-k2.[5-9]*"  # more specific than kimi-k2*
    assert resolve_llm("kimi-k2-thinking")["matched"] == "kimi-k2*"
    assert resolve_llm("us.anthropic.claude-opus-5-5-v1:0")["matched"] == "claude-*"


def test_unknown_model_is_conservative_with_warning():
    ctx = Ctx()
    r = resolve_llm("totally-new-model-9", ctx=ctx)
    assert r["matched"] == "*" and r["modalities_in"] == ["text", "image"]
    assert r["sources"]["modalities_in"] == "default"
    assert any(w["code"] == "unknown_model" for w in ctx.warnings)


def test_priority_flag_over_config_over_builtin():
    manage.llm_set("claude-opus-5-5", modalities="text,image,video", video_max_seconds=60)
    r = resolve_llm("claude-opus-5-5")
    assert "video" in r["modalities_in"] and r["sources"]["modalities_in"] == "config"
    assert r["video"]["max_seconds"] == 60
    r = resolve_llm("claude-opus-5-5", modalities="text")
    assert r["modalities_in"] == ["text"] and r["sources"]["modalities_in"] == "flag"
    assert manage.unset("llm_profiles", "claude-opus-5-5")
    assert resolve_llm("claude-opus-5-5")["sources"]["modalities_in"] == "builtin"


def test_provider_cache_sits_between_config_and_builtin(isolated_home):
    from vidroute.core.util.jsonio import write_json_atomic

    write_json_atomic(P.provider_cache_path(), {"provider": "openrouter", "models": {
        "brand-new-omni": {"input_modalities": ["text", "image", "video", "file"], "context_length": 262144}}})
    ctx = Ctx()
    r = resolve_llm("vendor/brand-new-omni", ctx=ctx)
    assert r["modalities_in"] == ["text", "image", "video"] and r["sources"]["modalities_in"] == "provider"
    assert r["context_tokens"] == 262144
    assert r["video"]["max_seconds"] == 300  # unknown limits -> conservative
    assert any(w["code"] == "video_limits_unknown" for w in ctx.warnings)


def test_audio_in_video_flag():
    r = resolve("qwen3.6-plus", "qwen-code", audio_in_video="yes")
    assert r["effective"]["audio_in_video"] is True


def test_host_aliases_unknown_and_override():
    assert resolve_host("codex")["id"] == "codex-cli"
    ctx = Ctx()
    assert resolve_host("my-own-host", ctx)["id"] == "unknown"
    assert any(w["code"] == "unknown_host" for w in ctx.warnings)
    manage.host_set("claude-code", attach_video="yes", video_delivery="native")
    h = resolve_host("claude-code")
    assert h["can_attach_video"] and h["video_delivery"] == "native" and h["source"] == "config"
    manage.unset("host_profiles", "claude-code")


def test_host_detected_from_environment(monkeypatch):
    for var, _ in P.HOST_ENV_MARKERS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GEMINI_CLI", "1")
    ctx = Ctx()
    h = resolve_host(None, ctx)
    assert h["id"] == "gemini-cli" and h["source"] == "detected:GEMINI_CLI"
    assert any(w["code"] == "host_detected" for w in ctx.warnings)


def test_budget_from_config():
    assert budget_tokens(1_000_000, RULES) == 500_000
    config.set_value("budget_fraction", 0.1)
    assert budget_tokens(1_000_000, RULES) == 100_000
    config.set_value("budget_tokens", 4242)
    assert budget_tokens(1_000_000, RULES) == 4242
