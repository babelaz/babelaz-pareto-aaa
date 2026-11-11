"""HERMES ADAPTER: the /aaa command: the one-time model menu, preset, spend bounds and disclosure, a NEW folder plus
git, and the /goal line to paste (Hermes v0.22.7 refuses a plugin-started goal, Brief issue 7)."""

import json
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
import sys
import threading
import time
from pathlib import Path

from . import hermes_round as hr
from .babelaz_pareto_aaa import brief as bf
from .babelaz_pareto_aaa import integrity, judge, pick, ship
from .babelaz_pareto_aaa import status as st

_PENDING, _QUAL = {}, {}  # prompts waiting for the menu answer; anchor-test results
_JOBS = {}  # session id -> the running anchor test
WAIT = ("The one-time test of your judge model is still running (slow or rate-limited model). You can start now: "
        "the first round waits for it, and stops with a clear message if the model fails it.\n")
MENU = """Pareto AAA: which models should run? (asked once; change it any time with the same command)
 1  Pareto (recommended): rounds are judged by the cheapest model that passes the anchor test, and the second
    opinion at 8/10 comes from another family when your provider has one. {pick}
 2  One model: everything runs on your model ({main}), each judge call a fresh call.
 3  Choose: set auxiliary.aaa_judge, auxiliary.aaa_confirm and auxiliary.aaa_smoke in config.yaml yourself.
{advice}Bounds: at most 7 worker runs and 14 judge calls per run. Billed by: {provider}.{ask}
Reply: /aaa models 1|2|3{keep}"""
ROUND_SECS = 3900  # the tool limit a round needs (Brief issue 10)
# Only when aaa_round's own tool limit is not active and the user's is shorter (Brief issue 10, the fallback).
TIMEOUT_ASK = """
Long rounds: your Hermes stops a plugin tool after {limit:.0f} s, and one worker round can take longer. Your answer
also sets timeouts.tools.sequential_call to 3900 s (65 min) in config.yaml. To keep your setting, add keep-timeout
(longer rounds will then be cut)."""


def _hermes(*args):
    """Run the user's own Hermes CLI (same install and home). Returns its stdout, or None when it failed."""
    r = subprocess.run([sys.executable, "-m", "hermes_cli.main", *args], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=60)
    return r.stdout.strip() if r.returncode == 0 else None


def _config() -> dict:
    """The parts of the user's Hermes config this needs, through `hermes config get <key> --json` (Hermes masks
    secrets; nothing here is stored or logged)."""
    def get(key):
        try:
            return json.loads(_hermes("config", "get", key, "--json") or "{}")
        except (ValueError, subprocess.SubprocessError):
            return {}
    keys = ("model", "auxiliary", "goals")
    with ThreadPoolExecutor(len(keys)) as pool:  # each read starts the Hermes CLI (seconds on Windows): run together
        return dict(zip(keys, pool.map(get, keys)))


UNREAD = -1.0  # _limit() could not read the config: never ask, never set (review 3, fix 4)


def _num(v):
    """One timeouts value as Hermes reads it (agent/deadline.py:184-209): a number, else None (falls through)."""
    try:
        return None if v is None or isinstance(v, bool) or float(v) != float(v) else float(v)
    except (TypeError, ValueError):
        return None


def _limit() -> float | None:
    """The sequential tool deadline Hermes applies (agent/tool_executor.py:838-845): timeouts.tools.sequential_call,
    else timeouts.tools.concurrent_batch, else 420 s (agent/tool_executor.py:138). 0 or less: none (None).
    UNREAD when the config could not be read, so a longer limit is never lowered on a guess."""
    try:
        raw = _hermes("config", "get", "timeouts", "--json")
        tools = ((json.loads(raw) or {}).get("tools") or {}) if raw is not None else None
    except (ValueError, TypeError, AttributeError, subprocess.SubprocessError):
        tools = None
    if not isinstance(tools, dict):
        return UNREAD
    secs = next((v for v in (_num(tools.get("sequential_call")), _num(tools.get("concurrent_batch"))) if v is not None),
                420.0)
    return secs if secs > 0 else None


def _ask_timeout() -> float | None:
    """The user's limit when the fallback question is needed: aaa_round's own limit is off, the user's limit is under
    ROUND_SECS, and the user has not chosen keep-timeout. Asked even when the models answer was saved in an earlier
    run (review 2, fix 2). Else None."""
    if hr.CTX.get("deadline") or hr.CTX["ctx"].get_config("keep_timeout"):
        return None
    limit = _limit()
    return limit if limit is not None and 0 < limit < ROUND_SECS else None


def _who(cfg, slot) -> str:
    main = cfg.get("model") or {}
    aux = (cfg.get("auxiliary") or {}).get(slot) or {}
    provider = aux.get("provider") if aux.get("provider") not in (None, "", "auto") else main.get("provider")
    return f"{provider or 'your provider'}/{aux.get('model') or main.get('default') or 'your model'}"


def _qual():
    return judge.load_qualified(hr.HERE / "qualified.json")


def _menu() -> str:
    main = (_config().get("model") or {})
    limit = _ask_timeout()
    q = _qual()
    j, c = judge.defaults(q, main.get("provider"))
    pick = (f"Judge {j}, second opinion {c} (from qualified.json)." if j else
            f"For now both use your model ({main.get('default') or 'your Hermes model'}).")
    advice = (f"Models that reached 8 in our tests: {', '.join(q['reached_8'])}.\n" if q["reached_8"] else "")
    return MENU.format(main=main.get("default") or "your Hermes model", provider=main.get("provider") or "your provider",
                       pick=pick, advice=advice,
                       ask=TIMEOUT_ASK.format(limit=limit) if limit else "",
                       keep="   (or: /aaa models 1 keep-timeout)" if limit else "")


def _models(parts, sid) -> str:
    if len(parts) < 2 or parts[1] not in ("1", "2", "3"):
        return _menu()
    hr.CTX["ctx"].set_config("models", int(parts[1]))
    if hr.CTX.get("deadline"):
        note = "aaa_round has its own 65-minute tool limit; your timeouts are unchanged."
    else:  # the fallback (Brief issue 10): the answer, keep-timeout included, is remembered
        # A saved keep-timeout stays unless the user is asked again (review 3, fix 3): the question is not shown then.
        keep, limit = "keep-timeout" in parts or bool(hr.CTX["ctx"].get_config("keep_timeout")), _limit()
        hr.CTX["ctx"].set_config("keep_timeout", keep)
        if limit == UNREAD:
            note = "Could not read your tool timeout; it is unchanged."
        elif limit is None or limit >= ROUND_SECS:
            note = "Your tool timeout is already long enough; it is unchanged."
        elif keep:
            note = f"Kept your tool timeout ({limit:.0f} s): longer rounds will be cut."
        else:
            ok = _hermes("config", "set", "timeouts.tools.sequential_call", str(ROUND_SECS)) is not None
            note = (f"Set timeouts.tools.sequential_call from {limit:.0f} to {ROUND_SECS} s." if ok
                    else "Could not set the tool timeout.")
    if parts[1] == "1":
        j, c = judge.defaults(_qual(), (_config().get("model") or {}).get("provider"))
        for slot, mid in (("aaa_judge", j), ("aaa_confirm", c)):
            if mid and _hermes("config", "set", f"auxiliary.{slot}.model", mid) is not None:
                note += f" Set auxiliary.{slot}.model to {mid} (qualified.json)."
    pending = _PENDING.pop(sid, None)
    return f"Saved: option {parts[1]}. {note}\n" + (_start(pending, sid) if pending else "Now run /aaa <your prompt>.")


def _plugin_files() -> list:
    keep = ("__init__.py", "hermes_aaa.py", "hermes_round.py", "hermes_worker.py", "plugin.yaml", "qualified.json",
            "babelaz_pareto_aaa/",
            "assets/", "skills/")
    return [r for r in integrity.tree_files(hr.HERE) if r.startswith(keep) and "__pycache__" not in r]


def _new_folder(prompt) -> Path:
    """NEW folder: under the plugin setting runs_dir, else the session's folder unless that is inside a git repo
    (never touch the user's repos), else ~/aaa-runs. Named from the prompt and the time."""
    base = hr.CTX["ctx"].get_config("runs_dir")  # the user's explicit choice wins (Brief issue 20)
    if not base:
        base = hr._cwd()
        if not base or subprocess.run(["git", "-C", base, "rev-parse"], capture_output=True).returncode == 0:
            base = str(Path.home() / "aaa-runs")
    slug = "-".join(re.findall(r"[a-z0-9]+", prompt.lower())[:4]) or "slice"
    return Path(base) / f"aaa-{slug}-{time.strftime('%Y%m%d-%H%M%S')}"


def _refs(text):
    """`--refs <folder>`: up to 3 PNG pictures of a look the user likes (brief section 6). Returns (pictures, prompt
    without the flag, error or None). Read into memory only: never copied into the run folder, committed or shipped."""
    odd = re.search(r"\s--refs(=\S*)?(?=\s*$)|\s--refs=\S*", " " + text)  # --refs=dir, or a bare --refs at the end
    if odd:
        return [], (" " + text).replace(odd.group(0), " ").strip(), (
            "--refs: put the folder after a space: /aaa <prompt> --refs <folder> (1 to 3 .png files).")
    m = re.search(r"\s--refs\s+(\"[^\"]+\"|\S+)", " " + text)
    if not m:
        return [], text, None
    folder = Path(os.path.expanduser(m.group(1).strip('"')))
    rest = (" " + text).replace(m.group(0), " ").strip()
    pics = sorted(p for p in folder.glob("*") if p.suffix.lower() == ".png" and p.is_file() and not p.is_symlink()) \
        if folder.is_dir() else []
    pics = [p for p in pics if p.stat().st_size <= 8_000_000][:3]
    if not pics:
        return [], rest, f"--refs: no PNG pictures (at most 8 MB each) found in {folder}. Use 1 to 3 .png files."
    return [p.read_bytes() for p in pics], rest, None


def _start(text, sid) -> str:
    from .babelaz_pareto_aaa import check
    old = hr.RUNS.get(sid)
    if old and old.status not in bf.FINAL:
        return f"A Pareto AAA run is already going in this session ({old.root}). Start a new session for another."
    refs, text, err = _refs(text)
    if err:
        return err
    preset = pick.preset(text)  # brick or catch only on clear words; any other game: the house paper style
    aset = pick.anchor_set(preset)  # paper borrows the brick anchors: one anchor test per model covers both
    cfg = _config()
    todo = []
    for slot, role in (("aaa_judge", "judge"), ("aaa_confirm", "second opinion")):
        key = (slot, _who(cfg, slot), aset)  # a new model is tested again (review 1, fix 5)
        if key not in _QUAL:  # qualified.json first (0 calls); else 2 small calls per model, once per gateway
            prov, _, mid = key[1].partition("/")
            _QUAL[key] = judge.known_pass(_qual(), prov, mid, aset, check.anchors(aset)[3])
        if _QUAL[key] is None:
            todo.append((slot, role, key))
    job = _JOBS.get(sid)
    if todo and not job:  # the test can take minutes and a command prints once: run it aside and say so (first real run)
        job = _JOBS[sid] = {"msg": "starting"}
        job["thread"] = threading.Thread(target=check.judge_test, args=(job, aset, todo, _QUAL, hr._caller), daemon=True)
        job["thread"].start()
    late = None
    if job:
        job["thread"].join(8)  # a fast model finishes here; a slow one finishes inside the first round, which waits
        _JOBS.pop(sid, None)
        if job.get("done") and job.get("err"):
            return job["err"]
        late = None if job.get("done") else job
    taste = ""
    if refs:  # one extra call at round 0, on the strongest slot; the pictures are never written to disk
        try:
            reply, _usage, who = hr._caller("aaa_smoke", "Follow the text block exactly. Reply in plain text.")(
                refs, bf.REFS_TEXT.format(today=judge.today(), n=len(refs), prompt=text))
            taste = bf.refs_art(reply)
        except Exception as e:
            return f"Pareto AAA can't start: the --refs call failed ({type(e).__name__}: {e}). Try again, or run without --refs."
        if not taste:
            return "Pareto AAA can't start: the --refs call returned no art direction. Try again, or run without --refs."
    refs_note = (f"Your {len(refs)} --refs picture(s) went once to the smoke-test writer, which wrote the art direction "
                 "(1 extra call), and go to the judge and second opinion every round. They are never saved in the run "
                 "folder.\n" if refs else "")
    root = _new_folder(text)
    run = hr.Run(sid, root, text, preset)
    run.taste, run.refs = taste, refs
    run.base, run.config = ship.init_run(root)
    run.hashes = integrity.file_hashes(hr.HERE, _plugin_files())
    hr.RUNS[sid] = run
    turns, turns_note = int((cfg.get("goals") or {}).get("max_turns") or 20), ""
    if turns < 8:  # the run needs at least 8 goal turns (brief section 7); disclosed like the other bounds
        ok = _hermes("config", "set", "goals.max_turns", "8") is not None
        turns_note = (f"Changed goals.max_turns in your Hermes config from {turns} to 8 (a run needs at least 8 goal "
                      "turns).\n" if ok else f"Could not raise goals.max_turns from {turns} to 8; the goal may stop early.\n")
        hr._log(run, turns_note.strip())
    run.job = late
    qual = [] if late else [(s, _QUAL[(s, _who(cfg, s), aset)]["scores"]) for s in ("aaa_judge", "aaa_confirm")]
    edge = judge.BORDERLINE + "\n" if qual and judge.borderline(qual[0][1]) else ""  # design decision
    if late:
        edge = WAIT
    if edge:
        hr._log(run, edge.strip())
    hr._log(run, f"start: preset {preset}, base {run.base[:10]}, plugin files hashed {len(run.hashes)}, "
              f"qualified {qual}")
    if hr.CTX.get("deadline"):
        hr._log(run, f"tool deadline: aaa_round's own entry limit, {ROUND_SECS} s (execution_timeout)")
    else:
        limit = _limit()
        shown = "unread" if limit == UNREAD else (f"{limit:.0f} s" if limit else "none")
        hr._log(run, f"tool deadline: timeouts.tools.sequential_call, {shown} (aaa_round's "
                     f"own limit could not be set{'; the user chose keep-timeout' if hr.CTX['ctx'].get_config('keep_timeout') else ''})")
    goal = (f"Pareto AAA run {root.name}: {text}. Use only the aaa_round tool (find it with tool_search). Call "
            "aaa_round with build=true once. Then, in this same turn, keep calling aaa_round with keep = the offered "
            "fix IDs you accept, in order, until it reports a final STATUS (AAA, CAP, PLATEAU or UNREVIEWED). Drop "
            "a fix that conflicts with the preset or taste, or adds scope. Then reply with a two-line summary. The "
            "goal is done only when the reply starts with a final STATUS line.")
    hr._log(run, f"goal line printed for the user: /goal {goal}")
    notes = "You can open index.html in the run folder at any time to see the latest checked round.\n" + (
        pick.PAPER_NOTE + "\n" if preset == "paper" else "") + (
        pick.SAME_MODEL + "\n" if _who(cfg, "aaa_judge") == _who(cfg, "aaa_confirm") else "")
    return (f"Pareto AAA run ready: {root}\nPreset: {preset} (skills/aaa-studio/presets/{preset}.md).\n{notes}"
            f"Spend bounds: at most {st.CAP_WORKER_RUNS + 1} worker runs and 14 judge calls; token counts are logged "
            f"each round in .aaa/run.log. Billed by: {(cfg.get('model') or {}).get('provider') or 'your provider'}.\n"
            f"{turns_note}{edge}"
            f"Who sees what: screenshots go to the judge ({_who(cfg, 'aaa_judge')}) and the second opinion "
            f"({_who(cfg, 'aaa_confirm')}); the game's code goes to the worker (your model) and the smoke-test "
            f"writer ({_who(cfg, 'aaa_smoke')}).\n{refs_note}Paste this line to start (Hermes doesn't let a plugin "
            f"start a goal by itself):\n/goal {goal}")


def aaa_command(raw_args, session_id=None, **_):
    try:
        text = (raw_args or "").strip()
        parts = text.split()
        if parts[:1] == ["models"]:
            return _models(parts, session_id)
        if not text:
            return "Usage: /aaa <what to build>, for example /aaa make me a brick-breaker"
        if pick.refuse(text):  # a vague or app prompt: one reply, no menu, no folder, no model call
            return pick.refuse(text)
        if not session_id:
            return "Pareto AAA needs a session id. Send one message first, then run /aaa again."
        if hr.CTX["ctx"].get_config("models") is None or _ask_timeout():
            _PENDING[session_id] = text
            return _menu()
        return _start(text, session_id)
    except BaseException as err:
        return f"Pareto AAA could not start: {type(err).__name__}: {err}"
