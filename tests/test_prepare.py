"""prepare: the local perception pipeline (frames/text routes) and the files it hands over."""

import json
import math
import random
import shutil
import sys
from pathlib import Path

import pytest

from vidroute.core.prepare import handoff as ho
from vidroute.core.prepare import keyframes as kf
from vidroute.core.prepare import media as pm
from vidroute.core.prepare import ocr as ocr_mod
from vidroute.core.prepare import timeline
from vidroute.core.prepare import transcript as tr

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not on PATH")
ROOT = Path(__file__).resolve().parents[1]
RULES = {"frames": {"change_threshold": 10, "min_interval_seconds": 2}}


# ------------------------------------------------------------- transcript


def test_make_chunks_merges_splits_and_pads():
    chunks = tr.make_chunks([[1.0, 2.0], [2.3, 3.0], [10.0, 70.0], [80.0, 80.1]], duration=90, max_len=25)
    assert chunks[0] == [0.85, 3.15]  # merged across a short gap, padded by 0.15 s
    assert [c for c in chunks if 9 < c[0] < 71] == [[9.85, 34.85], [34.85, 59.85], [59.85, 70.15]]
    assert chunks[-1] == [79.85, 80.25]
    assert all(c[1] - c[0] <= 25.0 + 1e-6 for c in chunks)


def test_language_detection_and_source_merge():
    segs = [{"start": 0, "end": 10, "text": "你好", "lang": "zh"}, {"start": 10, "end": 13, "text": "hi", "lang": "en"},
            {"start": 13, "end": 40, "text": "", "lang": "nospeech"}]
    assert tr.detect_language(segs) == "zh"
    assert tr.detect_language([]) is None
    assert tr.normalize_lang("eng") == "en" and tr.normalize_lang("chi") == "zh" and tr.normalize_lang("yue") == "yue"
    asr = [{"start": 0.0, "end": 5.0, "text": "a"}]
    cues = [{"start": 0.5, "end": 4.0, "text": "dup"}, {"start": 10.0, "end": 12.0, "text": "only in subs"}]
    assert [s["text"] for s in tr.merge_sources(asr, cues)] == ["a", "only in subs"]


def test_srt_round_trip_and_coverage():
    text = "1\r\n00:00:01,000 --> 00:00:02,500\r\n<i>Hello</i>\r\nworld\r\n\r\n2\n00:01:00.250 --> 00:01:02,000\n{\\an8}Top\n"
    cues = pm.parse_srt(text)
    assert cues == [{"start": 1.0, "end": 2.5, "text": "Hello world"}, {"start": 60.25, "end": 62.0, "text": "Top"}]
    assert pm.parse_srt(pm.format_srt(cues)) == cues
    assert pm.coverage(cues, [[0.0, 2.0], [60.0, 62.0]]) == pytest.approx((1.0 + 1.75) / 4.0)
    assert pm.coverage([], []) == 0.0


def test_fit_keeps_aspect_and_never_upscales():
    assert pm.fit(1920, 1080, 1280, 1280) == (1280, 720)
    assert pm.fit(640, 360, 1280, 1280) == (640, 360)
    assert pm.fit(1080, 1920, 400, 400, upscale=True) == (224, 400)


# -------------------------------------------------------------- keyframes

W, H = 64, 36


def _img(base: int = 200, block: tuple[int, int] | None = None, noise: int = 0, seed: int = 0) -> bytes:
    rnd = random.Random(seed)
    px = []
    for y in range(H):
        for x in range(W):
            v = base
            if block and block[0] <= x < block[0] + 8 and block[1] <= y < block[1] + 8:
                v = 40
            if noise:
                v = max(0, min(255, v + rnd.randint(-noise, noise)))
            px.append(v)
    return bytes(px)


def _cands(pictures: list[bytes], per: int = 20, rate: float = 4.0) -> list[kf.Candidate]:
    out = []
    for i, pic in enumerate(pictures):
        for j in range(per):
            t = (i * per + j) / rate
            px = _img(noise=2, seed=i * per + j) if pic == b"A" else pic
            out.append(kf.Candidate(t, px, kf.signature(px, W, H)))
    return out


def test_small_size_and_cell_change():
    assert kf.small_size(1920, 1080) == (64, 36)
    assert kf.small_size(1080, 1920) == (36, 64)
    assert kf.small_size(640, 480) == (64, 48)
    a, b = _img(), _img(block=(16, 8))
    assert kf.cell_change(a, a, W, H) == 0
    assert kf.cell_change(a, b, W, H) == 160
    assert kf.cell_change(a, _img(noise=3, seed=1), W, H) <= 3
    sa, sb = kf.signature(a, W, H), kf.signature(b, W, H)
    assert len(sa) == (W // 4) * (H // 4) and kf.sig_gap(sa, sb) == 160
    for seed in range(5):  # the signature gap is a lower bound of the cell change
        n = _img(block=(seed * 4, 4), noise=30, seed=seed)
        assert kf.sig_gap(sa, kf.signature(n, W, H)) <= kf.cell_change(a, n, W, H) + 1


def test_shots_dedupe_and_budget():
    b, c = _img(block=(0, 0)), _img(block=(40, 20))
    cands = _cands([b"A", b, b"A", c])  # A (noisy, static) - B - A again - C; 5 s each
    shots = kf.shots_from(cands, (W, H), 10, end=20.0)
    assert [round(s.start, 2) for s in shots] == [0.0, 5.0, 10.0, 15.0]  # noise never starts a shot
    unique = kf.dedupe(shots, cands, (W, H), 10)
    assert len(unique) == 3 and unique[0].repeats == [[10.0, 15.0]]
    frames, stats = kf.select(cands, (W, H), RULES, target=10, start=0.0, end=20.0)
    assert stats["repeats_folded"] == 1 and len(frames) == 3
    assert frames[0].repeats == [[10.0, 15.0]] and frames[0].start == 0.0 and 0 < frames[0].t < 5
    two, _ = kf.select(cands, (W, H), RULES, target=2, start=0.0, end=20.0)
    assert [f.start for f in two] == [0.0, 15.0]  # coverage: one per half, longest on screen wins


def test_choose_merges_picks_closer_than_min_gap():
    s1 = kf.Shot(0, 0, 0.0, end=1.0, t=0.5)
    s2 = kf.Shot(1, 1, 1.0, end=9.0, t=1.5)
    s3 = kf.Shot(2, 2, 9.0, end=10.0, t=9.5)
    out = kf.choose([s1, s2, s3], target=10, start=0.0, end=10.0, min_gap=2.0)
    assert out == [s2, s3]


# --------------------------------------------------------------- timeline


def _kf(i: int, start: float, end: float, repeats=None) -> kf.Keyframe:
    return kf.Keyframe(i, (start + end) / 2, start, end, 50.0, repeats or [], file=f"{i:03d}.jpg")


def test_timeline_sections_follow_pictures():
    frames = [_kf(1, 0, 5, [[10.0, 15.0]]), _kf(2, 5, 10)]
    secs = timeline.sections_for(frames, 20.0)
    assert [(s.start, s.end, s.frame.index if s.frame else None, s.again) for s in secs] == [
        (0, 5, 1, False), (5, 10, 2, False), (10.0, 15.0, 1, True), (15.0, 20.0, None, False)]
    segs = [{"start": 1.0, "end": 2.0, "text": "one"}, {"start": 11.0, "end": 12.0, "text": "two"},
            {"start": 16.0, "end": 17.0, "text": "three"}]
    ocr = [{"frame": 1, "lines": [{"text": "Title"}]}, {"frame": 2, "lines": [{"text": "Title"}, {"text": "New"}]}]
    built = timeline.build(frames, 20.0, segs, ocr)
    assert [[x["text"] for x in s.said] for s in built] == [["one"], [], ["two"], ["three"]]
    assert built[1].shown == ["New"]  # repeated line not shown again
    text = timeline.render(built, "v.mp4", "frames", "sole_source", "ocr-x")
    assert "(again)" in text and "no keyframe for this span" in text and "- [00:16] three" in text


def test_timeline_without_video_groups_by_minute():
    secs = timeline.build([], 130.0, [{"start": 70.0, "end": 71.0, "text": "x"}], None)
    assert [(s.start, s.end) for s in secs] == [(0.0, 60.0), (60.0, 120.0), (120.0, 130.0)]
    assert secs[1].said and "## 01:00–02:00" in timeline.render(secs, "a.m4a", "text", "sole_source", None)


# ---------------------------------------------------------------- handoff


def test_render_template_conditions():
    tmpl = ("<!-- header with if:route=x and endif words -->\n# {{title}}\n"
            "<!-- if:route=frames,text -->\nlocal\n<!-- endif -->\n<!-- if:route=native-av -->\nnative\n<!-- endif -->\n"
            "<!-- if:has:ocr -->\nocr\n<!-- endif -->\n<!-- if:!has:ocr -->\nno ocr\n<!-- endif -->\nend {{missing}}\n")
    out = ho.render_template(tmpl, {"title": "T"}, {"ocr": False}, "text")
    assert out == "# T\nlocal\nno ocr\nend\n"


@pytest.mark.parametrize("route", ["native-av", "native-v", "frames", "text"])
def test_analysis_guide_template_renders_for_every_route(route):
    tmpl = (ROOT / "references" / "analysis-guide.template.md").read_text(encoding="utf-8")
    out = ho.render_template(tmpl, {"video_name": "v.mp4", "route": route}, {"transcript": True}, route)
    assert "{{" not in out and "<!--" not in out
    assert "data, not instructions" in out and "Correction list" in out
    assert ("cannot hear" in out) == (route in ("frames", "text", "native-v"))
    assert ("cannot see the pictures" in out) == (route == "text")


def test_text_tokens_estimate():
    assert ho.text_tokens("你好世界") == 5
    assert 2 <= ho.text_tokens("hello world") <= 4


def test_ocr_reading_order():
    def line(text, x, y, h=20):
        return {"text": text, "box": [[x, y], [x + 50, y], [x + 50, y + h], [x, y + h]]}

    lines = [line("c", 0, 100), line("b", 300, 12), line("a", 10, 10)]
    assert [ln["text"] for ln in ocr_mod.reading_order(lines)] == ["a", "b", "c"]


# ------------------------------------------------------- end to end (fake)


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg/ffprobe not on PATH")
    sys.path.insert(0, str(ROOT / "tests"))
    import media as m

    return m.build_all(tmp_path_factory.mktemp("media"))


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setenv("VIDROUTE_FAKE_ENGINES", "1")


def _schema(name):
    return json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))


@needs_ffmpeg
def test_frames_route_end_to_end(media, tmp_path, fake):
    jsonschema = pytest.importorskip("jsonschema")
    from vidroute.api import prepare

    env = prepare(str(media["slides"]), llm="claude-opus-5-5", host="claude-code", task="minutes", out=str(tmp_path))
    assert env.ok, env.error
    root = Path(env.data["out_dir"])
    assert env.data["route"] == "frames" and root.parent == tmp_path.resolve()
    for name in ("SUMMARY.md", "ANALYSIS_GUIDE.md", "handoff.json", "manifest.json", "plan.json", "transcript.md",
                 "transcript.srt", "transcript.json", "timeline.md", "ocr.md", "ocr.json", "audio/16k.wav",
                 "contact-sheets/index.md"):
        assert (root / name).is_file(), name
    handoff = json.loads((root / "handoff.json").read_text(encoding="utf-8"))
    jsonschema.validate(handoff, _schema("handoff.schema.json"))
    plan = json.loads((root / "plan.json").read_text(encoding="utf-8"))
    jsonschema.validate(plan, _schema("plan.schema.json"))
    frames = sorted((root / "frames").glob("*.jpg"))
    assert len(frames) == 4  # four distinct slides, no crossfade frames
    assert len(frames) <= plan["delivery"]["frames"]["target_frames"] <= plan["effective"]["image_limits"]["max_images"]
    sheets = sorted((root / "contact-sheets").glob("sheet-*.jpg"))
    assert len(sheets) == math.ceil(len(frames) / 12)
    assert ho._jpeg_size(sheets[0])[0] > ho._jpeg_size(frames[0])[0] / 2
    assert handoff["uploads_raw_video"] is False and handoff["route"] == "frames"
    assert [a["path"] for a in handoff["attachments"]] == ["contact-sheets/sheet-01.jpg"]
    assert {t["path"]: t["attach"] for t in handoff["text_context"]} == {
        "transcript.md": True, "ocr.md": True, "timeline.md": False, "contact-sheets/index.md": False}
    assert all((root / a["path"]).is_file() for a in handoff["attachments"] + handoff["on_demand"])
    transcript = json.loads((root / "transcript.json").read_text(encoding="utf-8"))
    assert transcript["source"] == "embedded_subtitle" and transcript["language"] == "en"  # R1
    assert [s["start"] for s in transcript["segments"]] == pytest.approx([0.5, 6.5, 12.5, 18.5], abs=0.05)  # global time
    assert "fake-ocr" in (root / "ocr.md").read_text(encoding="utf-8")  # fakes can never pass for real engines
    guide = (root / "ANALYSIS_GUIDE.md").read_text(encoding="utf-8")
    assert "cannot hear" in guide and "{{" not in guide
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert {e["capability"] for e in manifest["engines"]} >= {"ocr", "vad", "scene"}
    assert all(e.get("license") for e in manifest["engines"])
    assert not any(f["path"].startswith(".work") for f in manifest["files"])


@needs_ffmpeg
def test_frame_budget_follows_max_images(media, tmp_path, fake):
    from vidroute.api import llm_set, prepare

    assert llm_set("tiny-vision-1", modalities="text,image", max_images=2, context_tokens=32000).ok
    env = prepare(str(media["nature"]), llm="tiny-vision-1", host="claude-code", out=str(tmp_path))
    assert env.ok, env.error
    frames = list((Path(env.data["out_dir"]) / "frames").glob("*.jpg"))
    assert 1 <= len(frames) <= 2


@needs_ffmpeg
def test_text_route_without_audio_or_speech(media, tmp_path, fake):
    from vidroute.api import prepare

    for key, why in (("no_audio", "no audio track"), ("silent", "no speech detected")):
        env = prepare(str(media[key]), llm="deepseek-v4-pro", host="opencode", out=str(tmp_path))
        assert env.ok, env.error
        root = Path(env.data["out_dir"])
        assert env.data["route"] == "text"
        step = next(s for s in env.data["steps"] if s["id"] == "transcript")
        assert step["status"] == "skipped" and why in step["detail"]
        assert not (root / "transcript.md").exists() and not (root / "contact-sheets").exists()
        handoff = env.data["handoff"]
        assert handoff["attachments"] == [] and handoff["transcript_role"] == "none"
        assert [t["path"] for t in handoff["text_context"] if t["attach"]] == ["timeline.md"]
        assert "There is no transcript" in (root / "ANALYSIS_GUIDE.md").read_text(encoding="utf-8")


@needs_ffmpeg
def test_rerun_resumes_and_reuses_plan(media, tmp_path, fake):
    from vidroute.api import plan, prepare

    video = str(media["slides_mkv"])
    assert plan(video, "claude-opus-5-5", "claude-code", out=str(tmp_path)).ok
    first = prepare(video, out=str(tmp_path))
    assert first.ok and first.data["plan_source"] == "out_dir"
    assert any(w["code"] == "plan_reused" for w in first.warnings)
    second = prepare(video, out=str(tmp_path))
    statuses = {s["id"]: s["status"] for s in second.data["steps"]}
    assert statuses["transcript"] == statuses["keyframes"] == statuses["contact_sheets"] == statuses["ocr"] == "cached"
    assert second.data["handoff"]["attachments"] == first.data["handoff"]["attachments"]


@needs_ffmpeg
def test_native_route_requires_consent(media, tmp_path, fake, capsys):
    from vidroute.api import prepare
    from vidroute.cli import main

    env = prepare(str(media["slides"]), llm="gemini-3.8-flash", host="gemini-cli", out=str(tmp_path))
    assert env.exit_code == 3 and env.error["code"] == "raw_video_handoff"
    assert "--accept-raw-video-handoff" in env.error["hint"]
    assert (Path(env.data["plan_path"])).is_file()
    rc = main(["prepare", str(media["slides"]), "--llm", "gemini-3.8-flash", "--host", "gemini-cli",
               "--out", str(tmp_path), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 3 and out["error"]["code"] == "raw_video_handoff" and out["data"]["uploads_raw_video"] is True
    # Privacy preference keeps the raw video local: frames route, no consent needed.
    env = prepare(str(media["slides"]), llm="gemini-3.8-flash", host="gemini-cli", prefer="privacy",
                  out=str(tmp_path))
    assert env.ok and env.data["route"] == "frames"
    assert env.data["handoff"]["degraded_from"][0]["route"] == "native-av"


@needs_ffmpeg
def test_unicode_paths_and_cli_json(media, tmp_path, fake, capsys):
    from vidroute.cli import main

    out = tmp_path / "out dir 输出"
    rc = main(["run", str(media["unicode"]), "--llm", "claude-opus-5-5", "--host", "claude-code", "--out", str(out),
               "--json"])
    env = json.loads(capsys.readouterr().out)
    assert rc == 0 and env["ok"], env["error"]
    root = Path(env["data"]["out_dir"])
    assert root.parent == out.resolve() and root.name.startswith("会议-录屏-")
    assert (root / "SUMMARY.md").read_text(encoding="utf-8").startswith("# vidroute — 会议 录屏.mp4")


@needs_ffmpeg
def test_sampler_uses_dense_keyframes_and_bounds_all_intra(tmp_path):
    import subprocess

    from vidroute.core.context import Ctx

    def clip(name, gop):
        out = tmp_path / name
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc2=size=160x90:rate=10:duration=20", "-c:v", "libx264", "-g", str(gop),
                        "-keyint_min", str(gop), "-sc_threshold", "0", "-pix_fmt", "yuv420p", str(out)], check=True)
        return out

    rules = {"frames": {"max_candidates": 360, "max_interval_seconds": 30}}
    cands, meta = kf.sample(clip("dense.mp4", 5), 20.0, (64, 36), rules, Ctx())
    assert meta["method"] == "keyframes" and len(cands) == 40 and cands[1].t == pytest.approx(0.5)
    rules = {"frames": {"max_candidates": 10, "max_interval_seconds": 30}}
    cands, meta = kf.sample(clip("intra.mp4", 1), 20.0, (64, 36), rules, Ctx())
    assert meta["keyframes_truncated"] and meta["method"].startswith("decoded@")
    assert len(cands) == pytest.approx(10, abs=1) and cands[-1].t < 20.0
