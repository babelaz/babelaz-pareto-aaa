"""Unit tests for the worker brief (review 1: fix 2, the smoke file's rules; M3 note 8, engines and overused words)."""

from pathlib import Path

from babelaz_pareto_aaa import brief as bf
from babelaz_pareto_aaa import status as st

STUDIO = Path(__file__).resolve().parents[1] / "skills" / "aaa-studio"


def _brief(**kw):
    args = dict(n=1, prompt="make me a brick-breaker", preset="brick", art="Art.", taste="", keep=[],
                fixes=[{"id": "J1", "text": "Ease the hit flash."}], locked={"index.html", "game.js", "aaa.smoke.js"},
                studio=str(STUDIO))
    args.update(kw)
    return bf.worker_brief(**args)


def test_locked_brief_never_lists_the_smoke_file_as_editable():
    line = next(ln for ln in _brief().splitlines() if ln.startswith("Edit only these files"))
    assert "aaa.smoke.js" not in line and "game.js" in line and "index.html" in line


def test_smoke_file_may_be_written_only_when_a_fix_asks():
    assert "unless a fix below asks you to write it" in _brief()
    assert "aaa.smoke.js" not in st.smoke_lock_fix()["text"]  # S0 on its first retry does not ask the worker


def test_brief_states_the_engine_rules_and_carries_the_overused_words_text():
    text = _brief(locked=None, n=0)
    assert "three.js 0.187.0" in text and "PixiJS 8" in text and "no CDN" in text
    assert "neon, cyber" in text and "vibrant" in text


def test_refs_art_direction_is_short_and_plain():
    """M4 --refs (Brief issue 19, DECIDED): the frozen art direction keeps at most 10 lines and 1,200 characters, and the
    judge prompt names the reference pictures only when there are some (no refs: the prompt is unchanged)."""
    from babelaz_pareto_aaa import judge
    reply = "```\n" + "\n".join(f"line {i}" for i in range(15)) + "\n```"
    art = bf.refs_art(reply)
    assert art.splitlines() == [f"line {i}" for i in range(10)]
    assert "REFERENCE" not in judge.judge_text(["E1"], {}) and "Then 2 REFERENCE" in judge.judge_text(["E1"], {}, 2)
