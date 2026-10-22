"""Synthetic test media built with ffmpeg (no copyrighted material, CI-friendly).

Usable as a module from tests or as a script:  python tests/media.py OUT_DIR
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"


def _run(args: list[str]) -> None:
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def has_drawtext() -> bool:
    out = subprocess.run([FFMPEG, "-hide_banner", "-filters"], capture_output=True, text=True).stdout
    return " drawtext " in out


def font_option() -> str:
    """drawtext font argument that works on Windows (no fontconfig), macOS and Linux."""
    candidates = {
        "Windows": [r"C:/Windows/Fonts/arial.ttf", r"C:/Windows/Fonts/segoeui.ttf"],
        "Darwin": ["/System/Library/Fonts/Supplemental/Arial.ttf", "/Library/Fonts/Arial.ttf",
                   "/System/Library/Fonts/Helvetica.ttc"],
        "Linux": ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans.ttf",
                  "/usr/share/fonts/TTF/DejaVuSans.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"],
    }.get(platform.system(), [])
    for c in candidates:
        if os.path.exists(c):
            return "fontfile='" + c.replace("\\", "/").replace(":", "\\:") + "':"
    return ""  # rely on fontconfig


SLIDES = [
    ("Quarterly Review 2026", ["Revenue grew 18 percent", "Two new regions opened", "Hiring plan on track"]),
    ("Product Roadmap", ["Launch beta in March", "Mobile app in June", "Security audit in Q3"]),
    ("Risks and Mitigations", ["Supply delays", "Price pressure", "Team capacity"]),
    ("Action Items", ["Alice drafts the budget", "Bob books the venue", "Review again on Friday"]),
]


def make_slides(
    out: Path,
    slide_seconds: float = 6.0,
    slides: int = 4,
    size: str = "1280x720",
    audio: str = "tone",
    subtitles: bool = True,
    fps: int = 10,
) -> Path:
    """Text slides (drawtext), a gated tone ("speech-like" on/off), and optionally a subtitle track.

    audio: "tone" | "silence" | "none" | path to a WAV to loop under the slides.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    total = slide_seconds * slides
    filters = []
    font = font_option()
    if has_drawtext():
        for i in range(slides):
            title, bullets = SLIDES[i % len(SLIDES)]
            a, b = i * slide_seconds, (i + 1) * slide_seconds
            en = f"enable='between(t,{a},{b})'"
            filters.append(f"drawtext={font}text='{title}':fontsize=64:fontcolor=black:x=80:y=80:{en}")
            for j, line in enumerate(bullets):
                filters.append(f"drawtext={font}text='- {line}':fontsize=44:fontcolor=0x222222:x=110:y={220 + j * 90}:{en}")
            filters.append(f"drawtext={font}text='Slide {i + 1}':fontsize=28:fontcolor=gray:x=1100:y=660:{en}")
    vf = ",".join(filters) if filters else "null"
    args = ["-f", "lavfi", "-i", f"color=c=white:s={size}:d={total}:r={fps}"]
    if audio == "tone":
        expr = "if(lt(mod(t\\,3)\\,2)\\,0.3*sin(2*PI*440*t)\\,0)"
        args += ["-f", "lavfi", "-i", f"aevalsrc={expr}:s=16000:d={total}"]
    elif audio == "silence":
        args += ["-f", "lavfi", "-i", f"anullsrc=r=16000:cl=mono:d={total}"]
    elif audio != "none":
        args += ["-stream_loop", "-1", "-i", str(audio)]
    srt = None
    if subtitles:
        srt = out.with_suffix(".srt")
        lines = []
        for i in range(slides):
            a, b = i * slide_seconds + 0.5, (i + 1) * slide_seconds - 0.5
            lines.append(f"{i + 1}\n{_srt(a)} --> {_srt(b)}\nSubtitle for slide {i + 1}: {SLIDES[i % len(SLIDES)][0]}\n")
        srt.write_text("\n".join(lines), encoding="utf-8")
        args += ["-i", str(srt)]
    maps = ["-map", "0:v"]
    if audio != "none":
        maps += ["-map", "1:a"]
    if subtitles:
        maps += ["-map", f"{2 if audio != 'none' else 1}:s"]
    codec = ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-vf", vf]
    if audio != "none":
        codec += ["-c:a", "aac", "-b:a", "64k", "-ac", "1"]
    if subtitles:
        codec += ["-c:s", "mov_text" if out.suffix.lower() in (".mp4", ".mov", ".m4v") else "srt",
                  "-metadata:s:s:0", "language=eng"]
    _run([*args, *maps, *codec, "-t", str(total), str(out)])
    if srt is not None:
        srt.unlink(missing_ok=True)
    return out


def make_nature(out: Path, seconds: float = 12.0, source: str = "mandelbrot", audio: bool = True) -> Path:
    """Textless moving picture (mandelbrot zoom or testsrc2) with optional tone."""
    out.parent.mkdir(parents=True, exist_ok=True)
    src = {"mandelbrot": f"mandelbrot=s=640x360:r=10", "life": "life=s=640x360:r=10:mold=10:ratio=0.3",
           "testsrc2": "testsrc2=s=640x360:r=10"}[source]
    args = ["-f", "lavfi", "-i", src]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=330:sample_rate=16000:d={seconds}"]
    args += ["-t", str(seconds), "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    if audio:
        args += ["-c:a", "aac", "-b:a", "48k", "-shortest"]
    _run([*args, str(out)])
    return out


def make_speech_video(out: Path, wavs: list[Path], seconds_per_wav: float | None = None) -> Path:
    """Slides with real speech (e.g. the SenseVoice test WAVs) for real-model tests."""
    out.parent.mkdir(parents=True, exist_ok=True)
    concat = out.with_suffix(".speech.wav")
    inputs = []
    for w in wavs:
        inputs += ["-i", str(w)]
    n = len(wavs)
    graph = "".join(f"[{i}:a]apad=pad_dur=1.0[a{i}];" for i in range(n)) + "".join(f"[a{i}]" for i in range(n)) + \
        f"concat=n={n}:v=0:a=1[a]"
    _run([*inputs, "-filter_complex", graph, "-map", "[a]", "-ar", "16000", "-ac", "1", str(concat)])
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                                "default=nw=1:nk=1", str(concat)], capture_output=True, text=True).stdout.strip())
    slides = max(1, round(dur / 6.0))
    make_slides(out, slide_seconds=dur / slides, slides=slides, audio=str(concat), subtitles=False)
    concat.unlink(missing_ok=True)
    return out


def _srt(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_all(root: Path) -> dict[str, Path]:
    root.mkdir(parents=True, exist_ok=True)
    return {
        "slides": make_slides(root / "slides.mp4"),
        "slides_mkv": make_slides(root / "slides.mkv"),
        "silent": make_slides(root / "silent.mp4", audio="silence", subtitles=False),
        "no_audio": make_slides(root / "no_audio.mp4", audio="none", subtitles=False),
        "nature": make_nature(root / "nature.mp4"),
        "unicode": make_slides(root / "中文 路径 with spaces" / "会议 录屏.mp4", slides=2, subtitles=False),
    }


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "tests/.media")
    for name, p in build_all(target).items():
        print(f"{name}: {p} ({p.stat().st_size} bytes)")
