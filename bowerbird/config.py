"""Project configuration: bowerbird.toml, merged with defaults and CLI flags."""
from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from typing import Any

try:
    import tomllib  # py3.11+
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None

CONFIG_NAME = "bowerbird.toml"

# Measured on a cloned Qwen3-TTS voice. `bowerbird build` rewrites this per
# project from the real output, so the length estimate sharpens after one run.
DEFAULT_CHARS_PER_SEC = 17.0


@dataclass
class Config:
    # project
    target_seconds: float = 210.0          # 3:30
    chars_per_sec: float = DEFAULT_CHARS_PER_SEC
    out: str = "deck.mp4"

    # voice
    backend: str = "voicebox"
    url: str = "http://localhost:17493"
    profile_name: str = "bowerbird narrator"
    engine: str | None = None
    language: str = "en"
    seed: int | None = 20251006
    instruct: str | None = (
        "Confident presenter. Measured, warm, conversational. "
        "Keep momentum between sentences; do not over-enunciate."
    )

    # slides
    rasterizer: str = "auto"               # auto | chrome | pdf | office | images
    width: int = 1920
    height: int = 1080
    fps: int = 30
    dpi: int = 150                         # pdf rasterizing

    # audio
    loudness_i: float = -16.0
    loudness_tp: float = -1.5
    loudness_lra: float = 9.0
    audio_bitrate: str = "160k"

    # pacing
    default_gap: float = 0.45

    extra: dict[str, Any] = field(default_factory=dict)

    # ---- derived
    @property
    def target_chars(self) -> int:
        """Narration budget, once declared gaps are taken out."""
        return int(self.target_seconds * self.chars_per_sec)

    def estimate(self, chars: int, gaps: float) -> float:
        return chars / self.chars_per_sec + gaps


def _coerce(cfg: Config, data: dict[str, Any]) -> Config:
    known = {f for f in cfg.__dataclass_fields__ if f != "extra"}
    fields, extra = {}, {}
    for k, v in data.items():
        (fields if k in known else extra)[k] = v
    out = replace(cfg, **fields)
    out.extra.update(extra)
    return out


def load(root: str = ".") -> Config:
    """Read bowerbird.toml if present. Missing file is fine - defaults are usable."""
    cfg = Config()
    path = os.path.join(root, CONFIG_NAME)
    if not os.path.exists(path):
        return cfg
    if tomllib is None:  # pragma: no cover
        raise RuntimeError("Reading %s needs Python 3.11+ (tomllib)." % CONFIG_NAME)
    with open(path, "rb") as fh:
        data = tomllib.load(fh)
    # allow either flat keys or [bowerbird] table
    if "bowerbird" in data and isinstance(data["bowerbird"], dict):
        data = {**{k: v for k, v in data.items() if k != "bowerbird"}, **data["bowerbird"]}
    return _coerce(cfg, data)


def apply_overrides(cfg: Config, **flags: Any) -> Config:
    """CLI flags win over the file. None means 'not supplied'."""
    return _coerce(cfg, {k: v for k, v in flags.items() if v is not None})


def write_default(root: str, target_seconds: float) -> str:
    path = os.path.join(root, CONFIG_NAME)
    if os.path.exists(path):
        return path
    mins, secs = divmod(int(target_seconds), 60)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(
            "# bowerbird project config. Every key is optional.\n"
            "# CLI flags override these; per-slide keys in script.json override both.\n\n"
            "target_seconds = %g   # %d:%02d\n"
            "out = \"deck.mp4\"\n\n"
            "# Voice (local Voicebox by default - nothing leaves your machine)\n"
            "backend = \"voicebox\"\n"
            "url = \"http://localhost:17493\"\n"
            "# engine = \"qwen\"\n"
            "# seed = 20251006        # fixed seed keeps the voice consistent across slides\n\n"
            "# Slides\n"
            "rasterizer = \"auto\"     # auto | chrome | pdf | office | images\n"
            "width = 1920\n"
            "height = 1080\n"
            "fps = 30\n\n"
            "# Pacing: default pause after a slide, in seconds.\n"
            "# Override per slide with \"gap_after\" in script.json.\n"
            "default_gap = 0.45\n"
            % (target_seconds, mins, secs)
        )
    return path


def update_chars_per_sec(root: str, value: float) -> None:
    """Persist the measured speaking rate so the next estimate is calibrated."""
    path = os.path.join(root, CONFIG_NAME)
    line = "chars_per_sec = %.2f" % value
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(line + "\n")
        return
    text = open(path, encoding="utf-8").read()
    out, seen = [], False
    for ln in text.splitlines():
        if ln.strip().startswith("chars_per_sec"):
            out.append(line)
            seen = True
        else:
            out.append(ln)
    if not seen:
        out.append("")
        out.append("# Measured from the last build - keeps length estimates honest.")
        out.append(line)
    open(path, "w", encoding="utf-8").write("\n".join(out) + "\n")
