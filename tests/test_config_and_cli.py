import pytest

from bowerbird import config as C
from bowerbird import cli
from bowerbird import draft
from bowerbird import rasterize as R


# ---------------------------------------------------------------- config
def test_defaults_without_a_config_file(tmp_path):
    cfg = C.load(str(tmp_path))
    assert cfg.target_seconds == 210.0
    assert cfg.backend == "voicebox"


def test_toml_overrides_defaults(tmp_path):
    (tmp_path / "bowerbird.toml").write_text(
        'target_seconds = 120\nout = "x.mp4"\nfps = 24\n')
    cfg = C.load(str(tmp_path))
    assert cfg.target_seconds == 120
    assert cfg.out == "x.mp4"
    assert cfg.fps == 24


def test_namespaced_table_also_works(tmp_path):
    (tmp_path / "bowerbird.toml").write_text('[bowerbird]\nfps = 60\n')
    assert C.load(str(tmp_path)).fps == 60


def test_unknown_keys_go_to_extra_not_crash(tmp_path):
    (tmp_path / "bowerbird.toml").write_text('wat = 1\n')
    assert C.load(str(tmp_path)).extra["wat"] == 1


def test_cli_flags_beat_the_file():
    cfg = C.Config(fps=30, engine=None)
    out = C.apply_overrides(cfg, fps=60, engine=None)
    assert out.fps == 60
    assert out.engine is None          # None means "not supplied", not "set to None"


def test_target_chars_tracks_rate():
    assert C.Config(target_seconds=210, chars_per_sec=17.0).target_chars == 3570


def test_chars_per_sec_is_persisted_and_replaced(tmp_path):
    root = str(tmp_path)
    C.write_default(root, 210)
    C.update_chars_per_sec(root, 17.42)
    assert "chars_per_sec = 17.42" in (tmp_path / "bowerbird.toml").read_text()
    C.update_chars_per_sec(root, 18.00)
    text = (tmp_path / "bowerbird.toml").read_text()
    assert "chars_per_sec = 18.00" in text
    assert "17.42" not in text          # replaced, not appended twice


def test_write_default_never_clobbers(tmp_path):
    (tmp_path / "bowerbird.toml").write_text("fps = 1\n")
    C.write_default(str(tmp_path), 210)
    assert (tmp_path / "bowerbird.toml").read_text() == "fps = 1\n"


# ---------------------------------------------------------------- durations
@pytest.mark.parametrize("text,secs", [
    ("3:30", 210.0), ("210", 210.0), ("0:45", 45.0),
    ("3m30s", 210.0), ("90s", 90.0), ("2m", 120.0), ("1:05.5", 65.5),
])
def test_parse_duration(text, secs):
    assert cli.parse_duration(text) == pytest.approx(secs)


@pytest.mark.parametrize("bad", ["", "soon", "3:99", "--"])
def test_parse_duration_rejects_junk(bad):
    with pytest.raises(ValueError):
        cli.parse_duration(bad)


def test_natural_key_orders_numerically():
    names = ["s10.png", "s2.png", "s1.png"]
    assert sorted(names, key=R.natural_key) == ["s1.png", "s2.png", "s10.png"]


# ---------------------------------------------------------------- rasterizer
@pytest.mark.parametrize("name,expected", [
    ("deck.html", "chrome"), ("deck.pdf", "pdf"),
    ("deck.pptx", "office"), ("deck.png", "images"),
])
def test_backend_picked_from_extension(name, expected):
    assert R.pick_backend(name) == expected


def test_keynote_is_refused_with_a_way_out():
    with pytest.raises(R.RasterError, match="Export To"):
        R.pick_backend("deck.key")


def test_forced_backend_wins():
    assert R.pick_backend("deck.pdf", "images") == "images"


def test_html_sections_prefers_slide_class():
    raw = ("<section class='note'>skip</section>"
           "<section class='slide a'>one</section>"
           "<section class='b slide'>two</section>")
    got = R.html_sections(raw)
    assert len(got) == 2 and "one" in got[0] and "two" in got[1]


def test_html_sections_falls_back_to_any_section():
    raw = "<section>one</section><section>two</section>"
    assert len(R.html_sections(raw)) == 2


# ---------------------------------------------------------------- draft
def test_prompt_states_the_budget_and_flags_status():
    cfg = C.Config(target_seconds=210, chars_per_sec=17.0)
    p = draft.build_prompt({1: "Intro", 2: "Roadmap stuff"}, {2: "roadmap"}, cfg)
    assert "3:30" in p
    assert str(cfg.target_chars) in p
    assert "SLIDE 1" in p and "SLIDE 2" in p
    assert "STATUS" in p and "roadmap" in p
    assert 'um' in p.lower()            # the no-filler rule is present


@pytest.mark.parametrize("reply", [
    '[{"slide": 1, "text": "hi"}]',
    'Sure!\n```json\n[{"slide": 1, "text": "hi"}]\n```\nHope that helps.',
    'Here you go: [{"slide": 1, "text": "hi"}]',
])
def test_parse_reply_tolerates_wrapping(reply):
    assert draft.parse_reply(reply) == [{"slide": 1, "text": "hi"}]


def test_parse_reply_rejects_non_array():
    with pytest.raises(ValueError):
        draft.parse_reply("no json here")
