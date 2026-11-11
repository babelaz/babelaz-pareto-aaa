"""HERMES ADAPTER: run state in memory, and aaa_round (worker, done-list, smoke writer, check, commit, keep-best).

Truth lives in this gateway process's memory (RUNS); disk gets the log only (.aaa/run.log). The core check is called
with call_model functions built here on the plugin's own task slots (ctx.llm, no operator opt-in needed).
"""

import json
import os
import shutil
import threading
import time
from pathlib import Path

from . import hermes_worker as hw
from .babelaz_pareto_aaa import brief as bf
from .babelaz_pareto_aaa import integrity, judge, pick, ship
from .babelaz_pareto_aaa import status as st

HERE = Path(__file__).resolve().parent
STUDIO = HERE / "skills" / "aaa-studio"
SOURCE_SUFFIXES, SOURCE_LIMIT = (".html", ".js", ".css"), 90_000
CTX, RUNS = {}, {}  # RUNS: orchestrator session_id -> Run
STOPPED = {"sessions": set(), "dirs": set()}  # stopped workers: their tools stay blocked (Brief issue 11)

class Run:
    def __init__(self, sid, root, prompt, preset):
        self.sid, self.root, self.prompt, self.preset = sid, root, prompt, preset
        self.status, self.line, self.report, self.why = "CONTINUE", "STATUS: CONTINUE", "", ""
        self.state = self.worker = self.smoke_failure = self.base = self.config = self.hashes = None
        self.worker_runs = self.smoke_tries = self.calls = 0
        self.mandatory, self.offered, self.keep, self.log = [], [], [], []
        self.commits, self.proofs = {}, {}
        self.taste, self.refs = "", []  # frozen at /aaa from --refs (art direction text; pictures in memory only)
        self.lock, self.job = threading.Lock(), None  # job: a judge test still running at /aaa


def _cwd() -> str:
    try:
        cwd = CTX["ctx"].session_cwd()
        return os.path.realpath(cwd) if cwd else ""
    except Exception:
        return ""


def _log(run, text: str) -> None:
    """Append to the run's log in memory, then rewrite .aaa/run.log (unlinked first: never written through a link)."""
    run.log.append(time.strftime("%H:%M:%S ") + text)
    try:  # disk is only a log: a failed write never breaks a round
        d = _real_dir(run.root / ".aaa")
        (d / "run.log").unlink(missing_ok=True)
        (d / "run.log").write_text("\n".join(run.log) + "\n", encoding="utf-8")
    except OSError:
        pass


def _real_dir(d: Path, fresh=False) -> Path:
    """A real directory inside the run folder: a planted link or file is removed first; fresh=True empties it."""
    if d.is_symlink() or (d.exists() and not d.is_dir()):
        d.unlink()
    elif fresh and d.exists():
        shutil.rmtree(d)
    d.mkdir(exist_ok=True)
    return d


def _worker_of(sid):
    """The run whose current worker is this session: by id (stderr line, or an earlier hook), else by run folder."""
    if not sid or sid in RUNS or sid in STOPPED["sessions"]:
        return None
    for run in list(RUNS.values()):
        w = run.worker
        if not w:
            continue
        w["session"] = w.get("session") or hw.session_line(w["err"])  # printed before the turn starts
        if w["session"] == sid:
            return run
        if not w["session"] and _cwd() == str(run.root) and str(run.root) not in STOPPED["dirs"]:
            w["session"] = sid
            return run
    return None


def _missing(run) -> list:
    try:
        done = json.loads((run.root / "aaa-done.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        done = {}
    done = done if isinstance(done, dict) else {}
    out = []
    for i in run.worker["ids"]:
        v = str(done.get(i, ""))
        if not (v.startswith("done") or (v.startswith("blocked") and i[0] not in "GS")):
            out.append(i)
    return out


# ---------------------------------------------------------------- model calls (task slots)
def _caller(slot, instructions="Follow the text block exactly. Reply with one JSON object only."):
    """call_model(images, text) -> (text, usage, model_id) through the plugin's own task slot (no opt-in needed)."""
    def call(images, text):
        blocks = [{"type": "text", "text": text}] + [{"type": "image", "data": p, "mime_type": "image/png"}
                                                     for p in images]
        kw = dict(instructions=instructions, input=blocks, task=slot, max_tokens=32000)  # room for xhigh/max thinking
        r = judge.complete_retry(CTX["ctx"].llm.complete_structured, kw)  # temperature 0; waits out a 429
        u = r.usage
        usage = {"input": u.input_tokens, "output": u.output_tokens, "total": u.total_tokens} if u else None
        return r.text or "", usage, f"{r.provider}/{r.model}"
    return call


def _write_smoke(run, failure):
    """Round 0, and each round while the smoke isn't locked: one tool-free aaa_smoke call writes aaa.smoke.js. After
    two tries the worker writes it and this call reviews it (brief section 4, S0). A second failed call raises."""
    smoke = run.root / "aaa.smoke.js"
    previous = smoke.read_text(encoding="utf-8", errors="replace") if smoke.is_file() and not smoke.is_symlink() else None
    sources, size = {}, 0
    for rel in integrity.tree_files(run.root):
        if (rel.endswith(SOURCE_SUFFIXES) and rel != "aaa.smoke.js" and size < SOURCE_LIMIT
                and not (run.root / rel).is_symlink()):  # never send a file from outside the run folder
            sources[rel] = (run.root / rel).read_text(encoding="utf-8", errors="replace")[:SOURCE_LIMIT - size]
            size += len(sources[rel])
    review = run.smoke_tries >= 2
    prompt = bf.smoke_prompt(sources, failure, previous if (failure or review) else None)
    if review:
        prompt = "Review the worker's aaa.smoke.js below and return it corrected.\n" + prompt
    call, err = _caller("aaa_smoke", "Follow the text block exactly. Reply with the JavaScript file only."), None
    for _attempt in range(2):
        try:
            text, usage, model = call([], prompt)
            break
        except Exception as e:
            err = e
    else:
        raise RuntimeError(f"smoke writer failed twice: {err}")
    run.smoke_tries += 1
    smoke.unlink(missing_ok=True)
    smoke.write_text(bf.smoke_from_reply(text), encoding="utf-8")
    _log(run, f"smoke: written by aaa_smoke ({model}, try {run.smoke_tries}{', review' if review else ''}, "
              f"tokens {usage and usage.get('total')})")


# ---------------------------------------------------------------- aaa_round
def _check_keep(run, keep) -> str | None:
    offered = [f["id"] for f in run.offered]
    if not isinstance(keep, list) or not all(isinstance(k, str) for k in keep) or len(set(keep)) != len(keep):
        return "keep must be a list of distinct fix IDs."
    unknown = [k for k in keep if k not in offered]
    if unknown:
        return f"Unknown IDs {unknown}. You can only keep offered fixes: {offered or 'none'}."
    if not keep and offered and not run.mandatory:
        return f"Keep at least one offered fix ({offered}): mandatory fixes don't exist this round."
    return None


def aaa_round(args, session_id=None, **_):
    run = RUNS.get(session_id)
    if run is None:
        return "No Pareto AAA run in this session. Start one with /aaa <prompt>."
    seen = run.calls
    with run.lock:  # a call cut by Hermes's tool deadline keeps running; a second call waits for it
        if run.calls != seen:
            return run.report
        try:
            run.report = _round(run, args if isinstance(args, dict) else {})
        except BaseException as err:  # brief: any crash is UNREVIEWED
            run.status, run.line = "UNREVIEWED", "STATUS: UNREVIEWED"
            run.report = f"{run.line}\nCrash: {type(err).__name__}: {str(err)[:300]}. This STATUS is final."
            _log(run, f"UNREVIEWED: crash: {type(err).__name__}: {err}")
        run.calls += 1
        if run.status in bf.FINAL and not run.why:  # design decision: below 8, say why and the next step
            try:
                rounds = run.state.rounds if run.state else []
                run.why = st.why_below_8(run.status, rounds, bool(run.state and run.state.smoke_bytes))
            except Exception as err:
                run.why = f"Why: unknown ({type(err).__name__})."
            if run.why:
                _log(run, run.why)
                run.report += "\n" + run.why
        return run.report


def _interrupted() -> bool:
    """The user's /stop or Ctrl+C on this call (tools/interrupt.py:57); the call runs on, so aaa_round polls this."""
    try:
        from tools.interrupt import is_interrupted
        return bool(is_interrupted())
    except Exception:
        return False


def _round(run, args):
    from .babelaz_pareto_aaa import check  # imports Playwright: only when a round runs
    if run.status in bf.FINAL:
        return run.report
    job, run.job = run.job, None
    if job and (job["thread"].join() or job.get("err")):  # the judge test /aaa left running (slow model): wait for it
        run.status, run.line = "UNREVIEWED", "STATUS: UNREVIEWED"
        _log(run, f"UNREVIEWED: {job['err']}")
        return f"{run.line}\n{job['err']} This STATUS is final."
    n = len(run.state.rounds) if run.state else 0
    if n == 0:
        if not args.get("build"):
            return "Call aaa_round with build=true first (round 0)."
        fixes = [bf.BUILD]
    else:
        keep = args.get("keep") or []
        err = _check_keep(run, keep)
        if err:
            return err + " No round ran."
        fixes = run.mandatory + [next(f for f in run.offered if f["id"] == k) for k in keep]
        if "S0" in [f["id"] for f in run.mandatory] and run.smoke_tries >= 2:
            fixes = [dict(f, text=f["text"] + " Also write aaa.smoke.js yourself. " + bf.SMOKE_CONTRACT)
                     if f["id"] == "S0" else f for f in fixes]
        run.worker_runs += 1  # every worker run counts toward CAP
    _log(run, f"round {n}: start, fixes {[f['id'] for f in fixes]}")
    rd = _real_dir(_real_dir(run.root / ".aaa") / f"round-{n}", fresh=True)
    (run.root / "aaa-done.json").unlink(missing_ok=True)
    art = (STUDIO / "presets" / f"{run.preset}.md").read_text(encoding="utf-8")
    text = bf.worker_brief(n=n, prompt=run.prompt, preset=run.preset, art=art, taste=run.taste, fixes=fixes, keep=run.keep,
                           locked=run.state.locked if run.state else None, studio=str(STUDIO))
    (rd / "brief.md").write_text(text, encoding="utf-8")
    model, provider = CTX["ctx"].get_config("worker_model"), CTX["ctx"].get_config("worker_provider")
    run.worker = {"ids": [f["id"] for f in fixes], "err": rd / "worker.err", "session": None}

    def stop():
        run.worker["session"] = run.worker["session"] or hw.session_line(rd / "worker.err")
        if run.worker["session"]:
            STOPPED["sessions"].add(run.worker["session"])
        else:  # no id yet: block by folder (the next worker is found by its own Session line first, review 1 fix 6)
            STOPPED["dirs"].add(str(run.root))

    try:
        w = hw.run(hw.command(rd / "brief.md", run.root, model, provider), run.root, rd,
                   on_session=lambda s: run.worker.update(session=s), on_stop=stop, interrupted=_interrupted)
        missing = _missing(run)
    finally:
        killed = hw.sweep(run.worker["session"])
        sess, run.worker = run.worker["session"], None
    _log(run, f"round {n}: worker exit {w['exit']} in {w['secs']} s, stopped={w['stopped']}, session {sess}, "
              f"tokens {w['tokens']}, swept {len(killed)}, done-list missing {missing}")
    if w["error"] and w["secs"] > 120:  # it ran, then failed (e.g. context full): check its files (first real run)
        _log(run, f"round {n}: worker error after {w['secs']} s; its files are checked: {w['error']}")
    elif w["error"]:
        run.status, run.line = "UNREVIEWED", "STATUS: UNREVIEWED"
        _log(run, f"UNREVIEWED: the worker could not start: {w['error']}")
        return f"{run.line}\nThe worker could not start: {w['error']}. This STATUS is final."
    (run.root / "aaa-done.json").unlink(missing_ok=True)  # a claim only, never part of the slice
    if w["stopped"] == "interrupt":  # brief section 4: the same reset as a stall, but no check, and it doesn't count
        ship.guard(run.root, run.config)
        ship.reset(run.root, run.state.base_sha if run.state else run.base)
        if n:
            run.worker_runs -= 1
        _log(run, f"round {n}: user interrupt: worker stopped, reset to the last accepted commit; not checked, not counted")
        return (f"{run.line}\nRound {n} was interrupted by the user: the folder is back at the last accepted round. "
                "Nothing was checked or counted. Call aaa_round again to retry, or stop here.")
    note = ship.guard(run.root, run.config)
    if note:
        _log(run, f"round {n}: tamper: {note}")
    if w["stopped"]:  # first real run: a worker at the ceiling had a playable game: check it as it is, don't reset
        _log(run, f"round {n}: {w['stopped']}: worker stopped; its files are checked; the round counts")
    if run.state is None:
        run.state = check.RunState(base_sha=run.base, plugin_dir=str(HERE), plugin_hashes=run.hashes, refs=run.refs)
    if run.state.smoke_bytes is None:
        _write_smoke(run, run.smoke_failure)
    out = _real_dir(_real_dir(rd) / "check", fresh=True)
    res = check.run_round(run.root, out, run.preset, run.state, _caller("aaa_judge"), _caller("aaa_confirm"),
                          worker_runs=run.worker_runs)
    rnd = res["round"]
    failed = [f"{g['name']}: {g['detail']}" for g in run.state._last_gates if not g["ok"]]
    run.smoke_failure = "; ".join(failed)[:1500] if run.state.smoke_bytes is None and failed else None
    run.commits[n] = run.state.base_sha = ship.commit(run.root, f"Pareto AAA round {n}: {res['line']}")
    ev = out / "evidence.png"
    run.proofs[n] = ev.read_bytes() if ev.is_file() and not ev.is_symlink() else None
    run.status, run.line = res["status"], res["line"]
    run.mandatory, run.offered = list(rnd.gate_fails), list(rnd.fixes)
    if w["stopped"] and bf.BUILD in fixes:  # the reset removed the half-built slice: BUILD carries over (review 1, fix 6)
        run.mandatory = [bf.BUILD]
    if rnd.judge is not None:
        run.keep = list(rnd.keep)
    done = f"{len(fixes) - len(missing)}/{len(fixes)} done"
    _log(run, f"round {n}: {res['line']} · gates failed {[g['id'] for g in rnd.gate_fails]} · judge {rnd.judge} · "
              f"confirm {rnd.confirm} · models {rnd.models} · cached {rnd.cached} · unreviewed {rnd.unreviewed} · "
              f"fixes {done} · offered {[f['id'] for f in rnd.fixes]} · commit {run.commits[n][:10]}")
    finish = _finish(run) if run.status in bf.FINAL else ""
    return bf.round_report(line=run.line, n=n, runs=run.worker_runs, cap=st.CAP_WORKER_RUNS, done=done,
                           mandatory=run.mandatory, offered=run.offered, keep_count=len(run.keep),
                           final=run.status in bf.FINAL, finish=finish)


def _finish(run) -> str:
    """Final STATUS: check out the best round (keep-best) and copy aaa-proof.png into the project, from memory."""
    best, judged = st.best_round(run.state.rounds)
    head = ship.keep_best(run.root, run.commits[best.n], f"Pareto AAA: keep best round {best.n}")
    if run.proofs.get(best.n):
        head = ship.write_proof(run.root, run.proofs[best.n])
    models = best.models or {}
    who = pick.final_who(models, bool(best.confirm), run.status)  # a same-model line: after STATUS, never in it
    _log(run, f"final {run.status}: best round {best.n} (score {best.score}, judged {judged}), head {head[:10]}")
    return (f"Best round {best.n} (score {best.score if judged else 'none: never judged'}) is checked out in "
            f"{run.root}, with aaa-proof.png.{who}")
