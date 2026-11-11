"""Gates (brief section 6, step 2). Pure: takes measurements, returns pass or fail per gate. No I/O, no Hermes.

Thresholds: 55 FPS average, a 1% low frame of at most 33.75 ms by the wall clock (Brief issue 25; the demo machine
re-checks it), 60 s, 1 s.
"""

import math
import re

FPS_AVG_MIN = 55
# 1% low as a wall-clock frame time (Brief issue 25, DECIDED: option a). Set from data, midway between the worst smooth
# run (18.6 ms) and the best mild-jank run (48.9 ms), 3 runs each on the paper Brick build: 15.15 ms of margin on each
# side (evidence/M4/paper/fps/calibrate_measure.txt). The gate reads the 99th percentile, so an occasional
# 2-vsync-length frame (0.7% of frames) passes; at 2% of frames it measures 33.8-34.6 ms and fails
# (evidence/M4/paper/fps/two_vsync_probe.txt).
FPS_LOW_FRAME_MS = 33.75
FPS_LOW_MIN = 1000 / FPS_LOW_FRAME_MS  # about 29.6 FPS
SOUND_RISE = 0.01  # AnalyserNode energy after input minus before, on a 0-1 scale
WIN_LOSS_MAX_MS = 60000  # brief: win or loss within 60 s
RESTART_MAX_MS = 1000  # brief: restart in under 1 s, from the restart input to the title screen


def evaluate(m: dict) -> list:
    """Return one dict per gate: {name, ok, detail}. Failed gates become G fixes in check.py."""
    g = []

    def add(name, ok, detail):
        g.append({"name": name, "ok": bool(ok), "detail": detail})

    errs = m.get("page_errors", []) + m.get("console_errors", [])
    add("0 page or console errors", not errs, f"{len(errs)} found" + (f": {errs[0][:160]}" if errs else ""))
    lay = m.get("layout", [])
    add("no overflow or overlap at 375 px", not lay, "clean" if not lay else "; ".join(lay[:3]))
    add("axe serious = 0", m.get("axe_serious", 1) == 0, f"{m.get('axe_serious', 'not run')} serious or critical")
    add(
        f"FPS avg >= 55 and 1% low frame <= {FPS_LOW_FRAME_MS} ms",
        m.get("fps_avg", 0) >= FPS_AVG_MIN and m.get("fps_low", 0) >= FPS_LOW_MIN,
        f"avg {m.get('fps_avg', 0):.1f}, 1% low {m.get('fps_low', 0):.1f}",
    )
    rise = m.get("sound_after", 0) - m.get("sound_before", 0)
    add("sound rises after input", rise > SOUND_RISE, f"before {m.get('sound_before', 0):.3f}, after {m.get('sound_after', 0):.3f}")
    add("smoke test passes and hash matches", m.get("smoke_ok") and m.get("hash_ok"), m.get("smoke_detail", ""))
    add("scope check passes", m.get("scope_ok"), m.get("scope_detail", "clean"))
    deps = m.get("deps", [])
    add("0 new runtime dependencies", not deps, "none" if not deps else "; ".join(deps[:3]))
    sharp = m.get("sharp", [])
    add("sharp drawing: canvas backing store >= CSS size x 2", not sharp, "clean" if not sharp else "; ".join(sharp[:3]))
    cyc = m.get("cycle") or {}
    seen = " > ".join(cyc.get("seq", [])[:12]) or "no states read"
    add("state goes title, playing, won or lost, title", cyc.get("sequence_ok"), f"read: {seen}")
    win = cyc.get("win_loss_ms")
    add("won or lost within 60 s", win is not None and win <= WIN_LOSS_MAX_MS,
        "not reached" if win is None else f"{win / 1000:.1f} s")
    rs = cyc.get("restart_ms")
    add("restart under 1 s", rs is not None and rs < RESTART_MAX_MS, "not measured" if rs is None else f"{rs:.0f} ms")
    diff = states_problems(m.get("state_shots") or {})
    add("screenshots differ between title, playing and won or lost", not diff, "clean" if not diff else "; ".join(diff))
    return g


JS_FAULT = re.compile(r"\s*(Uncaught\s+)?(TypeError|ReferenceError|SyntaxError|RangeError|EvalError|URIError)\b")


def lock_passed(res: dict) -> bool:
    """One strength run (brief section 6, round-0 lock). True when the smoke FAILS for a game reason: it returns
    ok:false, or it throws any error that is not a JS runtime fault (a TypeError, ReferenceError, SyntaxError...),
    so a broken smoke cannot lock. The lock needs all three runs."""
    if res.get("ok") is False:  # any thrown error except a JS runtime fault (the smoke's own bug); real runs threw
        err = str(res.get("err", ""))  # "Timed out waiting for 'won' or 'lost' (current state: playing)", a game reason
        return not JS_FAULT.match(err)
    return (res.get("r") or {}).get("ok") is False


def cycle_check(log: list) -> dict:
    """The state checks (brief section 6), read from the in-page log of one smoke run.
    log: {'k': 'state', 's': title|playing|won|lost|missing, 't': ms} and {'k': 'input', 't': ms} entries.
    Returns the states seen, whether title -> playing -> won or lost -> title happened in that order, and the timings:
    win_loss_ms from playing to won or lost; restart_ms from the first input after the end to the title screen
    (or from the end, when no input came first)."""
    states = [e for e in log if e.get("k") == "state"]
    seq = []
    for e in states:
        if not seq or seq[-1] != e["s"]:
            seq.append(e["s"])
    out = {"seq": seq, "hooks": bool(seq) and not {"missing"} & set(seq), "sequence_ok": False,
           "win_loss_ms": None, "restart_ms": None}

    def first(names, start):
        return next((i for i in range(start, len(states)) if states[i]["s"] in names), None)

    i_title = first(("title",), 0)
    i_play = first(("playing",), i_title + 1) if i_title is not None else None
    i_end = first(("won", "lost"), i_play + 1) if i_play is not None else None
    i_back = first(("title",), i_end + 1) if i_end is not None else None
    if i_end is not None:
        out["win_loss_ms"] = states[i_end]["t"] - states[i_play]["t"]
    if i_back is None:
        return out
    out["sequence_ok"] = True
    t_end, t_back = states[i_end]["t"], states[i_back]["t"]
    presses = [e["t"] for e in log if e.get("k") == "input" and t_end <= e["t"] <= t_back]
    out["restart_ms"] = t_back - (presses[0] if presses else t_end)
    return out


def state_checks_ok(m: dict) -> bool:
    """Round-0 lock (brief section 6): the code's own state checks, all required before the smoke is locked. The hooks
    exist, the state goes title > playing > won or lost > title, win or loss within 60 s, restart under 1 s, and the
    state screenshots differ. Missing data fails."""
    c = m.get("cycle") or {}
    win, rs = c.get("win_loss_ms"), c.get("restart_ms")
    return bool(c.get("hooks") and c.get("sequence_ok") and win is not None and win <= WIN_LOSS_MAX_MS
                and rs is not None and rs < RESTART_MAX_MS and not states_problems(m.get("state_shots") or {}))


def sharp_problems(canvases: list, dpr: float, where: str) -> list:
    """Each visible canvas's backing store must be at least its CSS size x devicePixelRatio (brief: sharp drawing)."""
    out = []
    for c in canvases:
        need_w, need_h = math.floor(c["cssW"] * dpr), math.floor(c["cssH"] * dpr)
        if c["w"] < need_w or c["h"] < need_h:
            out.append(f"{where} px: {c['id']} is {c['w']}x{c['h']}, needs {need_w}x{need_h}")
    return out


def states_problems(shots: dict) -> list:
    """shots: {state: sha256 of its screenshot}. Needs title, playing and won or lost, and no two states may match."""
    out = []
    missing = [n for n in ("title", "playing") if n not in shots]
    if "won" not in shots and "lost" not in shots:
        missing.append("won or lost")
    if missing:
        out.append("no screenshot in " + ", ".join(missing))
    names = list(shots)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if shots[a] == shots[b]:
                out.append(f"{a} and {b} look the same")
    return out


def fps_only_failing(results: list) -> bool:
    """True when FPS is the one failing gate (the FPS near-miss rule: measure once more)."""
    failing = [g for g in results if not g["ok"]]
    return len(failing) == 1 and failing[0]["name"].startswith("FPS")


def fps_margin(m: dict) -> float:
    """How close a run is to both FPS floors: the smaller of the two ratios. Higher is better."""
    return min(m.get("fps_avg", 0) / FPS_AVG_MIN, m.get("fps_low", 0) / FPS_LOW_MIN)


def summary(gates: list) -> str:
    return "\n".join(f"{'PASS' if x['ok'] else 'FAIL'}  {x['name']}: {x['detail']}" for x in gates)
