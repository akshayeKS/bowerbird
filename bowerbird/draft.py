"""Drafting the narration.

By default bowerbird writes a prompt and gets out of the way: no API key, no
provider dependency, works offline. Paste it into whatever model you like and
save the result as script.json.

`--llm` is opt-in and calls the Anthropic API directly over urllib, so the core
package still has zero dependencies.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

DEFAULT_MODEL = "claude-opus-4-8"
PROMPT_FILE = "DRAFT_PROMPT.md"

# The writing rules, learned by getting each one wrong first.
RULES = """\
HOW TO WRITE IT

1. Don't read the slide. The viewer can already read it. Narration supplies what
   the slide doesn't: why it matters, and where to look.

2. Write ONE continuous argument, not N standalone paragraphs. Each slide should
   open by picking up where the previous one left off - "And here's the scale...",
   "But...", "So what do you actually do?". Without this it sounds like a list of
   announcements being read out, which is the single most common failure.

3. Respect the length budget. Spoken narration runs at roughly %(cps).1f characters
   per second. Your TOTAL across all slides must be about %(chars)d characters for a
   %(mins)d:%(secs)02d video. Count as you go - narration drafts overshoot badly and it
   is not visible by eye.

4. Spell numbers out: "eight hundred sixty", not "860". Text-to-speech mangles
   digits. Don't read URLs or ticket numbers aloud - the slide already shows them.

5. Never write "um" or "uh". TTS pronounces them as words and it sounds worse
   than no filler. Use connective phrases instead.

6. On demo or screenshot slides, point at what's visible: "And here it is, live.",
   "as you can see...", "Now watch what happens when...".

7. HONESTY. If a slide is marked Planned / Roadmap / Beta / Coming soon, the
   narration MUST say so out loud ("that's planned, not today"). Quietly
   presenting unshipped work as shipped is far worse than sounding stilted.
   Slides below carry a STATUS marker where this was detected.

8. Ground every number in what's actually on that slide. Don't invent figures.

PACING
Set "gap_after" on each slide: the pause in seconds before the next slide.
Use ~0.30 mid-thought (e.g. through a demo sequence that should feel continuous)
and 0.50-0.65 at real transitions. Default is %(gap).2f.

OUTPUT
Reply with ONLY a JSON array, no prose and no code fence:

[
 {"slide": 1, "text": "...", "gap_after": 0.5},
 {"slide": 2, "text": "...", "gap_after": 0.4}
]

One entry per slide listed below, same numbers. Use "" for text on any slide that
should stay silent.
"""


def build_prompt(slide_texts: dict[int, str], statuses: dict[int, str], cfg) -> str:
    mins, secs = divmod(int(cfg.target_seconds), 60)
    rules = RULES % {
        "cps": cfg.chars_per_sec,
        "chars": cfg.target_chars,
        "mins": mins,
        "secs": secs,
        "gap": cfg.default_gap,
    }
    per_slide = int(cfg.target_chars / max(1, len(slide_texts)))
    lines = [
        "You are writing the spoken narration for a slide deck video.",
        "",
        "TARGET LENGTH: %d:%02d (about %d characters total, ~%d per slide on average,"
        " though it should be uneven - setup slides need more, title and closing"
        " slides much less)." % (mins, secs, cfg.target_chars, per_slide),
        "",
        rules,
        "",
        "THE DECK (%d slides). This is the text actually on each slide:" % len(slide_texts),
        "",
    ]
    for n in sorted(slide_texts):
        body = " ".join((slide_texts[n] or "").split())
        lines.append("--- SLIDE %d ---" % n)
        if statuses.get(n):
            lines.append("STATUS: marked \"%s\" - narration must say this isn't shipped yet."
                         % statuses[n])
        lines.append(body or "(no text extracted - describe from the slide image)")
        lines.append("")
    return "\n".join(lines)


def write_prompt(prompt: str, root: str = ".") -> str:
    path = os.path.join(root, PROMPT_FILE)
    open(path, "w", encoding="utf-8").write(prompt)
    return path


def parse_reply(text: str) -> list[dict]:
    """Pull the JSON array out of a model reply, fenced or not."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1:
        raise ValueError("No JSON array found in the reply.")
    data = json.loads(text[start:end + 1])
    if not isinstance(data, list):
        raise ValueError("Expected a JSON array of slide objects.")
    return data


def call_anthropic(prompt: str, model: str = DEFAULT_MODEL,
                   api_key: str | None = None, max_tokens: int = 8000) -> str:
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError(
            "--llm needs an API key. Set ANTHROPIC_API_KEY, or drop --llm and use\n"
            "the generated %s with any model you like." % PROMPT_FILE)
    body = json.dumps({
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body, method="POST",
        headers={"content-type": "application/json",
                 "x-api-key": key,
                 "anthropic-version": "2023-06-01"})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            js = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError("Anthropic API error %s: %s"
                           % (e.code, e.read()[:400].decode(errors="ignore"))) from None
    except urllib.error.URLError as e:
        raise RuntimeError("Couldn't reach the Anthropic API (%s)." % e.reason) from None
    return "".join(b.get("text", "") for b in js.get("content", []))
