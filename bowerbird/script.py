"""script.json: the one file you actually edit.

Shape:
    [{"slide": 1, "title": "...", "text": "...", "gap_after": 0.45}, ...]

Optional per-slide keys: engine, seed, instruct, status.
"slide" numbers index slides/sNN.png. A slide with empty text is skipped
entirely - useful for a title card you want silent.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

SCRIPT_NAME = "script.json"

# Words that mean "not shipped yet". If a slide shows one of these, narration
# that doesn't hedge will oversell it - the single biggest credibility risk in
# a pitch deck, so `check` warns about it.
STATUS_WORDS = ("planned", "roadmap", "coming soon", "in progress", "beta",
                "upcoming", "future", "not yet", "next up", "wip", "preview")
HEDGE_WORDS = ("planned", "next", "soon", "will ", "we'll", "going to", "future",
               "not today", "not yet", "roadmap", "upcoming", "aim to", "intend",
               "would ", "once ", "eventually", "beta", "in progress")


@dataclass
class Slide:
    slide: int
    text: str
    title: str = ""
    gap_after: float | None = None
    status: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def chars(self) -> int:
        return len(self.text)

    def gap(self, default: float) -> float:
        return default if self.gap_after is None else float(self.gap_after)

    def to_json(self) -> dict[str, Any]:
        d: dict[str, Any] = {"slide": self.slide}
        if self.title:
            d["title"] = self.title
        d["text"] = self.text
        if self.gap_after is not None:
            d["gap_after"] = self.gap_after
        if self.status:
            d["status"] = self.status
        d.update(self.extra)
        return d


class ScriptError(ValueError):
    pass


_KNOWN = {"slide", "text", "title", "gap_after", "status"}


def load(root: str = ".") -> list[Slide]:
    path = os.path.join(root, SCRIPT_NAME)
    if not os.path.exists(path):
        raise ScriptError(
            "No %s here.\nRun: bowerbird init <your-deck>" % SCRIPT_NAME)
    try:
        raw = json.load(open(path, encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ScriptError("%s isn't valid JSON (%s)" % (SCRIPT_NAME, e)) from e
    if not isinstance(raw, list):
        raise ScriptError("%s must be a list of slide objects." % SCRIPT_NAME)

    out, seen = [], set()
    for i, row in enumerate(raw):
        if not isinstance(row, dict):
            raise ScriptError("Entry %d is not an object." % i)
        if "slide" not in row:
            raise ScriptError("Entry %d has no \"slide\" number." % i)
        try:
            n = int(row["slide"])
        except (TypeError, ValueError):
            raise ScriptError("Entry %d: \"slide\" must be a number." % i) from None
        if n in seen:
            raise ScriptError("Slide %d appears twice." % n)
        seen.add(n)
        gap = row.get("gap_after")
        if gap is not None:
            try:
                gap = float(gap)
            except (TypeError, ValueError):
                raise ScriptError("Slide %d: gap_after must be a number." % n) from None
            if gap < 0:
                raise ScriptError("Slide %d: gap_after can't be negative." % n)
        out.append(Slide(
            slide=n,
            text=str(row.get("text", "")).strip(),
            title=str(row.get("title", "")),
            gap_after=gap,
            status=str(row.get("status", "")),
            extra={k: v for k, v in row.items() if k not in _KNOWN},
        ))
    out.sort(key=lambda s: s.slide)
    return out


def save(slides: list[Slide], root: str = ".") -> str:
    path = os.path.join(root, SCRIPT_NAME)
    data = [s.to_json() for s in slides]
    open(path, "w", encoding="utf-8").write(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    return path


def spoken(slides: list[Slide]) -> list[Slide]:
    """Slides with narration. Empty text means the slide is skipped."""
    return [s for s in slides if s.text]


def budget(slides: list[Slide], chars_per_sec: float, default_gap: float) -> dict[str, float]:
    live = spoken(slides)
    chars = sum(s.chars for s in live)
    gaps = sum(s.gap(default_gap) for s in live)
    return {
        "slides": len(live),
        "chars": chars,
        "gaps": gaps,
        "speech": chars / chars_per_sec if chars_per_sec else 0.0,
        "total": (chars / chars_per_sec if chars_per_sec else 0.0) + gaps,
    }


def detect_status(slide_text: str) -> str:
    """Pull a 'not shipped yet' marker out of a slide's own text, if present."""
    low = slide_text.lower()
    for w in STATUS_WORDS:
        if re.search(r"\b%s\b" % re.escape(w), low):
            return w
    return ""


def overclaims(slide: Slide) -> bool:
    """True when a slide is marked unshipped but its narration never hedges."""
    if not slide.status or not slide.text:
        return False
    low = slide.text.lower()
    return not any(h in low for h in HEDGE_WORDS)


def scaffold(slide_texts: dict[int, str], default_gap: float) -> list[Slide]:
    """Build a starter script from whatever text the deck gave us."""
    out = []
    for n in sorted(slide_texts):
        body = " ".join(slide_texts[n].split())
        title = body[:60].strip()
        out.append(Slide(
            slide=n,
            text="",
            title=title,
            gap_after=default_gap,
            status=detect_status(body),
        ))
    return out
