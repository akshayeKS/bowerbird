import json

import pytest

from bowerbird import script as S


def write(tmp_path, data):
    (tmp_path / "script.json").write_text(json.dumps(data))
    return str(tmp_path)


def test_load_sorts_and_defaults(tmp_path):
    root = write(tmp_path, [
        {"slide": 3, "text": "three"},
        {"slide": 1, "text": "one", "gap_after": 0.25},
    ])
    slides = S.load(root)
    assert [s.slide for s in slides] == [1, 3]
    assert slides[0].gap(0.45) == 0.25
    assert slides[1].gap(0.45) == 0.45      # falls back to the project default


def test_missing_file_names_the_fix(tmp_path):
    with pytest.raises(S.ScriptError, match="bowerbird init"):
        S.load(str(tmp_path))


def test_rejects_duplicate_slide(tmp_path):
    root = write(tmp_path, [{"slide": 1, "text": "a"}, {"slide": 1, "text": "b"}])
    with pytest.raises(S.ScriptError, match="twice"):
        S.load(root)


def test_rejects_negative_gap(tmp_path):
    root = write(tmp_path, [{"slide": 1, "text": "a", "gap_after": -1}])
    with pytest.raises(S.ScriptError, match="negative"):
        S.load(root)


def test_rejects_non_numeric_slide(tmp_path):
    root = write(tmp_path, [{"slide": "one", "text": "a"}])
    with pytest.raises(S.ScriptError, match="must be a number"):
        S.load(root)


def test_bad_json_is_reported_clearly(tmp_path):
    (tmp_path / "script.json").write_text("{not json")
    with pytest.raises(S.ScriptError, match="isn't valid JSON"):
        S.load(str(tmp_path))


def test_empty_text_slides_are_skipped(tmp_path):
    root = write(tmp_path, [
        {"slide": 1, "text": "spoken"},
        {"slide": 2, "text": "   "},
    ])
    slides = S.load(root)
    assert len(slides) == 2
    assert [s.slide for s in S.spoken(slides)] == [1]


def test_unknown_keys_survive_a_round_trip(tmp_path):
    root = write(tmp_path, [{"slide": 1, "text": "a", "engine": "luxtts", "seed": 7}])
    slides = S.load(root)
    assert slides[0].extra == {"engine": "luxtts", "seed": 7}
    S.save(slides, root)
    again = S.load(root)
    assert again[0].extra["engine"] == "luxtts"


def test_budget_counts_only_spoken_slides():
    slides = [S.Slide(1, "a" * 170, gap_after=0.5),
              S.Slide(2, "", gap_after=9.0)]       # silent slide contributes nothing
    b = S.budget(slides, chars_per_sec=17.0, default_gap=0.45)
    assert b["slides"] == 1
    assert b["chars"] == 170
    assert b["gaps"] == 0.5
    assert b["speech"] == pytest.approx(10.0)
    assert b["total"] == pytest.approx(10.5)


@pytest.mark.parametrize("text,expected", [
    ("Coming soon: automatic budgets", "coming soon"),
    ("Roadmap for next quarter", "roadmap"),
    ("This ships today", ""),
    ("Planning permission granted", ""),      # 'planning' must not match 'planned'
])
def test_detect_status(text, expected):
    assert S.detect_status(text) == expected


def test_overclaim_flags_unhedged_narration():
    bad = S.Slide(1, "Tidepool drafts budgets from production traffic.", status="planned")
    good = S.Slide(2, "Drafting budgets from traffic is planned, not today.", status="planned")
    unmarked = S.Slide(3, "Tidepool drafts budgets.", status="")
    assert S.overclaims(bad) is True
    assert S.overclaims(good) is False
    assert S.overclaims(unmarked) is False     # nothing claimed about status


def test_scaffold_carries_status_and_leaves_text_empty():
    slides = S.scaffold({1: "Intro", 2: "Planned: automatic budgets"}, default_gap=0.4)
    assert [s.slide for s in slides] == [1, 2]
    assert all(s.text == "" for s in slides)
    assert slides[1].status == "planned"
    assert slides[0].gap_after == 0.4
