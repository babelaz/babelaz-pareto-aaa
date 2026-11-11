"""Unit tests for the batch 2 pure checks: the strength lock rule, the state cycle, sharp drawing, screenshots by state,
the FPS retry rule and the detail crop box. One or more per new check."""

from babelaz_pareto_aaa import gates
from babelaz_pareto_aaa.detail import detail_box


def s(name, t):
    return {"k": "state", "s": name, "t": t}


def i(t):
    return {"k": "input", "t": t}


def gate(m, prefix):
    return next(g["ok"] for g in gates.evaluate(m) if g["name"].startswith(prefix))


def test_weak_smoke_that_passes_on_a_frozen_loop_is_rejected():
    # a smoke that returns ok without waiting for the game passes on a frozen loop or blocked input: not a failure
    assert not gates.lock_passed({"ok": True, "r": {"ok": True, "ended": "title"}})


def test_timeout_that_names_the_game_is_a_strength_failure():
    assert gates.lock_passed({"ok": False, "err": "Error: game never reached won or lost"})
    assert not gates.lock_passed({"ok": False, "err": "TypeError: x is undefined"})
    # first real runs: a timeout naming the state is a game reason
    assert gates.lock_passed({"ok": False, "err": "Error: Timed out after 30000 ms waiting for 'won' or 'lost' (current state: playing)"})
    assert not gates.lock_passed({"ok": False, "err": "ReferenceError: foo is not defined"})


def test_clean_cycle_passes_and_reports_both_timings():
    c = gates.cycle_check([s("title", 0), s("playing", 100), s("won", 5100), i(5200), s("title", 5210)])
    assert c["sequence_ok"] and c["hooks"]
    assert c["win_loss_ms"] == 5000 and c["restart_ms"] == 10


def test_cycle_needs_title_playing_won_or_lost_title_in_order():
    assert not gates.cycle_check([s("title", 0), s("playing", 10), s("title", 20)])["sequence_ok"]
    assert gate({"cycle": gates.cycle_check([s("title", 0), s("playing", 10), s("title", 20)])},
                "state goes") is False


def test_win_or_loss_slower_than_60_s_fails_the_gate():
    c = gates.cycle_check([s("title", 0), s("playing", 100), s("lost", 60200), s("title", 60210)])
    assert c["win_loss_ms"] == 60100
    assert gate({"cycle": c}, "won or lost within 60 s") is False


def test_restart_from_the_restart_input_over_1_s_fails_the_gate():
    c = gates.cycle_check([s("title", 0), s("playing", 10), s("won", 1000), i(2000), s("title", 3500)])
    assert c["restart_ms"] == 1500
    assert gate({"cycle": c}, "restart under 1 s") is False


def test_missing_state_hook_is_flagged():
    c = gates.cycle_check([s("missing", 0)])
    assert c["hooks"] is False and c["sequence_ok"] is False


def test_sharp_drawing_flags_a_backing_store_below_css_size_times_dpr():
    small = [{"id": "canvas", "w": 480, "h": 320, "cssW": 480, "cssH": 320}]
    sharp = [{"id": "canvas", "w": 960, "h": 640, "cssW": 480, "cssH": 320}]
    assert gates.sharp_problems(small, 2, "1280")
    assert gates.sharp_problems(sharp, 2, "1280") == []


def test_screenshots_must_differ_between_states_and_include_an_end_state():
    assert gates.states_problems({"title": "a", "playing": "b", "won": "c"}) == []
    assert gates.states_problems({"title": "a", "playing": "a", "lost": "c"})
    assert gates.states_problems({"title": "a", "playing": "b"})


def test_fps_retry_only_when_fps_is_the_only_failing_gate():
    fps = {"name": "FPS avg >= 55 and 1% low >= 30", "ok": False, "detail": ""}
    ok = {"name": "x", "ok": True, "detail": ""}
    assert gates.fps_only_failing([fps, ok])
    assert not gates.fps_only_failing([fps, {"name": "0 page or console errors", "ok": False, "detail": ""}])


def test_fps_margin_prefers_the_run_closer_to_both_floors():
    dip = gates.fps_margin({"fps_avg": 60, "fps_low": 29.9})
    better = gates.fps_margin({"fps_avg": 58, "fps_low": 31})
    assert dip < better


def test_detail_box_clips_to_the_viewport_and_uses_device_pixels():
    assert detail_box({"x": -10, "y": 100, "w": 200, "h": 100}, 375, 667, 2) == (0, 200, 380, 200)


def test_detail_box_is_none_when_missing_or_off_screen():
    assert detail_box(None, 375, 667, 2) is None
    assert detail_box({"x": 400, "y": 0, "w": 10, "h": 10}, 375, 667, 2) is None


# Measures where every gate passes (review 2 tests start from this and break one thing).
GOOD = {"page_errors": [], "console_errors": [], "layout": [], "axe_serious": 0, "fps_avg": 60, "fps_low": 59,
        "sound_before": 0, "sound_after": 0.2, "smoke_ok": True, "hash_ok": True, "scope_ok": True, "deps": [],
        "sharp": [], "state_shots": {"title": "a", "playing": "b", "lost": "c"},
        "cycle": gates.cycle_check([s("title", 0), s("playing", 100), s("lost", 5100), i(5200), s("title", 5210)])}


def test_lock_needs_every_state_check_not_just_the_hooks():
    assert all(g["ok"] for g in gates.evaluate(GOOD)) and gates.state_checks_ok(GOOD)
    # weak smoke A: the hooks answer, but the game never left the title screen
    assert not gates.state_checks_ok(dict(GOOD, cycle=gates.cycle_check([s("title", 0)])))
    slow = gates.cycle_check([s("title", 0), s("playing", 10), s("won", 1000), i(2000), s("title", 3500)])
    assert not gates.state_checks_ok(dict(GOOD, cycle=slow))
    assert not gates.state_checks_ok(dict(GOOD, state_shots={"title": "a", "playing": "a", "lost": "c"}))
    assert not gates.state_checks_ok({})


def test_fps_retry_keeps_errors_seen_only_in_the_dropped_run(monkeypatch):
    from pathlib import Path

    from babelaz_pareto_aaa import check

    runs = iter([dict(GOOD, fps_avg=50, fps_low=40), dict(GOOD, fps_avg=40, fps_low=20, page_errors=["TypeError: x"])])
    monkeypatch.setattr(check, "_capture", lambda *_a: {"measures": next(runs), "items": []})
    _cap, m, results, retry = check._measure("root", Path("out"), "", False, {})
    assert retry["kept"] == "first" and m["fps_avg"] == 50
    assert m["page_errors"] == ["TypeError: x"]
    assert not next(g["ok"] for g in results if g["name"].startswith("0 page or console errors"))
