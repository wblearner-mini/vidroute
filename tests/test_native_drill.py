"""Native routes (D1/D2: copy, remux, transcode, segments with global offsets) and drill."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
pytestmark = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not on PATH")
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    sys.path.insert(0, str(ROOT / "tests"))
    import media as m

    root = tmp_path_factory.mktemp("media")
    return {"slides": m.make_slides(root / "slides.mp4"),
            "long": m.make_slides(root / "long.mp4", slides=12, slide_seconds=6.5)}


@pytest.fixture(autouse=True)
def fake(monkeypatch):
    monkeypatch.setenv("VIDROUTE_FAKE_ENGINES", "1")


def _schema(name):
    return json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))


def _streams(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout.split()
    return sorted(set(out))


def test_native_av_fits_as_is(media, tmp_path):
    jsonschema = pytest.importorskip("jsonschema")
    from vidroute.api import prepare

    env = prepare(str(media["slides"]), llm="gemini-3.8-flash", host="gemini-cli", out=str(tmp_path),
                  accept_raw_video_handoff=True)
    assert env.ok, env.error
    root = Path(env.data["out_dir"])
    h = env.data["handoff"]
    jsonschema.validate(h, _schema("handoff.schema.json"))
    assert h["route"] == "native-av" and h["uploads_raw_video"] and h["uploads_raw_audio"]
    assert [a["type"] for a in h["attachments"]] == ["video"]
    assert (root / h["attachments"][0]["path"]).stat().st_size == media["slides"].stat().st_size
    assert not any(w["code"] == "raw_video_handoff" for w in env.warnings)  # consent given: no nagging
    seg = json.loads((root / "segments.json").read_text(encoding="utf-8"))
    assert [s["offset_sec"] for s in seg["segments"]] == [0.0] and seg["includes_audio"]
    summary = (root / "SUMMARY.md").read_text(encoding="utf-8")
    assert "Uploads raw video: YES" in summary
    guide = (root / "ANALYSIS_GUIDE.md").read_text(encoding="utf-8")
    assert "You can hear the audio" in guide and "cannot hear" not in guide
    assert not (root / "frames").exists() and not (root / "contact-sheets").exists()


def test_native_v_drops_audio_and_needs_transcript(media, tmp_path):
    from vidroute.api import prepare

    env = prepare(str(media["slides"]), llm="qwen3.6-plus", host="qwen-code", out=str(tmp_path),
                  accept_raw_video_handoff=True)
    assert env.ok, env.error
    h = env.data["handoff"]
    assert h["route"] == "native-v" and h["uploads_raw_video"] and not h["uploads_raw_audio"]
    video = Path(env.data["out_dir"]) / h["attachments"][0]["path"]
    assert _streams(video) == ["video"]  # the model cannot hear: the audio track is not shipped
    assert h["transcript_role"] == "sole_source"
    assert {t["path"]: t["attach"] for t in h["text_context"]} == {"transcript.md": True}


def test_transcode_and_segments_keep_global_time(media, tmp_path):
    jsonschema = pytest.importorskip("jsonschema")
    from vidroute.api import llm_set, prepare

    assert llm_set("tiny-omni-1", modalities="text,image,video,audio", audio_in_video="yes", video_max_seconds=30,
                   video_max_mb=1, context_tokens=1_000_000).ok
    env = prepare(str(media["long"]), llm="tiny-omni-1", host="gemini-cli", task="minutes", out=str(tmp_path),
                  accept_raw_video_handoff=True)
    assert env.ok, env.error
    root = Path(env.data["out_dir"])
    h = env.data["handoff"]
    jsonschema.validate(h, _schema("handoff.schema.json"))
    seg = json.loads((root / "segments.json").read_text(encoding="utf-8"))
    parts = seg["segments"]
    assert len(parts) == 3 and seg["overlap_seconds"] == 3
    assert [p["offset_sec"] for p in parts] == [0.0, 25.0, 50.0] and parts[-1]["end"] == pytest.approx(78.0, abs=0.1)
    for p in parts:
        f = root / p["file"]
        assert f.stat().st_size / 1e6 <= seg["max_file_mb"]  # every part fits the per-file limit
        dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                    str(f)], capture_output=True, text=True).stdout)
        assert dur == pytest.approx(p["duration"], abs=0.3)
        assert _streams(f) == ["audio", "video"]
    assert [a["segment"]["offset_sec"] for a in h["attachments"]] == [0.0, 25.0, 50.0]
    assert h["budget"]["video_seconds"] == pytest.approx(84.0, abs=0.5)
    transcript = json.loads((root / "transcript.json").read_text(encoding="utf-8"))
    assert transcript["segments"][-1]["start"] > 70  # transcript stays in global time
    assert next(t for t in h["text_context"] if t["type"] == "transcript")["attach"]  # minutes need verbatim text


def test_prepare_falls_back_to_frames_when_files_do_not_fit(media, tmp_path, monkeypatch):
    from vidroute.api import llm_set, prepare
    from vidroute.core.prepare import native

    monkeypatch.setattr(native, "FIT_MARGIN", 1e-6)  # nothing fits
    assert llm_set("tiny-omni-2", modalities="text,image,video,audio", audio_in_video="yes", video_max_seconds=30,
                   video_max_mb=1, context_tokens=1_000_000).ok
    env = prepare(str(media["long"]), llm="tiny-omni-2", host="gemini-cli", out=str(tmp_path),
                  accept_raw_video_handoff=True)
    assert env.ok, env.error
    root = Path(env.data["out_dir"])
    h = env.data["handoff"]
    assert h["route"] == "frames" and not h["uploads_raw_video"]
    assert h["degraded_from"][0]["route"] == "native-av" and "exceed" in h["degraded_from"][0]["why"]
    assert any(w["code"] == "native_does_not_fit" for w in env.warnings)
    assert not (root / "native").exists() and not (root / "segments.json").exists()
    assert json.loads((root / "plan.json").read_text(encoding="utf-8"))["route"] == "frames"


def test_drill_ranges_use_global_time(media, tmp_path, capsys):
    from vidroute.api import drill, prepare
    from vidroute.cli import main

    env = prepare(str(media["long"]), llm="claude-opus-5-5", host="claude-code", out=str(tmp_path))
    assert env.ok, env.error
    out_dir = env.data["out_dir"]
    d = drill(out_dir, "00:30-00:45", asr=True, ocr=True)
    assert d.ok, d.error
    data = d.data
    assert data["range"] == [30.0, 45.0] and data["dir"] == "drill/0m30s-0m45s"
    assert data["frames"] and all(30.0 <= f["t"] < 45.0 for f in data["frames"])
    root = Path(out_dir)
    assert all((root / f["path"]).is_file() for f in data["frames"])
    assert [a["path"] for a in data["attachments"]] == ["drill/0m30s-0m45s/contact-sheets/sheet-01.jpg"]
    t = json.loads((root / "drill/0m30s-0m45s/transcript.json").read_text(encoding="utf-8"))
    assert t["segments"] and all(30.0 - 0.5 <= s["start"] < 45.0 for s in t["segments"])
    assert (root / "drill/0m30s-0m45s/ocr.md").is_file()
    fixed = drill(out_dir, "10-14", fps=2)
    assert fixed.ok and len(fixed.data["frames"]) == 8
    assert [f["t"] for f in fixed.data["frames"]][:2] == [10.25, 10.75]
    rc = main(["drill", out_dir, "--range", "20-25", "--max-frames", "2", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and 1 <= len(out["data"]["frames"]) <= 2
    assert drill(out_dir, "05:00-06:00").exit_code == 1  # starts after the end of the video
    assert drill(str(tmp_path), "0-5").error["code"] == "invalid_input"  # not an output folder


def test_frames_with_audio_track_needs_the_same_consent(media, tmp_path):
    from vidroute.api import llm_set, prepare

    assert llm_set("ears-and-eyes-1", modalities="text,image,audio", context_tokens=1_000_000).ok
    kw = dict(llm="ears-and-eyes-1", host="gemini-cli", out=str(tmp_path))
    env = prepare(str(media["slides"]), **kw)
    assert env.ok, env.error
    h = env.data["handoff"]
    assert h["route"] == "frames" and not h["uploads_raw_audio"] and h["transcript_role"] == "sole_source"
    assert any(w["code"] == "audio_not_attached" for w in env.warnings)
    env = prepare(str(media["slides"]), accept_raw_video_handoff=True, **kw)
    assert env.ok, env.error
    h = env.data["handoff"]
    audio = [a for a in h["attachments"] if a["type"] == "audio"]
    assert len(audio) == 1 and audio[0]["path"].startswith("audio/attach/part-01.")
    assert h["uploads_raw_audio"] and not h["uploads_raw_video"] and h["transcript_role"] == "reference"
    assert _streams(Path(env.data["out_dir"]) / audio[0]["path"]) == ["audio"]
    guide = (Path(env.data["out_dir"]) / "ANALYSIS_GUIDE.md").read_text(encoding="utf-8")
    assert "You can hear the audio** (attached as a separate audio file)" in guide and "cannot hear" not in guide
