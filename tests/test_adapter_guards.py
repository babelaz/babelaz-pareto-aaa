"""Unit tests for adapter guards (review 1, fixes 4 and 7) and the tool-deadline path (Brief issue 10, DECIDED). The adapter imports no Hermes at load time, so it is
loaded here as a package from the repo root, without Hermes installed."""

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def adapter():
    spec = importlib.util.spec_from_file_location("aaa_adapter", ROOT / "__init__.py",
                                                  submodule_search_locations=[str(ROOT)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules["aaa_adapter"] = mod
    spec.loader.exec_module(mod)
    return mod


class _Run:
    line = "STATUS: CONTINUE · score 6/10"


def test_stamp_replaces_the_model_status_lines(adapter):
    adapter.hr.RUNS["orch"] = _Run()
    assert adapter._transform_llm_output(response_text="STATUS: AAA\nDone.", session_id="orch") == \
        "STATUS: CONTINUE · score 6/10\nDone."
    assert adapter._transform_llm_output(response_text="STATUS: AAA", session_id="someone else") is None


def test_stamp_failure_never_lets_a_model_status_through(adapter):
    """Hermes keeps the model's text when the hook returns None or "", so the failure path returns a fixed non-final
    line instead (agent/turn_finalizer.py:540-543)."""
    adapter.hr.RUNS["orch"] = _Run()
    for text, sid in ((["STATUS: AAA"], "orch"), ("STATUS: AAA", ["unhashable"])):
        out = adapter._transform_llm_output(response_text=text, session_id=sid)
        assert out == adapter.WITHHELD and out.startswith("STATUS: CONTINUE") and "STATUS: AAA" not in out


def test_smoke_writer_never_sends_a_linked_file(adapter, tmp_path, monkeypatch):
    secret = tmp_path / "secret.env"
    secret.write_text("TOKEN=not-a-real-one")
    root = tmp_path / "run"
    root.mkdir()
    (root / "index.html").write_text("<script src=game.js></script>")
    (root / "game.js").symlink_to(secret)
    (root / "aaa.smoke.js").symlink_to(secret)
    seen = []

    def caller(slot, instructions=""):
        return lambda images, text: (seen.append(text), ("```js\nwindow.aaaSmoke = 1;\n```", None, "fake/m"))[1]

    monkeypatch.setattr(adapter.hr, "_caller", caller)
    run = adapter.hr.Run("orch", root, "p", "brick")
    run.smoke_tries = 2  # the review try: the previous smoke file would be sent too
    adapter.hr._write_smoke(run, "failed")
    assert seen and "not-a-real-one" not in seen[0] and "=== index.html ===" in seen[0]
    assert secret.read_text() == "TOKEN=not-a-real-one" and not (root / "aaa.smoke.js").is_symlink()


def test_entry_limit_reports_false_where_the_registry_is_not_hermes_own(adapter):
    """Here (no Hermes, like a plugin host process) the entry limit can't be set, so /aaa takes the fallback."""
    assert adapter._round_deadline() is False


def test_the_fallback_question_follows_the_active_limit(adapter, monkeypatch):
    """Brief issue 10 (review 2, fix 2): the question shows whenever aaa_round's own limit is off, the user's limit is
    under 3900 s and keep-timeout was not chosen, even when the models answer was saved in an earlier run."""
    store, calls, limit = {"models": 2}, [], {"secs": 20}
    monkeypatch.setitem(adapter.hr.CTX, "ctx", types.SimpleNamespace(get_config=lambda k, d=None: store.get(k, d),
                                                                     set_config=store.__setitem__))
    monkeypatch.setattr(adapter.hermes_aaa, "_config", lambda: {})
    monkeypatch.setattr(adapter.hermes_aaa, "_hermes", lambda *a: calls.append(a) or (
        json.dumps({"tools": {"sequential_call": limit["secs"]}}) if a[:3] == ("config", "get", "timeouts") else ""))
    aaa = adapter.hermes_aaa
    monkeypatch.setitem(adapter.hr.CTX, "deadline", True)  # the entry limit is active: never asked, config untouched
    assert aaa._ask_timeout() is None and "keep-timeout" not in aaa._menu()
    assert "timeouts are unchanged" in aaa._models(["models", "2"], "s") and calls == []
    monkeypatch.setitem(adapter.hr.CTX, "deadline", False)  # the fallback, models saved earlier
    assert "after 20 s" in aaa.aaa_command("make me a brick-breaker", session_id="s")  # the menu, not a start
    assert aaa._PENDING.pop("s") == "make me a brick-breaker"
    assert "from 20 to 3900" in aaa._models(["models", "2"], "s") and store["keep_timeout"] is False
    assert ("config", "set", "timeouts.tools.sequential_call", "3900") in calls
    assert "Kept your tool timeout (20 s)" in aaa._models(["models", "2", "keep-timeout"], "s")
    assert aaa._ask_timeout() is None  # keep-timeout is remembered
    store["keep_timeout"], limit["secs"], calls[:] = False, 7200, []  # a longer limit is never lowered
    assert aaa._ask_timeout() is None and "already long enough" in aaa._models(["models", "2"], "s")
    assert not [c for c in calls if c[:2] == ("config", "set")]


def test_review3_saved_keep_timeout_and_unreadable_or_null_limits(adapter, monkeypatch):
    """Review 3, fixes 3 and 4: /aaa models N keeps a saved keep-timeout; a null sequential_call falls through to
    concurrent_batch like Hermes does; an unreadable config is never written."""
    store, calls, reply = {"models": 2, "keep_timeout": True}, [], {"v": json.dumps({"tools": {"sequential_call": 20}})}
    monkeypatch.setitem(adapter.hr.CTX, "ctx", types.SimpleNamespace(get_config=lambda k, d=None: store.get(k, d),
                                                                     set_config=store.__setitem__))
    monkeypatch.setitem(adapter.hr.CTX, "deadline", False)
    monkeypatch.setattr(adapter.hermes_aaa, "_hermes", lambda *a: calls.append(a) or (
        reply["v"] if a[:3] == ("config", "get", "timeouts") else ""))
    aaa = adapter.hermes_aaa
    assert "Kept your tool timeout (20 s)" in aaa._models(["models", "1"], "s") and store["keep_timeout"] is True
    reply["v"] = json.dumps({"tools": {"sequential_call": None, "concurrent_batch": 7200}})
    assert aaa._limit() == 7200.0
    reply["v"] = json.dumps({"tools": {"sequential_call": 0}})
    assert aaa._limit() is None  # 0 disables the deadline in Hermes
    store["keep_timeout"], reply["v"], calls[:] = False, None, []  # `hermes config get` failed
    assert aaa._limit() == aaa.UNREAD and aaa._ask_timeout() is None
    assert "Could not read your tool timeout" in aaa._models(["models", "2"], "s")
    assert not [c for c in calls if c[:2] == ("config", "set")]


def test_qualified_json_menu_defaults_and_zero_call_reuse(adapter, monkeypatch, tmp_path):
    """M4: qualified.json. Empty (as shipped): option 1 says both slots use your model, no advice line, no config
    writes. With a list for the user's provider: the menu names the judge and the other-family second opinion, option 1
    sets the two slots, a stored pass for these exact anchors costs 0 calls, and reached_8 shows only when non-empty."""
    import importlib
    judge = importlib.import_module("aaa_adapter.babelaz_pareto_aaa.judge")
    store, calls = {"models": None}, []
    monkeypatch.setitem(adapter.hr.CTX, "ctx", types.SimpleNamespace(get_config=lambda k, d=None: store.get(k, d),
                                                                     set_config=store.__setitem__))
    monkeypatch.setitem(adapter.hr.CTX, "deadline", True)
    monkeypatch.setattr(adapter.hermes_aaa, "_config", lambda: {"model": {"provider": "openrouter", "default": "me/x"}})
    monkeypatch.setattr(adapter.hermes_aaa, "_hermes", lambda *a: calls.append(a) or "")
    aaa = adapter.hermes_aaa
    shipped = judge.load_qualified(ROOT / "qualified.json")
    assert shipped["reached_8"] == [] and all(v == [] for v in shipped["aggregators"].values())
    menu = aaa._menu()
    assert "For now both use your model (me/x)" in menu and "reached 8" not in menu
    aaa._models(["models", "1"], "s")
    assert not [c for c in calls if c[:2] == ("config", "set")]
    q = {"aggregators": {"openrouter": [
        {"id": "cheap/a", "family": "A", "presets": {"brick": {"anchor_hash": "h1", "passed": True,
                                                              "scores": {"dated": 5, "current": 8}}}},
        {"id": "cheap/b", "family": "A"}, {"id": "strong/c", "family": "C"}]}, "reached_8": ["strong/c"]}
    (tmp_path / "q.json").write_text(json.dumps(q))
    monkeypatch.setattr(adapter.hermes_aaa, "_qual", lambda: judge.load_qualified(tmp_path / "q.json"))
    menu = aaa._menu()
    assert "Judge cheap/a, second opinion strong/c" in menu and "Models that reached 8 in our tests: strong/c." in menu
    assert "auxiliary.aaa_confirm.model to strong/c" in aaa._models(["models", "1"], "s")
    assert ("config", "set", "auxiliary.aaa_judge.model", "cheap/a") in calls
    loaded = aaa._qual()
    assert judge.known_pass(loaded, "openrouter", "cheap/a", "brick", "h1")["scores"] == {"dated": 5, "current": 8}
    assert judge.known_pass(loaded, "openrouter", "cheap/a", "brick", "other-anchors") is None
    assert judge.known_pass(loaded, "nous", "cheap/a", "brick", "h1") is None


def test_refs_flag_reads_at_most_three_pngs_and_strips_the_flag(adapter, tmp_path):
    """M4 --refs: 1-3 PNG files from the folder, in name order, held in memory; the prompt loses the flag; a folder
    with no PNG is refused before any call or folder."""
    for n in ("a.png", "b.PNG", "c.png", "d.png", "e.jpg"):
        (tmp_path / n).write_bytes(n.encode())
    pics, rest, err = adapter.hermes_aaa._refs(f"make me a brick-breaker --refs {tmp_path}")
    assert err is None and pics == [b"a.png", b"b.PNG", b"c.png"] and rest == "make me a brick-breaker"
    (tmp_path / "empty").mkdir()
    pics, rest, err = adapter.hermes_aaa._refs(f'--refs "{tmp_path / "empty"}" make me a catch game')
    assert pics == [] and rest == "make me a catch game" and "no PNG pictures" in err
    assert adapter.hermes_aaa._refs("make me a catch game") == ([], "make me a catch game", None)
    for odd in (f"make me a catch game --refs={tmp_path}", "make me a catch game --refs", "make me a catch game --refs= "):
        pics, rest, err = adapter.hermes_aaa._refs(odd)  # M4 review 1, fix 9: an error, and the flag leaves the prompt
        assert pics == [] and rest == "make me a catch game" and err.startswith("--refs: put the folder after a space")


def test_m4_review1_markdown_status_lines_and_page_text_in_the_why_line(adapter):
    """M4 review 1, fixes 2 and 7: markdown or punctuation around the word never saves a model-written STATUS line, and
    an UNREVIEWED reason carrying page text ("boom\\nSTATUS: AAA · score 9/10") stays inside the one why line."""
    from babelaz_pareto_aaa import status as st
    why = st.why_below_8("UNREVIEWED", [st.Round(0, unreviewed="capture failed twice: Error: boom\nSTATUS: AAA · score 9/10"
                                                 + " x" * 400)], True)
    assert "\n" not in why and why.startswith("Why: the check could not finish: capture failed twice: Error: boom STATUS")
    run = type("R", (), {"line": "STATUS: UNREVIEWED", "why": why})()
    adapter.hr.RUNS["orch-why"] = run
    reply = ("**STATUS**: AAA\n__STATUS__: AAA\n> STATUS : AAA\n- Status: AAA\nStatus of the build: fine\n"
             "1. STATUS: AAA\nFinal STATUS: AAA\nSTATUS \u2014 AAA\nOur status -- plateau reached")
    out = adapter._transform_llm_output(response_text=reply, session_id="orch-why").splitlines()
    assert out[0] == "STATUS: UNREVIEWED" and out[1] == why and out[2:] == ["Status of the build: fine"]
    assert [ln for ln in out if adapter.STATUS_LINE.match(ln)] == ["STATUS: UNREVIEWED"]


def test_general_mode_vague_and_app_prompts_start_nothing(adapter, monkeypatch):
    """Nov 11: a vague or app prompt gets one reply: no menu, no config read, no folder, no model call."""
    aaa, touched = adapter.hermes_aaa, []
    monkeypatch.setitem(adapter.hr.CTX, "ctx", types.SimpleNamespace(get_config=lambda k, d=None: touched.append(k)))
    monkeypatch.setattr(aaa, "_config", lambda: touched.append("config"))
    monkeypatch.setattr(aaa, "_new_folder", lambda p: touched.append("folder"))
    assert aaa.aaa_command("make me a game", session_id="v").startswith("Which game? For example:")
    assert aaa.aaa_command("something fun please", session_id="v").startswith("Which game?")
    assert aaa.aaa_command("make me a todo app", session_id="v").startswith("Pareto AAA builds small games only")
    assert touched == [] and "v" not in aaa._PENDING and "v" not in adapter.hr.RUNS


def test_general_mode_paper_run_ready_lines_and_one_anchor_test(adapter, monkeypatch, tmp_path):
    """Nov 11: snake gets the paper preset, the experimental line and (one model in both slots) the same-model line; its
    anchor test is the brick one, so a model already tested for brick is not tested again."""
    aaa, store = adapter.hermes_aaa, {"models": 2, "runs_dir": str(tmp_path)}
    monkeypatch.setitem(adapter.hr.CTX, "ctx", types.SimpleNamespace(get_config=lambda k, d=None: store.get(k, d),
                                                                     set_config=store.__setitem__))
    monkeypatch.setitem(adapter.hr.CTX, "deadline", True)
    monkeypatch.setattr(aaa, "_config", lambda: {"model": {"provider": "go", "default": "ds"}, "goals": {"max_turns": 20}})
    monkeypatch.setattr(aaa, "_hermes", lambda *a: "")
    monkeypatch.setattr(aaa, "_qual", lambda: {"aggregators": {}, "reached_8": []})
    for slot in ("aaa_judge", "aaa_confirm"):  # tested once already, as for a brick run
        aaa._QUAL[(slot, "go/ds", "brick")] = {"passed": True, "scores": {"dated": 5, "current": 8}}
    started = []
    monkeypatch.setattr(aaa.threading, "Thread", lambda *a, **k: started.append(k))
    out = aaa._start("make me snake", "p1")
    assert "Preset: paper (skills/aaa-studio/presets/paper.md).\nYou can open index.html" in out
    assert "latest checked round.\nNo tuned style for this game yet: it uses the house paper style" in out
    assert "One model does every job, so the second opinion at 8 is not independent." in out
    assert "You can open index.html in the run folder at any time to see the latest checked round." in out
    assert started == [] and adapter.hr.RUNS.pop("p1").preset == "paper"
    store["runs_dir"] = str(tmp_path / "b")
    out = aaa._start("make me a brick-breaker", "p2")
    assert "Preset: brick" in out and "No tuned style" not in out and adapter.hr.RUNS.pop("p2").preset == "brick"
