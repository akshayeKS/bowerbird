# bowerbird

Turn a slide deck into a narrated video, in your own voice, entirely on your machine.

The bowerbird builds an elaborate display to present with, and mimics the sounds it
hears. That's the whole tool: your deck, your voice.

```
deck.pdf / .pptx / .html ──┐
                           ├── bowerbird init ──> slides/*.png + script.json
 target length (3:30) ─────┘
                                      │
                         ✎ you write the narration     ← the part that matters
                                      │
 samples/*.wav ── bowerbird voice ──> cloned profile
                                      │
                                      ▼
                            bowerbird build ──> deck.mp4
```

Nothing is uploaded. Voice cloning runs against a local
[Voicebox](https://github.com/voiceboxdev/voicebox) install on `localhost`, and the
only network call bowerbird ever makes is the optional `--llm` flag you have to ask
for by name.

---

## Why this rather than a TTS slideshow script

Three things that turn out to matter more than the text-to-speech itself:

**The script is the product.** Everything else is plumbing around one editable file.
`bowerbird` spends its effort on making that file easy to get right — budgeting its
length, flagging where it overclaims, and making a single slide cheap to redo.

**Pacing is declared, not detected.** Each slide carries a `gap_after` in seconds. An
earlier version of this tool inferred slide boundaries from the audio with
`silencedetect`; it was fragile, needed tuning, and once starved a four-word sentence
down to 0.35 seconds of screen time. A number in a file is better.

**Narration drafts overshoot, invisibly.** A first draft written to a 3:30 target came
in at 6:09 — 44% over — and that was not apparent from reading it. `bowerbird` counts
characters against a measured speaking rate and tells you before you render, then
recalibrates that rate from your actual output so the next estimate is sharper.

---

## Install

```sh
pip install bowerbird
```

Or from source:

```sh
git clone https://github.com/akshayeKS/bowerbird
cd bowerbird && pip install -e .
```

The package itself has **no Python dependencies** — standard library only. It does need
some external tools, and `bowerbird check` will tell you exactly which are missing.

| | needed for | install |
|---|---|---|
| **ffmpeg** | always | `brew install ffmpeg` |
| **Voicebox** | voice cloning | see below |
| Chrome | HTML decks | probably already there |
| poppler | PDF decks | `brew install poppler` |
| LibreOffice | `.pptx`/`.odp` decks | `brew install --cask libreoffice` |

> **Using a work laptop?** Check with your IT or security team before installing
> Voicebox. It's a third-party app that downloads a multi-gigabyte local AI model and
> processes recordings of your voice. Everything stays on your machine and nothing is
> sent to a server — which usually makes this an easy conversation — but installing it
> is still likely to need approval, and voice data may fall under your organisation's
> biometric or acceptable-use policy. Also remember that if your deck is confidential,
> your narration script is too.

Keynote isn't supported directly — use **File ▸ Export To ▸ PDF** and pass the PDF.
LibreOffice's `.key` import is unreliable enough that failing loudly is kinder.

---

## Walkthrough

There's a small fictional deck in `examples/demo-deck/` to try this on.

### 1. Rasterize the deck and scaffold the script

```sh
bowerbird init examples/demo-deck/deck.html --target 3:30
```

```
Rasterizing examples/demo-deck/deck.html (backend: chrome)…
  5 slides -> slides/
  pulled text from 5 slides

Wrote script.json (5 slides, all text empty)
  slides marked not-yet-shipped: 5

Budget for 3:30 is about 3570 characters of narration total.
```

You now have `slides/s01.png…`, a `script.json` skeleton, and a `bowerbird.toml`.

### 2. Draft the narration

```sh
bowerbird draft
```

This writes `DRAFT_PROMPT.md` — your slide text plus the writing rules and the
character budget. Paste it into Claude or any other model and save the JSON array it
returns as `script.json`.

No API key, no provider lock-in, works offline. If you'd rather it did the call:

```sh
export ANTHROPIC_API_KEY=sk-...
bowerbird draft --llm
```

Either way, **read the result before building**. The draft is a starting point.

### 3. Clone your voice

Put 3–6 recordings in `./samples` — 20–30 seconds each, quiet room, normal speaking
pace — then:

```sh
bowerbird voice
```

Voicebox transcribes each sample itself, so you don't type transcripts by hand. The
transcripts are cached in `samples/samples.json`, so this is a one-time cost.

### 4. Check, then build

```sh
bowerbird check
```

```
Tools
  ok    ffmpeg/ffprobe       found

Voice backend (voicebox)
  ok    ready                1.7B on MPS (Apple Silicon)
  ok    voice profile        3f2a91c4-…

Project
  ok    slides/              5 images
  ok    script.json          5 of 5 slides have narration
  ok    slide/script match   aligned
  ok    length               3:28.40 vs target 3:30 (-2s) @ 17.4 chars/s
```

```sh
bowerbird build
```

Budget roughly **20–30 seconds per slide** on Apple Silicon for generation, plus a
minute or so to render.

---

## Writing a script that doesn't sound like a robot reading bullet points

These are the rules baked into `DRAFT_PROMPT.md`, each learned by getting it wrong:

1. **Don't read the slide.** The viewer can read. Narration says what the slide
   doesn't — why it matters, where to look.
2. **Write one continuous argument.** Each slide should open by picking up where the
   last left off: *"And here's the scale…"*, *"But…"*, *"So what do you actually do?"*
   Without this you get N announcements instead of a pitch. This matters more than any
   audio processing.
3. **Spell numbers out.** "eight hundred sixty", not "860". Don't read URLs or ticket
   numbers aloud — the slide already shows them.
4. **Never write "um" or "uh".** TTS pronounces them as words. It sounds worse than no
   filler at all. Use connective phrases instead.
5. **Point at things on demo slides.** *"And here it is, live."*, *"as you can see…"*
6. **Say when something isn't shipped yet.** If a slide is badged Planned / Roadmap /
   Beta, the narration must say so.

That last one is enforced. `init` detects status words on your slides, and `check`
warns when such a slide's narration never hedges:

```
  warn  honesty              slides 5 are marked not-yet-shipped but the narration
                             never says so - it will sound like they already ship.
```

Quietly presenting unshipped work as shipped is worse than sounding stilted, and it's
the easiest mistake to make when writing to a time budget.

---

## Configuration

`bowerbird.toml`, all keys optional. CLI flags beat the file; per-slide keys beat both.

```toml
target_seconds = 210        # 3:30
out = "deck.mp4"

backend = "voicebox"
url = "http://localhost:17493"
# engine = "qwen"
# seed = 20251006           # fixed seed keeps the voice consistent across slides

rasterizer = "auto"         # auto | chrome | pdf | office | images
width = 1920
height = 1080
fps = 30

default_gap = 0.45          # pause after a slide, in seconds
```

`chars_per_sec` is written here automatically after each build — that's the calibration
that keeps length estimates honest for your voice and engine.

### Per-slide

```json
[
 {"slide": 1, "text": "Right now this gets checked by eye.", "gap_after": 0.5},
 {"slide": 2, "text": "And here's the scale.", "gap_after": 0.3},
 {"slide": 3, "text": "", "comment": "silent title card"},
 {"slide": 4, "text": "One stubborn slide.", "engine": "luxtts", "seed": 42}
]
```

- `gap_after` — ~0.30 mid-thought (a demo sequence that should feel continuous),
  0.50–0.65 at real transitions.
- Empty `text` skips the slide entirely.
- `engine`, `seed`, `instruct` override the project defaults for that slide only.

---

## Iterating

```sh
bowerbird build --slides 7,12    # one clip sounds off - redo just those
bowerbird build --video-only     # re-render after editing gap_after (~1 min, no TTS)
bowerbird build --no-instruct    # drop the delivery hint if it's not helping
```

Clips are cached in `clips/`, so a re-run only does what's missing. Per-slide clips are
a deliberate design choice: you can re-roll one line without disturbing its neighbours.

---

## How it works, and the parts that are load-bearing

- **`-ar 48000` on the audio encode.** `loudnorm` processes at 192 kHz and leaves its
  output upsampled; without pinning this you ship a 96 kHz AAC that Slack previews,
  Drive and Keynote/PowerPoint embeds resample or reject.
- **Loudness is applied once, globally, after concatenation.** Per-clip normalisation
  makes every slide equally loud, which flattens delivery audibly.
- **Only head and tail silence is trimmed.** The pauses between sentences are the
  speaker breathing, not defects.
- **Slide durations are asserted against the final audio** to within 0.25 s. Silent
  drift desyncs the whole video, so it fails loudly instead.
- **HTML decks render at their native CSS size**, then Chrome scales to your target
  resolution — so embedded fonts and layout come out exactly as designed rather than
  upscaled and soft.

---

## Troubleshooting

**`ffmpeg: command not found` right after installing it.** An already-open terminal
won't see a fresh Homebrew install. Run `eval "$(/opt/homebrew/bin/brew shellenv)"` or
start a new shell. The install almost certainly worked.

**`no voice model loaded`.** Voicebox running is not the same as Voicebox ready. Open
the app, install a model (Qwen3-TTS is a good default; LuxTTS is faster without a GPU)
and let it finish loading. `check` tests for this specifically.

**Homebrew isn't installed.** Its installer needs a sudo password and can't be
automated — run it yourself in a terminal, then `brew install ffmpeg`.

**A number is read wrong.** Spell it out in `script.json` and re-run
`bowerbird build --slides N`.

---

## Adding a backend

The voice interface is four methods — `health`, `transcribe`, `ensure_profile`,
`generate`:

```python
from bowerbird.tts import backend

@backend("mybackend")
class MyBackend:
    def __init__(self, cfg): ...
    def health(self) -> tuple[bool, str]: ...
    def transcribe(self, path: str) -> str: ...
    def ensure_profile(self, name, samples, profile_id=None) -> str: ...
    def generate(self, profile_id, text, dest_wav, **opts) -> None: ...
```

Then set `backend = "mybackend"` in `bowerbird.toml`. Voicebox is the only one shipped
because it's the only one that's actually been run end to end — adding an untested
backend to the README would be a claim, not a feature.

Rasterizer backends live in `bowerbird/rasterize.py` and follow the same shape.

---

## Development

```sh
pip install -e ".[dev]"
pytest
```

---

## License

MIT
