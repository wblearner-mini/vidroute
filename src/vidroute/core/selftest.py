"""End-to-end self-test: a generated six-second clip through ``prepare`` (frames route), checked file by file.

The clip is a white slide with "VIDROUTE SELF TEST" (when ffmpeg has
``drawtext``) over the Chinese sample recording that ships with SenseVoice
(when installed; a tone otherwise). It exercises ffmpeg, keyframes, contact
sheets, VAD/ASR and OCR exactly as a real video would, and leaves nothing
outside ``$VIDROUTE_HOME/cache/selftest``.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any

from vidroute.core import paths
from vidroute.core.context import Ctx
from vidroute.core.errors import VidrouteError
from vidroute.core.util import ffmpeg, proc
from vidroute.core.util.jsonio import read_json

TEXT = "VIDROUTE SELF TEST"
SECONDS = 6.0


def make_clip(out: Path, wav: Path | None, font: str | None) -> Path:
    ff = ffmpeg.require()
    vf = "format=yuv420p"
    if font is not None:
        vf = (f"drawtext={font}text='{TEXT}':fontsize=72:fontcolor=black:x=(w-text_w)/2:y=(h-text_h)/2,"
              + vf)
    args: list[str] = [str(ff.ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
                       "-f", "lavfi", "-i", f"color=c=white:s=1280x720:r=10:d={SECONDS}"]
    if wav is not None:
        args += ["-i", str(wav)]
    else:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=16000:duration={SECONDS}"]
    args += ["-vf", vf, "-c:v", "mpeg4", "-q:v", "4", "-c:a", "aac", "-b:a", "96k", "-t", str(SECONDS),
             "-shortest", str(out)]
    res = proc.run(args, timeout=120)
    if not res.ok or not out.is_file():
        raise RuntimeError(f"could not generate the test clip: {(res.stderr or '').strip()[-300:]}")
    return out


def _check(name: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"check": name, "ok": ok, "detail": detail}


def run(ctx: Ctx) -> dict[str, Any]:
    from vidroute.core import doctor
    from vidroute.core.install.manifest import load_manifest
    from vidroute.core.prepare import media
    from vidroute.core.prepare import run as prepare_run

    started = time.monotonic()
    if not ffmpeg.detect().found:
        return {"status": "skipped", "reason": "ffmpeg/ffprobe are not installed", "checks": []}
    ctx.progress("selftest", None, None, "generating a test clip")
    caps = doctor.run(refresh=True, offline=True)["capabilities"]
    work = paths.cache_dir() / "selftest"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    wav = load_manifest().model("asr-sensevoice-small-int8").model_dir / "test_wavs" / "zh.wav"
    speech = caps["asr"]["ready"] and wav.is_file()
    font = media.drawtext_font()
    checks: list[dict[str, Any]] = []
    try:
        clip = make_clip(work / "self-test.mp4", wav if speech else None, font)
        ctx.progress("selftest", None, None, "running prepare on the test clip")
        ocr = "on" if caps["ocr"]["ready"] and font is not None else "off"
        sub = Ctx(cancel_event=ctx.cancel_event)
        data = prepare_run(str(clip), llm="vidroute-selftest", host="unknown", task="summary", route="frames",
                           out=str(work / "out"), ocr=ocr, ctx=sub)
    except (VidrouteError, RuntimeError) as exc:
        msg = getattr(exc, "message", None) or str(exc)
        return {"status": "failed", "checks": [_check("prepare", False, msg)],
                "seconds": round(time.monotonic() - started, 1)}
    root = Path(data["out_dir"])
    checks.append(_check("outputs", all((root / f).is_file() for f in ("SUMMARY.md", "ANALYSIS_GUIDE.md", "handoff.json")),
                         "SUMMARY.md, ANALYSIS_GUIDE.md and handoff.json written"))
    sheets = sorted((root / "contact-sheets").glob("*.jpg"))
    checks.append(_check("keyframes", bool(sheets), f"{len(sheets)} contact sheet(s)"))
    if speech:
        segments = (read_json(root / "transcript.json", default={}) or {}).get("segments") or []
        heard = any(str(s.get("text") or "").strip() for s in segments)
        checks.append(_check("speech", heard, f"{len(segments)} transcript segment(s) from the sample recording" if heard
                             else "no speech recognized in the sample recording"))
    if ocr == "on":
        text = (root / "ocr.md").read_text(encoding="utf-8") if (root / "ocr.md").is_file() else ""
        seen = "SELF TEST" in text.upper()
        checks.append(_check("ocr", seen, "on-screen text read back" if seen else "the slide text was not recognized"))
    return {
        "status": "passed" if all(c["ok"] for c in checks) else "failed",
        "checks": checks,
        "skipped": [c for c, on in (("speech", speech), ("ocr", ocr == "on")) if not on],
        "out_dir": str(root),
        "seconds": round(time.monotonic() - started, 1),
    }
