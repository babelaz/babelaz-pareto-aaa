"""--refs with the paper preset: the user's pictures replace the house paper style (brief, judge, run-ready line), and
the round report says plainly when there is nothing to choose."""

from pathlib import Path

from babelaz_pareto_aaa import brief as bf
from babelaz_pareto_aaa import judge, pick

STUDIO = Path(__file__).resolve().parents[1] / "skills" / "aaa-studio"
TASTE = "Shape language: rounded glass tiles.\nPalette: #0a2540 #7fdbff."


def _brief(preset, taste):
    art = (STUDIO / "presets" / f"{preset}.md").read_text(encoding="utf-8")
    return bf.worker_brief(n=0, prompt="make me tetris", preset=preset, art=art, taste=taste, fixes=[bf.BUILD],
                           keep=[], locked=None, studio=str(STUDIO))


def test_paper_with_refs_uses_only_the_refs_art_direction():
    text = _brief("paper", TASTE)
    assert "Art direction (from the user's --refs pictures; it replaces the house paper style" in text
    assert TASTE in text and bf.READABLE in text
    assert "Art direction (paper preset)" not in text
    assert "Paper Craft" not in text and "cut-card" not in text and "#faf3e5" not in text
    assert "\nTaste:" not in text


def test_paper_without_refs_keeps_the_house_style():
    text = _brief("paper", "")
    assert "Art direction (paper preset):" in text and "Paper Craft" in text and "- Readability:" in text
    assert "--refs pictures" not in text


def test_brick_with_refs_keeps_the_preset_art_and_adds_the_taste():
    text = _brief("brick", TASTE)
    assert "Art direction (brick preset):" in text and "Paper Wall" in text and "- Readability:" in text
    assert "Taste: " + TASTE in text
    assert "replaces the house paper style" not in text


def test_paper_judge_with_refs_takes_only_quality_from_the_anchors():
    text = judge.judge_text(["E1"], {}, 2, "paper")
    assert judge.CROSS_GAME_REFS in text and judge.CROSS_GAME not in text
    assert "Never use them for the look" in text
    assert "Then 2 REFERENCE picture(s) the user chose: they alone define the look." in text
    assert "alongside the anchors" not in text
    assert "the score cannot be above 7, and making it readable is the first fix" in text  # cap rule unchanged


def test_brick_judge_with_refs_is_unchanged():
    text = judge.judge_text(["E1"], {}, 1, "brick")
    assert "score how well the slice reaches that look, alongside the anchors" in text
    assert judge.CROSS_GAME_REFS not in text and judge.CROSS_GAME not in text


def test_run_ready_style_line():
    assert pick.style_note("paper", 0) == pick.PAPER_NOTE
    assert pick.style_note("paper", 2) == ("Style: your 2 --refs picture(s) replace the house paper style; the anchors "
                                           "only set the quality bar.")
    assert "add to the brick preset" in pick.style_note("brick", 1)
    assert pick.style_note("catch", 0) == ""
    assert pick.PAPER_NOTE not in pick.style_note("paper", 1)


def _report(mandatory, offered):
    return bf.round_report(line="STATUS: CAP · score 6/10", n=1, runs=1, cap=6, done="1/1 done", mandatory=mandatory,
                           offered=offered, keep_count=0, final=False)


def test_report_says_keep_is_empty_when_nothing_is_offered():
    report = _report([{"id": "G1", "text": "Fix the frame budget."}], [])
    assert report.splitlines()[-1] == "No fixes to choose: G1 is added automatically. Call aaa_round with keep = []."
    assert "keep at least one" not in report
    two = _report([{"id": "G1", "text": "a"}, {"id": "S0", "text": "b"}], [])
    assert "No fixes to choose: G1 and S0 are added automatically." in two


def test_report_with_offered_fixes_is_unchanged():
    report = _report([{"id": "G1", "text": "a"}], [{"id": "J1", "text": "b"}])
    assert "No fixes to choose" not in report and report.splitlines()[-1].startswith("Next: call aaa_round")
