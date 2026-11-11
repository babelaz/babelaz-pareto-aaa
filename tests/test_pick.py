"""General mode: preset choice, vague and app replies, the cross-game judge line, the borrowed
anchors and the same-model lines."""

import pytest

from babelaz_pareto_aaa import brief, check, judge, pick


@pytest.mark.parametrize("prompt, preset", [
    ("make me a brick-breaker", "brick"), ("Breakout please", "brick"), ("a block breaker", "brick"),
    ("make me snake", "paper"), ("a falling-blocks puzzle", "paper"), ("tetris", "paper"),
    ("falling blocks", "paper"), ("catch the apples", "catch"), ("fill the basket", "catch"),
    ("make me pong with a paddle", "paper"), ("a brickyard snake", "paper"),
])
def test_preset_choice(prompt, preset):
    assert pick.preset(prompt) == preset


@pytest.mark.parametrize("prompt", ["make me a game", "something fun please", "Make me a GAME!", "a cool simple web game",
                                    "can you build something for us", "make me a game --refs ./pics"])
def test_vague_prompts_get_the_examples(prompt):
    assert pick.refuse(prompt) == pick.VAGUE
    assert pick.VAGUE.startswith("Which game? For example: /aaa make me a brick-breaker")


@pytest.mark.parametrize("prompt", ["make me a todo app", "build a landing page", "a To-Do website", "make me a CRM"])
def test_app_prompts_get_the_games_only_reply(prompt):
    assert pick.refuse(prompt) == pick.NOT_APP


@pytest.mark.parametrize("prompt", ["a puzzle game app", "make me snake", "make me a brick-breaker",
                                    "a falling-blocks puzzle", "an arcade shop keeper", "applesauce catcher"])
def test_game_prompts_start(prompt):
    assert pick.refuse(prompt) is None


def test_judge_text_has_the_cross_game_line_only_for_paper():
    line = ("The anchors show a different game (a brick-breaker). Use them only for the quality level: finish, "
            "material, feedback and cohesion. Never mark the slice down for being a different game or layout.")
    paper = judge.judge_text(["E1"], {}, 0, "paper")
    assert line in paper
    assert paper.index("The three anchors show the same moment of play.\n" + line) >= 0
    for preset in ("brick", "catch", ""):
        assert line not in judge.judge_text(["E1"], {}, 0, preset)
    assert paper.index(line) < judge.judge_text(["E1"], {}, 2, "paper").index("Then 2 REFERENCE")


def test_paper_borrows_the_brick_anchors():
    assert check.anchors("paper") == check.anchors("brick")
    assert pick.anchor_set("paper") == "brick" and pick.anchor_set("catch") == "catch"
    assert check.anchors("catch") != check.anchors("brick")


def test_same_model_lines():
    same = {"judge": "go/deepseek-v4.1-flash", "confirm": "go/deepseek-v4.1-flash"}
    out = pick.final_who(same, True, "AAA")
    assert out == (" Judged by go/deepseek-v4.1-flash, second opinion by go/deepseek-v4.1-flash."
                   "\nJudge and second opinion: the same model (go/deepseek-v4.1-flash).")
    assert "the same model" not in pick.final_who(same, True, "PLATEAU")
    assert pick.final_who({"judge": "a/x", "confirm": "b/y"}, True, "AAA") == " Judged by a/x, second opinion by b/y."
    assert pick.final_who({"judge": "a/x"}, False, "CAP") == ""
    assert pick.SAME_MODEL.startswith("One model does every job, so the second opinion at 8 is not independent.")


def test_same_model_line_stays_out_of_the_status_line():
    finish = "Best round 1 (score 8) is checked out in /r, with aaa-proof.png." + pick.final_who(
        {"judge": "p/m", "confirm": "p/m"}, True, "AAA")
    report = brief.round_report(line="STATUS: AAA · score 8/10", n=1, runs=1, cap=6, done="1/1 done", mandatory=[],
                                offered=[], keep_count=0, final=True, finish=finish)
    lines = report.splitlines()
    assert lines[0] == "STATUS: AAA · score 8/10 (round 1; worker runs 1 of 6; fixes 1/1 done)"
    assert "Judge and second opinion: the same model (p/m)." in lines[1:]
    assert sum(ln.startswith("STATUS") for ln in lines) == 1


def test_worker_brief_has_the_loop_and_title_rules(tmp_path):
    (tmp_path / "checklists").mkdir()
    (tmp_path / "checklists" / "overused-words.md").write_text("words", encoding="utf-8")
    text = brief.worker_brief(n=0, prompt="make me snake", preset="paper", art="art", taste="", fixes=[brief.BUILD],
                              keep=[], locked=None, studio=str(tmp_path))
    assert "Loop: run ALL game logic and drawing from requestAnimationFrame" in text
    assert "No setInterval or setTimeout game loop" in text
    assert "The title starts play only on a key or pointer input" in text


def test_judge_text_has_the_readability_rule_for_every_preset():
    rule = ("- Readability: if the piece the player tracks (ball, head, falling piece) or the player's own piece is hard "
            "to see at play size in the evidence, the score cannot be above 7, and making it readable is the first fix.")
    for preset in ("brick", "catch", "paper", ""):
        assert rule in judge.judge_text(["E1"], {}, 0, preset)
