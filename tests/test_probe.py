"""probe: ffprobe parsing, picture heuristics, and end-to-end runs on synthetic media."""

import json
import shutil
import time
from pathlib import Path

import pytest

from vidroute.core.context import Ctx
from vidroute.core.probe import parse_ffprobe, probe, sample_times, speech_windows
from vidroute.core.probe import frames as fr

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not on PATH")
ROOT = Path(__file__).resolve().parents[1]


def test_parse_ffprobe_rotation_subtitles_and_cover_art(tmp_path):
    f = tmp_path / "x.mp4"
    f.write_bytes(b"0" * 10)
    data = {
        "format": {"duration": "61.5", "size": "1048576", "format_name": "mov,mp4,m4a,3gp,3g2,mj2", "bit_rate": "1000"},
        "streams": [
            {"codec_type": "video", "codec_name": "mjpeg", "disposition": {"attached_pic": 1}, "width": 300, "height": 300},
            {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080, "avg_frame_rate": "30000/1001",
             "side_data_list": [{"rotation": -90}]},
            {"codec_type": "audio", "codec_name": "aac", "channels": 2, "sample_rate": "48000", "tags": {"language": "chi"}},
            {"codec_type": "subtitle", "codec_name": "mov_text", "index": 3, "tags": {"language": "eng"}},
            {"codec_type": "subtitle", "codec_name": "hdmv_pgs_subtitle", "index": 4},
        ],
    }
    info = parse_ffprobe(data, f)
    assert info["duration"] == 61.5 and info["size_mb"] == 1.05
    assert info["video"]["codec"] == "h264"  # cover art ignored
    assert (info["video"]["width"], info["video"]["height"]) == (1080, 1920)  # rotated portrait
    assert info["video"]["fps"] == 29.97
    assert [s["codec"] for s in info["text_subtitles"]] == ["mov_text"]
    assert info["subtitles"][1]["image_based"] is True


def test_sampling_helpers():
    assert sample_times(80, 8)[0] == 5.0 and sample_times(80, 8)[-1] == 75.0
    assert speech_windows(60) == [[0.0, 60.0]]
    wins = speech_windows(3600)
    assert len(wins) == 6 and all(e - s == 20.0 for s, e in wins)


def _frame(fill):
    return bytes(fill(x, y) for y in range(fr.H) for x in range(fr.W))


def test_heuristics_on_synthetic_frames():
    blank = _frame(lambda x, y: 255)
    # black "glyphs": 2-px strokes every 6 px in three text bands
    text = _frame(lambda x, y: 0 if (y // 20) % 3 == 1 and 20 <= x < 300 and x % 6 < 2 else 255)
    gradient = _frame(lambda x, y: (x + y) % 256)
    assert fr.text_area(blank) == 0.0
    assert fr.text_area(text) > 0.1
    assert fr.text_area(gradient) == 0.0
    stats = fr.frame_stats([blank, text, blank])
    assert stats["text_density"] == pytest.approx(1 / 3, abs=0.01)
    assert stats["visual_change"] > 0
    assert fr.hamming(fr.dhash(text), fr.dhash(text)) == 0
    assert fr.hamming(fr.dhash(text), fr.dhash(gradient)) > 10


def test_text_stats_from_boxes_filters_non_text():
    results = [
        {"width": 640, "height": 360, "boxes": [[160, 313, 480, 341]]},  # subtitle line
        {"width": 640, "height": 360, "boxes": [[58, 0, 639, 359]]},  # whole-frame blob
        {"width": 640, "height": 360, "boxes": []},
    ]
    stats = fr.text_stats_from_boxes(results)
    assert stats["text_density"] == pytest.approx(1 / 3, abs=0.01) and 0 < stats["text_area"] < 0.05


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg/ffprobe not on PATH")
    import sys

    sys.path.insert(0, str(ROOT / "tests"))
    import media as m

    return m.build_all(tmp_path_factory.mktemp("media"))


@needs_ffmpeg
def test_probe_synthetic_media(media):
    start = time.monotonic()
    slides = probe(media["slides"], Ctx(), refresh=True)
    assert time.monotonic() - start < 10
    assert slides["duration"] == pytest.approx(24, abs=0.2)
    assert slides["has_audio"] and slides["video"]["height"] == 720
    assert [s["codec"] for s in slides["text_subtitles"]] == ["mov_text"]
    assert probe(media["slides_mkv"], Ctx(), refresh=True)["text_subtitles"][0]["codec"] == "subrip"
    no_audio = probe(media["no_audio"], Ctx(), refresh=True)
    assert not no_audio["has_audio"] and no_audio["speech_ratio"] == 0.0
    assert probe(media["silent"], Ctx(), refresh=True)["speech_ratio"] == 0.0
    nature = probe(media["nature"], Ctx(), refresh=True)
    assert nature["text_density"] < 0.3 and nature["visual_change"] > slides["visual_change"]
    unicode = probe(media["unicode"], Ctx(), refresh=True)
    assert unicode["name"] == "会议 录屏.mp4" and unicode["duration"] > 0
    assert probe(media["slides"], Ctx())["cached"] is True


@needs_ffmpeg
def test_make_plan_end_to_end_writes_valid_plan(media, tmp_path):
    jsonschema = pytest.importorskip("jsonschema")
    from vidroute.api import plan

    env = plan(str(media["slides"]), "claude-opus-5-5", "claude-code", task="minutes", out=str(tmp_path))
    assert env.ok, env.error
    path = Path(env.data["plan_path"])
    data = json.loads(path.read_text(encoding="utf-8"))
    schema = json.loads((ROOT / "schemas" / "plan.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(data, schema)
    assert data["route"] == "frames" and not data["uploads_raw_video"]
    assert path.parent.parent == tmp_path.resolve()


def test_missing_video_is_not_found():
    from vidroute.api import probe as api_probe

    env = api_probe("/definitely/not/here.mp4")
    assert env.exit_code == 1 and env.error["code"] == "not_found"
