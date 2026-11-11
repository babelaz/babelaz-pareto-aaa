"""The check (brief section 6): integrity, serve and capture, gates, judge, second opinion, STATUS.

Portable core: takes call_model functions, never imports Hermes. Truth lives in RunState (memory); disk gets the log only.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import capture, evidence, gates, integrity, judge, pick
from . import status as st
from .integrity import sha256

SMOKE = "aaa.smoke.js"
PRESETS = ("brick", "catch")


class NotQualified(Exception):
    """Raised before round 0 when a judge or second-opinion model fails the anchor test (brief section 7)."""


class CaptureFailed(Exception):
    """A capture failed twice in a row (brief: UNREVIEWED)."""


@dataclass
class RunState:
    base_sha: str  # last accepted commit, held in memory
    plugin_dir: str | None = None
    plugin_hashes: dict | None = None  # taken at the start of the run; re-checked each round
    locked: set | None = None  # file list, locked in the first round that passes the smoke lock
    engines: set | None = None  # locked paths holding a pinned engine file at the lock (review 3, fix 5)
    refs: list = field(default_factory=list)  # the user's --refs pictures (PNG bytes), judged every round; never on disk
    smoke_bytes: bytes | None = None  # stored only after the smoke lock passes; restored from here
    smoke_sha: str | None = None
    rounds: list = field(default_factory=list)
    cache: dict = field(default_factory=dict)  # tree hash + anchor hash -> judge outcome


def anchors(preset: str, assets=None):
    """The preset's three anchors in judge order: dated (6), clean (7), current (8), plus one hash of all three.
    paper has none of its own: it uses the brick anchors as quality levels (pick.ANCHOR_SET)."""
    d = Path(assets or capture.ASSETS) / "anchors" / pick.anchor_set(preset)
    dated, clean, current = ((d / f"{name}.png").read_bytes() for name in ("dated", "clean", "current"))
    return dated, clean, current, sha256(dated + clean + current)


def ensure_qualified(preset: str, call_model, role: str) -> dict:
    """Before round 0: the model scores the dated and current anchors, and the dated build must come out at least 2 lower.
    The clean anchor (7) is not part of this test. Not cached yet: the cache belongs with qualified.json (M4)."""
    dated, _clean, current, _ah = anchors(preset)
    res = judge.qualify(call_model, dated, current)
    if not res["passed"]:
        raise NotQualified(f"{role} model: {res['reason']} (scores {res['scores']})")
    return res


def judge_test(job, preset, todo, qual, caller):
    """The anchor test for each (slot, role, key) in todo, run in its own thread; job carries msg, err and done. One test per model: the same model in both slots is tested once."""
    try:
        for n, (slot, role, key) in enumerate(todo, 1):
            job["msg"] = f"testing the {role} model ({n} of {len(todo)})"
            same = next((v for k, v in qual.items() if k[1:] == key[1:] and v), None)
            try:
                qual[key] = same or ensure_qualified(preset, caller(slot), role)
            except NotQualified as err:
                if judge.rate_wait(err) is not None:  # not a verdict on the model: the provider refused the calls
                    job["err"] = (f"Pareto AAA can't start yet: your provider is rate-limiting or timing out the {role} model, even "
                                  f"after two waits. Nothing was judged. Try again in a few minutes, or set "
                                  f"auxiliary.{slot} to another model. ({str(err)[:200]})")
                else:
                    job["err"] = (f"Pareto AAA can't start: the {role} model failed the anchor test ({err}). It must "
                                  f"score the dated anchor at least 2 lower than the current one. Set auxiliary.{slot} "
                                  "to another model.")
                return
    except BaseException as err:
        job["err"] = f"Pareto AAA could not start: {type(err).__name__}: {err}"
    finally:
        job["done"] = True


def _ask_with_retry(call, images, text, known, prefix):
    """A failed or unparseable call is retried once; a second failure raises.
    A score below 8 with no valid fix gets one retry too; the first reply is kept if the retry fails."""
    out = None
    last = None
    for attempt in range(2):
        try:
            got = judge.ask(call, images, text, known, prefix)
        except Exception as err:
            last = err
            continue
        if out is not None:
            return got
        out = got
        if got["score"] >= st.AAA_BAR or got["fixes"]:
            return got
    if out is None:
        raise last
    return out


def run_round(root, out_dir, preset: str, state: RunState, call_judge, call_confirm, *, worker_runs: int = 0) -> dict:
    """One check. Returns {"round": Round, "status": str, "score": int|None, "line": str}. Any crash is UNREVIEWED.
    The anchor test (ensure_qualified) runs before round 0, in the caller."""
    root, out_dir = Path(root), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rnd = st.Round(n=len(state.rounds))
    state._last_gates, state._last_measures, state._last_usage = [], {}, None
    state._last_retry = None
    try:
        _round(root, out_dir, preset, state, rnd, call_judge, call_confirm)
    except Exception as err:  # brief: any crash is UNREVIEWED
        rnd.unreviewed = f"crash: {type(err).__name__}: {err}"
    state.rounds.append(rnd)
    stat = st.status(state.rounds, worker_runs)
    line = st.status_line(stat, rnd.score)
    log = {
        "round": rnd.n, "preset": preset, "status": stat, "line": line, "score": rnd.score,
        "judge": rnd.judge, "confirm": rnd.confirm, "models": rnd.models, "cached": rnd.cached,
        "gate_fails": rnd.gate_fails, "fixes": rnd.fixes, "keep": rnd.keep, "unreviewed": rnd.unreviewed,
        "smoke_locked": state.smoke_bytes is not None, "fps_retry": state._last_retry,
        "usage": state._last_usage, "measures": state._last_measures, "gates": state._last_gates,
    }
    (out_dir / "round.json").write_text(json.dumps(log, indent=2, default=str), encoding="utf-8")
    return {"round": rnd, "status": stat, "score": rnd.score, "line": line}


def _capture(root, out_dir, smoke_src: str, lock: bool) -> dict:
    """One capture, retried once. A second failure raises CaptureFailed."""
    err = None
    for _ in range(2):
        try:
            return capture.capture(root, out_dir, smoke_src, lock=lock)
        except Exception as e:
            err = e
    raise CaptureFailed(str(err))


def _measure(root, out_dir, smoke_src: str, lock: bool, extra: dict):
    """Capture, then the gates. When FPS is the only failing gate, measure once more and keep the run with the better
    FPS (batch 2, item 4). Both runs' FPS numbers are returned for the evidence. Returns (cap, m, results, retry)."""
    cap = _capture(root, out_dir / "shots", smoke_src, lock)
    m = dict(cap["measures"])
    m.update(extra)
    results = gates.evaluate(m)
    retry = None
    if gates.fps_only_failing(results):
        again = _capture(root, out_dir / "shots_fps_retry", smoke_src, False)
        m2 = dict(again["measures"])
        m2.update(extra)
        if lock:
            m2["lock"] = m["lock"]  # the lock result belongs to the first run
        retry = {"first": {"avg": m["fps_avg"], "low": m["fps_low"]},
                 "second": {"avg": m2["fps_avg"], "low": m2["fps_low"]}}
        if gates.fps_margin(m2) > gates.fps_margin(m):
            cap, m, m2 = again, m2, m
            retry["kept"] = "second"
        else:
            retry["kept"] = "first"
        for k in ("page_errors", "console_errors"):  # errors seen only in the dropped run still count (review 2)
            m[k] = m[k] + [e for e in m2[k] if e not in m[k]]
        results = gates.evaluate(m)
    return cap, m, results, retry


def _round(root, out_dir, preset, state, rnd, call_judge, call_confirm):
    locked_now = state.smoke_bytes is not None  # the smoke lock passed in an earlier round
    if state.plugin_dir and state.plugin_hashes is not None:
        now = integrity.file_hashes(state.plugin_dir, list(state.plugin_hashes))
        if not integrity.plugin_hashes_match(state.plugin_hashes, now):
            rnd.unreviewed = "plugin hash mismatch"
            return
    if not integrity.commit_exists(root, state.base_sha):
        rnd.unreviewed = "last accepted commit is missing"
        return

    # Step 0: integrity. Restores happen before capture, so the judge never sees a worker's out-of-scope edit.
    # Before the smoke lock passes, nothing is restored and nothing is locked (brief S0: not locked yet).
    smoke_path = root / SMOKE
    hash_ok = True
    if locked_now and (smoke_path.is_symlink() or not smoke_path.is_file()
                       or sha256(smoke_path.read_bytes()) != state.smoke_sha):
        smoke_path.unlink(missing_ok=True)  # a planted link is removed, never written through (review 2)
        smoke_path.write_bytes(state.smoke_bytes)
        hash_ok = False
    links = integrity.links(root)
    for rel in links:  # removed before anything is served or read, in every round (review 1, fix 4)
        (root / rel).unlink()
    changed = integrity.changed_files(root, state.base_sha)
    bad = integrity.scope_violations(changed, state.locked)
    for rel in bad:
        integrity.restore(root, state.base_sha, rel)
    notes = (["restored: " + ", ".join(bad)] if bad else []) + (["links removed: " + ", ".join(links)] if links else [])

    if not smoke_path.is_file():
        rnd.gate_fails = [st.gate_fix(1, "aaa.smoke.js is missing")]
        return
    smoke_src = smoke_path.read_bytes().decode("utf-8")

    # Steps 1 and 2: capture, then gates. Any failure becomes a G fix, and the round is not judged.
    lock = not locked_now  # re-run the strength lock every round until it passes
    extra = dict(hash_ok=hash_ok, scope_ok=not notes, deps=integrity.external_refs(root, state.engines),
                 scope_detail="; ".join(notes) or "clean")
    if not hash_ok:
        extra["smoke_detail"] = "aaa.smoke.js changed since the smoke lock; restored from memory"
    try:
        cap, m, results, retry = _measure(root, out_dir, smoke_src, lock, extra)
    except CaptureFailed as e:
        rnd.unreviewed = f"capture failed twice: {e}"
        return
    state._last_retry = retry
    failed = [g for g in results if not g["ok"]]
    rnd.gate_fails = [st.gate_fix(i, f"{g['name']}: {g['detail']}") for i, g in enumerate(failed, 1)]
    if lock:
        # Brief section 6: any failed round-0 check is S0 and no lock. Stored and locked only when the strength lock
        # passes, the smoke passes on the game, and every state check passes (hooks, order, 60 s, 1 s, shots differ).
        if m["lock"]["ok"] and m.get("smoke_ok") and gates.state_checks_ok(m):
            state.smoke_bytes = smoke_path.read_bytes()
            state.smoke_sha = sha256(state.smoke_bytes)
            state.locked = set(integrity.tree_files(root))
            state.engines = integrity.engine_paths(root, state.locked)
        else:
            rnd.gate_fails.append(st.smoke_lock_fix())
        results.append({"name": "strength lock: smoke fails on a blank page, a frozen loop and blocked input (S0)",
                        "ok": m["lock"]["ok"], "detail": m["lock"]["detail"]})
    state._last_gates = results
    # Numbers only. Worker-written text (DOM ids, console or page errors, smoke output) never reaches the judge.
    state._last_measures = {
        "errors": len(m.get("page_errors", [])) + len(m.get("console_errors", [])),
        "fps_avg": round(m.get("fps_avg", 0), 1),
        "fps_low_1pct": round(m.get("fps_low", 0), 1),
        "longtasks": m.get("longtasks"),
        "sound_before": round(m.get("sound_before", 0), 3),
        "sound_after": round(m.get("sound_after", 0), 3),
        "axe_serious": m.get("axe_serious"),
        "layout_issues": len(m.get("layout", [])),
    }
    dated, clean, current, ah = anchors(preset)
    ids = [eid for eid, _, _ in cap["items"]]
    images = [dated, clean, current] + list(state.refs) + [png for _, _, png in cap["items"]]
    ah = sha256(ah.encode() + b"".join(state.refs)) if state.refs else ah  # the cache key covers the refs too

    # Step 3: judge, then second opinion only at 8 or more.
    if not rnd.gated:
        key = (integrity.tree_hash(root), ah)
        if key in state.cache:
            outcome = state.cache[key]
            rnd.cached = True
        else:
            outcome = {"judge": None, "confirm": None}
            text = judge.judge_text(ids, state._last_measures, len(state.refs), preset)
            try:
                outcome["judge"] = _ask_with_retry(call_judge, images, text, ids, "J")
            except Exception as e:
                rnd.unreviewed = f"judge failed twice: {e}"
                return
            if outcome["judge"]["score"] >= st.AAA_BAR:
                try:
                    outcome["confirm"] = _ask_with_retry(call_confirm, images, text, ids, "C")
                except Exception as e:
                    rnd.unreviewed = f"second opinion failed twice: {e}"
                    return
            state.cache[key] = outcome
        j, c = outcome["judge"], outcome["confirm"]
        rnd.judge = j["score"]
        rnd.confirm = c["score"] if c else None
        j_fixes = st.top_complaint_fix(j["score"], j["fixes"], j["complaint"])
        rnd.fixes = st.pick_fixes(j_fixes, c["fixes"] if c else [], rnd.confirm_disagreed)
        rnd.keep = c["keep"] if (c and rnd.confirm_disagreed) else j["keep"]
        rnd.models = {"judge": j["model"], "confirm": c["model"] if c else None}
        state._last_usage = {"judge": j["usage"], "confirm": c["usage"] if c else None}

    # Step 4: evidence PNG (always, gated rounds too).
    headline = f"Round {rnd.n} · {preset}"
    evidence.render(cap["items"], gates.summary(results), headline, out_dir / "evidence.png")

