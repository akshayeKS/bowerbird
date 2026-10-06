"""bowerbird command line."""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys

from . import config as cfgmod
from . import draft as draftmod
from . import rasterize as rast
from . import render
from . import script as scriptmod
from . import tts

SLIDES_DIR = "slides"
CLIPS_DIR = "clips"
BUILD_DIR = "build"
SAMPLES_DIR = "samples"
PROFILE_FILE = ".bowerbird_profile"
AUDIO_EXTS = (".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aiff", ".aif")


def die(msg: str, code: int = 1):
    print("\nerror: " + msg, file=sys.stderr)
    raise SystemExit(code)


def parse_duration(text: str) -> float:
    """'3:30' or '210' or '3m30s' -> seconds."""
    text = str(text).strip().lower()
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return float(text)
    m = re.fullmatch(r"(\d+):([0-5]?\d(\.\d+)?)", text)
    if m:
        return int(m.group(1)) * 60 + float(m.group(2))
    m = re.fullmatch(r"(?:(\d+)m)?\s*(?:(\d+(?:\.\d+)?)s)?", text)
    if m and (m.group(1) or m.group(2)):
        return int(m.group(1) or 0) * 60 + float(m.group(2) or 0)
    raise ValueError("Can't read a duration from %r. Try 3:30 or 210." % text)


def fmt(seconds: float) -> str:
    return "%d:%05.2f" % (int(seconds) // 60, seconds % 60)


def sample_files(d: str) -> list[str]:
    files = [p for p in glob.glob(os.path.join(d, "*"))
             if os.path.splitext(p)[1].lower() in AUDIO_EXTS]
    return sorted(files, key=lambda p: rast.natural_key(os.path.basename(p)))


def slide_pngs(d: str = SLIDES_DIR) -> list[str]:
    return sorted(glob.glob(os.path.join(d, "s[0-9]*.png")),
                  key=lambda p: rast.natural_key(os.path.basename(p)))


# ---------------------------------------------------------------- init
def cmd_init(a) -> int:
    if not os.path.exists(a.deck):
        die("No such deck: %s" % a.deck)
    try:
        target = parse_duration(a.target)
    except ValueError as e:
        die(str(e))

    cfg = cfgmod.apply_overrides(cfgmod.load("."), rasterizer=a.rasterizer)
    cfg.target_seconds = target

    print("Rasterizing %s (backend: %s)…" % (a.deck, rast.pick_backend(a.deck, cfg.rasterizer)))
    try:
        n = rast.rasterize(a.deck, SLIDES_DIR, cfg)
    except (rast.RasterError, Exception) as e:
        die(str(e))
    print("  %d slides -> %s/" % (n, SLIDES_DIR))

    texts = rast.extract_text(a.deck)
    if texts:
        print("  pulled text from %d slides" % len(texts))
    else:
        print("  no slide text available - you'll write the script from the images")

    cfgmod.write_default(".", target)

    path = os.path.join(".", scriptmod.SCRIPT_NAME)
    if os.path.exists(path):
        print("\n%s already exists - leaving it alone." % scriptmod.SCRIPT_NAME)
    else:
        slides = scriptmod.scaffold({i: texts.get(i, "") for i in range(1, n + 1)},
                                    cfg.default_gap)
        scriptmod.save(slides, ".")
        flagged = [s.slide for s in slides if s.status]
        print("\nWrote %s (%d slides, all text empty)" % (scriptmod.SCRIPT_NAME, n))
        if flagged:
            print("  slides marked not-yet-shipped: %s" % ", ".join(map(str, flagged)))

    mins, secs = divmod(int(target), 60)
    print("\nBudget for %d:%02d is about %d characters of narration total."
          % (mins, secs, cfg.target_chars))
    print("\nNext:")
    print("  1. bowerbird draft            # writes a prompt for an LLM to fill in")
    print("  2. put 3-6 voice recordings in ./%s" % SAMPLES_DIR)
    print("  3. bowerbird voice")
    print("  4. bowerbird check && bowerbird build")
    return 0


# ---------------------------------------------------------------- draft
def cmd_draft(a) -> int:
    cfg = cfgmod.load(".")
    if not os.path.exists(scriptmod.SCRIPT_NAME):
        die("No %s yet. Run: bowerbird init <deck>" % scriptmod.SCRIPT_NAME)
    slides = scriptmod.load(".")

    texts, statuses = {}, {}
    for s in slides:
        texts[s.slide] = s.title or ""
        if s.status:
            statuses[s.slide] = s.status
    if a.deck:
        got = rast.extract_text(a.deck)
        for k, v in got.items():
            texts[k] = v

    prompt = draftmod.build_prompt(texts, statuses, cfg)

    if not a.llm:
        path = draftmod.write_prompt(prompt, ".")
        print("Wrote %s" % path)
        print("\nPaste it into Claude (or any model), then save the JSON array it")
        print("returns as %s. Or re-run with --llm to do it automatically." % scriptmod.SCRIPT_NAME)
        return 0

    print("Asking %s to draft the narration…" % a.model)
    try:
        reply = draftmod.call_anthropic(prompt, model=a.model)
        data = draftmod.parse_reply(reply)
    except Exception as e:
        die(str(e))

    by_num = {s.slide: s for s in slides}
    for row in data:
        n = int(row.get("slide", 0))
        if n in by_num:
            by_num[n].text = str(row.get("text", "")).strip()
            if row.get("gap_after") is not None:
                by_num[n].gap_after = float(row["gap_after"])
    scriptmod.save(slides, ".")

    b = scriptmod.budget(slides, cfg.chars_per_sec, cfg.default_gap)
    print("Wrote %s - %d chars, estimated %s (target %s)"
          % (scriptmod.SCRIPT_NAME, b["chars"], fmt(b["total"]), fmt(cfg.target_seconds)))
    print("\nRead it before building. Check the length and the not-yet-shipped slides.")
    return 0


# ---------------------------------------------------------------- voice
def cmd_voice(a) -> int:
    cfg = cfgmod.apply_overrides(cfgmod.load("."), url=a.url, backend=a.backend)
    src = a.samples or SAMPLES_DIR
    files = sample_files(src)
    if not files:
        die("No audio in %s/.\n"
            "Record 3-6 clips of yourself (20-30s each, quiet room, normal pace).\n"
            "Any of: %s" % (src, ", ".join(AUDIO_EXTS)))
    if len(files) < 3:
        print("note: %d sample(s) found - 3 to 6 clones noticeably better." % len(files))

    backend = tts.get(cfg.backend, cfg)
    ok, detail = backend.health()
    if not ok:
        die("Voice backend not ready: %s" % detail)
    print("Backend %s ready (%s)" % (cfg.backend, detail))

    cache_path = os.path.join(src, "samples.json")
    cache: dict[str, str] = {}
    if os.path.exists(cache_path):
        try:
            for row in json.load(open(cache_path)):
                if row.get("file") and row.get("text"):
                    cache[os.path.basename(row["file"])] = row["text"]
        except Exception:
            pass

    samples, new = [], 0
    for f in files:
        base = os.path.basename(f)
        text = cache.get(base, "")
        if not text:
            print("  transcribing %s…" % base)
            text = backend.transcribe(f)
            if not text:
                die("Transcription of %s came back empty. Is the clip silent?\n"
                    "You can write transcripts by hand in %s instead." % (base, cache_path))
            new += 1
        samples.append({"file": f, "text": text})
    if new:
        json.dump([{"file": os.path.relpath(s["file"]), "text": s["text"]} for s in samples],
                  open(cache_path, "w"), indent=1)
        print("  cached %d transcript(s) in %s" % (new, cache_path))

    pid = backend.ensure_profile(cfg.profile_name, samples, a.profile_id)
    open(PROFILE_FILE, "w").write(pid)
    print("\nVoice profile ready: %s" % pid)
    return 0


# ---------------------------------------------------------------- check
def cmd_check(a) -> int:
    cfg = cfgmod.apply_overrides(cfgmod.load("."), url=a.url)
    ok = True

    def line(good, label, detail=""):
        nonlocal ok
        ok = ok and good
        print("  %-5s %-20s %s" % ("ok" if good else "MISS", label, detail))

    print("\nTools")
    miss = render.missing_tools()
    line(not miss, "ffmpeg/ffprobe",
         "found" if not miss else "missing %s - brew install ffmpeg" % ", ".join(miss))

    print("\nVoice backend (%s)" % cfg.backend)
    try:
        backend = tts.get(cfg.backend, cfg)
        ready, detail = backend.health()
        line(ready, "ready", detail)
    except Exception as e:
        line(False, "ready", str(e).splitlines()[0])
    line(os.path.exists(PROFILE_FILE), "voice profile",
         open(PROFILE_FILE).read().strip() if os.path.exists(PROFILE_FILE)
         else "run: bowerbird voice")

    print("\nProject")
    pngs = slide_pngs()
    line(bool(pngs), "slides/", "%d images" % len(pngs) if pngs
         else "run: bowerbird init <deck>")

    try:
        slides = scriptmod.load(".")
    except scriptmod.ScriptError as e:
        line(False, "script.json", str(e).splitlines()[0])
        print("\nFix the MISS lines above.\n")
        return 1

    live = scriptmod.spoken(slides)
    line(bool(live), "script.json", "%d of %d slides have narration" % (len(live), len(slides)))

    missing_png = [s.slide for s in live
                   if not os.path.exists(os.path.join(SLIDES_DIR, "s%02d.png" % s.slide))]
    line(not missing_png, "slide/script match",
         "aligned" if not missing_png else "no image for slide(s) %s" % missing_png)

    if live:
        b = scriptmod.budget(slides, cfg.chars_per_sec, cfg.default_gap)
        delta = b["total"] - cfg.target_seconds
        close = abs(delta) <= max(10.0, cfg.target_seconds * 0.07)
        line(close, "length",
             "%s vs target %s (%+.0fs) @ %.1f chars/s"
             % (fmt(b["total"]), fmt(cfg.target_seconds), delta, cfg.chars_per_sec))

        over = [s.slide for s in live if scriptmod.overclaims(s)]
        if over:
            print("  %-5s %-20s slides %s are marked not-yet-shipped but the narration"
                  % ("warn", "honesty", ", ".join(map(str, over))))
            print("  %-5s %-20s never says so - it will sound like they already ship."
                  % ("", ""))

    print("\n%s\n" % ("Ready - run: bowerbird build" if ok
                      else "Fix the MISS lines above, then re-run check."))
    return 0 if ok else 1


# ---------------------------------------------------------------- build
def cmd_build(a) -> int:
    cfg = cfgmod.apply_overrides(cfgmod.load("."), url=a.url, engine=a.engine, out=a.out)
    try:
        slides = scriptmod.load(".")
    except scriptmod.ScriptError as e:
        die(str(e))
    live = scriptmod.spoken(slides)
    if not live:
        die("No narration in %s yet. Run: bowerbird draft" % scriptmod.SCRIPT_NAME)

    for s in live:
        png = os.path.join(SLIDES_DIR, "s%02d.png" % s.slide)
        if not os.path.exists(png):
            die("Missing %s - run: bowerbird init <deck>" % png)

    os.makedirs(CLIPS_DIR, exist_ok=True)
    try:
        render.require_tools()
    except render.RenderError as e:
        die(str(e))

    if not a.video_only:
        backend = tts.get(cfg.backend, cfg)
        ready, detail = backend.health()
        if not ready:
            die("Voice backend not ready: %s" % detail)

        pid = a.profile_id
        if not pid and os.path.exists(PROFILE_FILE):
            pid = open(PROFILE_FILE).read().strip()
        if not pid:
            die("No voice profile. Run: bowerbird voice")
        print("Using voice profile %s" % pid)

        want = None
        if a.slides:
            try:
                want = {int(x) for x in a.slides.replace(" ", "").split(",") if x}
            except ValueError:
                die("--slides wants numbers, e.g. --slides 3,7")
            unknown = want - {s.slide for s in live}
            if unknown:
                die("No narration for slide(s) %s" % sorted(unknown))

        if want:
            todo = [s for s in live if s.slide in want]
        else:
            todo = [s for s in live
                    if not os.path.exists(os.path.join(CLIPS_DIR, "slide-%02d.wav" % s.slide))]
        if not todo:
            print("All clips already exist. Use --slides N to redo one.")

        for i, s in enumerate(todo, 1):
            print("Generating slide %d (%d of %d): %s…"
                  % (s.slide, i, len(todo), s.text[:58]))
            try:
                backend.generate(
                    pid, s.text,
                    os.path.join(CLIPS_DIR, "slide-%02d.wav" % s.slide),
                    engine=s.extra.get("engine", cfg.engine),
                    seed=s.extra.get("seed", cfg.seed),
                    instruct=None if a.no_instruct else s.extra.get("instruct", cfg.instruct),
                    to_wav=render.to_wav)
            except tts.BackendError as e:
                die(str(e))

    missing = [s.slide for s in live
               if not os.path.exists(os.path.join(CLIPS_DIR, "slide-%02d.wav" % s.slide))]
    if missing:
        die("No clips for slide(s) %s. Run without --video-only." % missing)

    try:
        stats = render.build(live, CLIPS_DIR, SLIDES_DIR, BUILD_DIR, cfg.out, cfg)
    except render.RenderError as e:
        die(str(e))

    if stats["chars_per_sec"] > 0:
        cfgmod.update_chars_per_sec(".", stats["chars_per_sec"])
    print("\nDone: %s (%s)" % (os.path.abspath(cfg.out), fmt(stats["total"])))
    print("Measured %.1f chars/s - saved for the next length estimate."
          % stats["chars_per_sec"])
    return 0


# ---------------------------------------------------------------- entry
def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="bowerbird",
        description="Turn a slide deck into a narrated video, in your own voice, "
                    "entirely on your machine.")
    sub = p.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("init", help="rasterize a deck and scaffold the script")
    i.add_argument("deck", help="folder of images, .html, .pdf, .pptx/.ppt/.odp")
    i.add_argument("--target", default="3:30", help="target length (default 3:30)")
    i.add_argument("--rasterizer", default=None,
                   choices=["auto", "chrome", "pdf", "office", "images"])
    i.set_defaults(fn=cmd_init)

    d = sub.add_parser("draft", help="write a prompt for an LLM to fill in the narration")
    d.add_argument("--deck", default=None, help="re-read slide text from the deck")
    d.add_argument("--llm", action="store_true", help="call the Anthropic API directly")
    d.add_argument("--model", default=draftmod.DEFAULT_MODEL)
    d.set_defaults(fn=cmd_draft)

    v = sub.add_parser("voice", help="clone your voice from sample recordings")
    v.add_argument("samples", nargs="?", default=None, help="folder (default ./samples)")
    v.add_argument("--url", default=None)
    v.add_argument("--backend", default=None)
    v.add_argument("--profile-id", default=None, help="reuse an existing profile")
    v.set_defaults(fn=cmd_voice)

    c = sub.add_parser("check", help="preflight everything before building")
    c.add_argument("--url", default=None)
    c.set_defaults(fn=cmd_check)

    b = sub.add_parser("build", help="generate clips and render the video")
    b.add_argument("--slides", default=None, help="regenerate only these, e.g. 3,7")
    b.add_argument("--video-only", action="store_true", help="re-render, skip generation")
    b.add_argument("--no-instruct", action="store_true")
    b.add_argument("--engine", default=None)
    b.add_argument("--profile-id", default=None)
    b.add_argument("--url", default=None)
    b.add_argument("--out", default=None)
    b.set_defaults(fn=cmd_build)

    a = p.parse_args(argv)
    try:
        return a.fn(a)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
