"""Unit tests for pure logic: fix IDs, round scores, STATUS precedence, PLATEAU, keep-best."""

from babelaz_pareto_aaa import gates
from babelaz_pareto_aaa import status as st


def rnd(n, judge=None, confirm=None, gates=(), unreviewed=None):
    return st.Round(
        n=n,
        judge=judge,
        confirm=confirm,
        gate_fails=[st.gate_fix(i, g) for i, g in enumerate(gates, 1)],
        unreviewed=unreviewed,
    )


def test_score_is_lower_of_judge_and_confirm():
    assert rnd(0, 9, 8).score == 8
    assert rnd(0, 8).score == 8
    assert rnd(0).score is None


def test_gated_round_has_no_score():
    r = st.Round(n=0, gate_fails=[st.gate_fix(1, "errors")])
    assert r.gated and r.score is None


def test_aaa_needs_clean_gates_and_both_at_8():
    assert st.status([rnd(0, 8, 8)], 0) == "AAA"
    assert st.status([rnd(0, 8, 7)], 0) == "CONTINUE"
    assert st.status([rnd(0, 8, 8, gates=["errors"])], 0) == "CONTINUE"


def test_cap_after_six_worker_runs_and_aaa_wins_over_cap():
    assert st.status([rnd(0, 6)], 5) == "CONTINUE"
    assert st.status([rnd(0, 6)], 6) == "CAP"
    assert st.status([rnd(0, 8, 8)], 6) == "AAA"


def test_unreviewed_wins_over_everything():
    assert st.status([rnd(0, 8, 8, unreviewed="plugin hash mismatch")], 6) == "UNREVIEWED"


def test_plateau_two_judged_rounds_that_do_not_beat_the_best():
    assert st.status([rnd(0, 7), rnd(1, 6), rnd(2, 7)], 0) == "PLATEAU"


def test_no_plateau_when_a_round_improves():
    assert st.status([rnd(0, 5), rnd(1, 6), rnd(2, 7)], 0) == "CONTINUE"


def test_confirm_disagreement_does_not_count_toward_plateau():
    # round 2 disagrees (confirm 4) and must not count; without that rule rounds 2 and 3 would plateau
    rounds = [rnd(0, 5), rnd(1, 6), rnd(2, 9, 4), rnd(3, 6)]
    assert st.status(rounds, 0) == "CONTINUE"


def test_keep_best_ties_go_to_later_round():
    best, judged = st.best_round([rnd(0, 7), rnd(1, 7)])
    assert best.n == 1 and judged


def test_keep_best_skips_gated_rounds():
    best, judged = st.best_round([rnd(0, 9, 9), rnd(1, gates=["x"])])
    assert best.n == 0 and judged


def test_no_judged_round_ships_latest_flagged():
    best, judged = st.best_round([rnd(0, gates=["x"]), rnd(1, gates=["y"])])
    assert best.n == 1 and not judged


def test_judged_fixes_need_known_evidence_and_are_capped_at_two():
    raw = [
        {"text": "a", "evidence": ["E1"]},
        {"text": "b", "evidence": ["E9"]},
        {"text": "c", "evidence": []},
        {"text": "d", "evidence": ["E2", "E1"]},
        {"text": "e", "evidence": ["E1"]},
    ]
    out = st.judged_fixes(raw, {"E1", "E2"})
    assert [f["id"] for f in out] == ["J1", "J2"]
    assert [f["text"] for f in out] == ["a", "d"]
    many = [{"text": f"f{i}", "evidence": ["E1"]} for i in range(7)]
    assert [f["id"] for f in st.judged_fixes(many, {"E1"}, "C")] == ["C1", "C2"]


def test_keep_list_keeps_short_strings_only():
    assert st.keep_list(["buttons are flat", "", 3, "  sound rises  "]) == ["buttons are flat", "sound rises"]
    assert st.keep_list("not a list") == []
    assert len(st.keep_list([f"k{i}" for i in range(9)])) == st.MAX_KEEP


def test_top_complaint_becomes_j1_only_below_8_with_no_fix():
    assert st.top_complaint_fix(6, [], "buttons look glossy")[0]["id"] == "J1"
    assert st.top_complaint_fix(8, [], "buttons look glossy") == []


def test_confirm_fixes_replace_judge_fixes_only_on_disagreement():
    j = [{"id": "J1", "text": "j", "evidence": ["E1"]}]
    c = [{"id": "C1", "text": "c", "evidence": ["E1"]}]
    assert st.pick_fixes(j, c, True) == c
    assert st.pick_fixes(j, c, False) == j


def test_status_line_format():
    assert st.status_line("AAA", 8) == "STATUS: AAA · score 8/10"


def test_lock_passes_only_when_smoke_fails_on_blank_for_a_game_reason():
    assert gates.lock_passed({"ok": False, "err": "Error: aaaGame missing"})
    assert gates.lock_passed({"ok": True, "r": {"ok": False}})
    # a stray TypeError is not a failure on blank, so the lock does not pass
    assert not gates.lock_passed({"ok": False, "err": "TypeError: null is not an object (reading 'getContext')"})
    assert not gates.lock_passed({"ok": True, "r": {"ok": True}})


def test_smoke_that_passes_on_blank_never_reaches_aaa():
    # the lock failed, so the round carries S0 and is gated: it is never judged, so it can never be AAA
    lock_fail = st.Round(n=0, gate_fails=[st.smoke_lock_fix()], judge=8, confirm=8)
    assert not lock_fail.aaa_pass
    assert st.status([lock_fail], 0) == "CONTINUE"


def test_why_below_8_says_why_and_the_next_step():
    """Design decision: a final run below 8 gets one plain line after the STATUS; AAA and CONTINUE get none."""
    from babelaz_pareto_aaa import judge
    R = st.Round
    assert st.why_below_8("AAA", [R(0, judge=8, confirm=8)], True) == ""
    assert st.why_below_8("CONTINUE", [R(0, judge=6)], True) == ""
    plat = [R(0, judge=5), R(1, judge=7), R(2, judge=7), R(3, judge=6)]
    assert st.why_below_8("PLATEAU", plat, True).startswith("Why: scores levelled off at 7 for 3 rounds. Next: give "
                                                               "reference pictures")
    low = [R(0, judge=5), R(1, judge=6), R(2, judge=6), R(3, judge=5)]
    assert "stronger worker model" in st.why_below_8("PLATEAU", low, True)
    assert st.why_below_8("CAP", low, True).startswith("Why: the round budget ran out (6 worker runs")
    assert "smoke test never locked" in st.why_below_8("CAP", [R(0, gate_fails=[{"id": "S0"}])] * 3, False)
    assert "no round passed the code gates" in st.why_below_8("CAP", [R(0, gate_fails=[{"id": "G1"}])], True)
    split = [R(0, judge=6), R(1, judge=8, confirm=7), R(2, judge=7)]
    assert "the judge gave 8 but the second opinion gave 7" in st.why_below_8("PLATEAU", split, True)
    assert "judge failed twice" in st.why_below_8("UNREVIEWED", [R(0, unreviewed="judge failed twice: x")], True)
    assert judge.borderline({"dated": 6, "current": 8}) and not judge.borderline({"dated": 5, "current": 8})
    assert not judge.borderline({})
