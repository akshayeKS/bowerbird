"""ffmpeg: clean clips, apply declared gaps, mix audio, render video.

Two things here are load-bearing and easy to get wrong:

1. `-ar 48000` on the audio encode. loudnorm runs at 192kHz internally and
   leaves its output upsampled; without this you ship a 96kHz AAC, which
   Slack previews, Drive and Keynote/PowerPoint embeds resample or reject.

2. Loudness is applied ONCE, globally, after concatenation - never per clip.
   Per-clip normalisation makes every slide equally loud, which flattens the
   delivery and is audible as a pumping, robotic quality.
"""
from __future__ import annotations

import os
import shutil
import subprocess

TOOLS = ("ffmpeg", "ffprobe")


class RenderError(RuntimeError):
    pass


def missing_tools() -> list[str]:
    return [t for t in TOOLS if not shutil.which(t)]


def require_tools() -> None:
    miss = missing_tools()
    if miss:
        raise RenderError(
            "%s not found on PATH.\n"
            "  macOS:   brew install ffmpeg\n"
            "  Linux:   sudo apt install ffmpeg\n"
            "  Windows: winget install ffmpeg\n\n"
            "Just installed it with Homebrew? An already-open terminal won't see it yet.\n"
            'Run:  eval "$(/opt/homebrew/bin/brew shellenv)"' % " and ".join(miss))


def run(cmd: list[str], what: str = "") -> str:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RenderError("%s failed\n  %s\n%s"
                          % (what or cmd[0], " ".join(cmd[:8]), (r.stderr or "")[-900:]))
    return r.stdout


def duration(path: str) -> float:
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
               "-of", "csv=p=0", path], "ffprobe").strip()
    try:
        return float(out)
    except ValueError:
        raise RenderError("ffprobe read no duration from %s (empty clip?)" % path) from None


def to_wav(src: str, dst: str) -> None:
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", src,
         "-ac", "1", "-ar", "48000", dst], "ffmpeg")


# Trim only the dead air at the head and tail. Internal rhythm is left alone -
# the pause between sentences is the speaker breathing, not a defect.
_TRIM = ("highpass=f=70,"
         "silenceremove=start_periods=1:start_duration=0.05:start_threshold=-45dB:start_silence=0.08,"
         "areverse,"
         "silenceremove=start_periods=1:start_duration=0.05:start_threshold=-45dB:start_silence=0.10,"
         "areverse")


def prepare_clip(src: str, dst: str, gap: float) -> float:
    af = _TRIM + (",apad=pad_dur=%.2f" % gap if gap > 0 else "")
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-ac", "1", "-ar", "48000",
         "-af", af, dst], "ffmpeg")
    return duration(dst)


def mix_audio(parts: list[str], dest: str, total: float, cfg) -> float:
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    for p in parts:
        cmd += ["-i", p]
    n = len(parts)
    cmd += [
        "-filter_complex",
        "".join("[%d:a]" % i for i in range(n)) +
        "concat=n=%d:v=0:a=1,"
        "acompressor=threshold=-20dB:ratio=2:attack=10:release=200,"
        "loudnorm=I=%g:TP=%g:LRA=%g,"
        "afade=t=in:st=0:d=0.4,afade=t=out:st=%.3f:d=1.2[a]"
        % (n, cfg.loudness_i, cfg.loudness_tp, cfg.loudness_lra, max(0.0, total - 1.2)),
        "-map", "[a]", "-ar", "48000", "-c:a", "aac", "-b:a", cfg.audio_bitrate, dest]
    run(cmd, "ffmpeg (audio mix)")
    return duration(dest)


def render_video(slides, durs: list[float], slides_dir: str, build_dir: str,
                 audio: str, out: str, cfg, log=print) -> None:
    vf0 = ("scale=%d:%d:force_original_aspect_ratio=decrease,"
           "pad=%d:%d:(ow-iw)/2:(oh-ih)/2,format=yuv420p,setsar=1"
           % (cfg.width, cfg.height, cfg.width, cfg.height))
    fps = cfg.fps
    cum = prev = 0
    lines = []
    for i, (s, d) in enumerate(zip(slides, durs)):
        cum += d
        f = round(cum * fps)
        frames = max(1, f - prev)
        prev = f
        vf = vf0
        if i == 0:
            vf += ",fade=t=in:st=0:d=0.5"
        if i == len(slides) - 1:
            vf += ",fade=t=out:st=%.3f:d=1.2" % max(0.0, frames / fps - 1.2)
        png = os.path.join(slides_dir, "s%02d.png" % s.slide)
        seg = os.path.join(build_dir, "seg%02d.mp4" % s.slide)
        log("  rendering slide %d/%d" % (i + 1, len(slides)))
        run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-framerate", str(fps),
             "-i", png, "-frames:v", str(frames), "-vf", vf,
             "-c:v", "libx264", "-preset", "veryfast", "-tune", "stillimage",
             "-crf", "20", "-g", str(fps * 2), "-pix_fmt", "yuv420p", seg], "ffmpeg")
        lines.append("file '%s'" % os.path.basename(seg))

    listing = os.path.join(build_dir, "list.txt")
    open(listing, "w").write("\n".join(lines) + "\n")
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
         "-i", listing, "-i", audio, "-map", "0:v", "-map", "1:a",
         "-c", "copy", "-movflags", "+faststart", "-shortest", out], "ffmpeg (mux)")


def build(slides, clips_dir: str, slides_dir: str, build_dir: str, out: str,
          cfg, log=print) -> dict:
    """Clips -> finished video. Returns stats including the measured speaking rate."""
    require_tools()
    os.makedirs(build_dir, exist_ok=True)

    log("\nApplying declared gaps…")
    durs, parts, speech = [], [], 0.0
    for s in slides:
        gap = s.gap(cfg.default_gap)
        src = os.path.join(clips_dir, "slide-%02d.wav" % s.slide)
        dst = os.path.join(build_dir, "c%02d.wav" % s.slide)
        d = prepare_clip(src, dst, gap)
        durs.append(d)
        parts.append(dst)
        speech += d - gap
        log("  slide %2d  %5.2fs  (gap %.2f)" % (s.slide, d, gap))

    total = sum(durs)
    log("\nNarration length: %d:%05.2f" % (int(total) // 60, total % 60))

    audio = os.path.join(build_dir, "audio.m4a")
    actual = mix_audio(parts, audio, total, cfg)
    # Silent drift desyncs the whole video; fail loudly instead.
    if abs(actual - total) > 0.25:
        raise RenderError(
            "Timing drift: slide durations sum to %.2fs but the audio is %.2fs.\n"
            "Slides would fall out of sync with the narration." % (total, actual))
    log("Timing check: slides %.2fs vs audio %.2fs — locked." % (total, actual))

    render_video(slides, durs, slides_dir, build_dir, audio, out, cfg, log=log)

    chars = sum(s.chars for s in slides)
    return {
        "total": total,
        "speech": speech,
        "chars": chars,
        "chars_per_sec": (chars / speech) if speech > 0 else 0.0,
        "durations": durs,
    }
