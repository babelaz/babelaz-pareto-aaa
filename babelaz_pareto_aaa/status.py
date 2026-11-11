"""Pure logic, no I/O, no Hermes: fix IDs, round scores, STATUS and keep-best.

Rules: docs/BRIEF.md section 4. Unit tests: tests/test_status.py.
"""

from dataclasses import dataclass, field

AAA_BAR = 8  # judge and second opinion must both reach this
CAP_WORKER_RUNS = 6  # worker runs after round 0
PLATEAU_ROUNDS = 2  # judged rounds in a row that don't beat the best judged score
MAX_FIXES = 2  # J1, J2 or C1, C2
MAX_KEEP = 5  # KEEP list entries kept from the judge's reply (short by design)


@dataclass
class Round:
    n: int
    gate_fails: list = field(default_factory=list)  # G fixes (and S0); gated rounds are never judged
    judge: int | None = None
    confirm: int | None = None
    fixes: list = field(default_factory=list)  # J fixes, or C fixes when confirm disagreed
    keep: list = field(default_factory=list)  # KEEP list: what already works (same source as the fixes)
    unreviewed: str | None = None
    cached: bool = False
    models: dict = field(default_factory=dict)  # {"judge": id, "confirm": id}, as they actually ran

    @property
    def gated(self) -> bool:
        return bool(self.gate_fails)

    @property
    def score(self) -> int | None:
        """Gated and unreviewed rounds have no score. A confirmed round scores the lower of the two."""
        if self.judge is None:
            return None
        return self.judge if self.confirm is None else min(self.judge, self.confirm)

    @property
    def confirm_disagreed(self) -> bool:
        return self.confirm is not None and self.confirm < AAA_BAR

    @property
    def aaa_pass(self) -> bool:
        return (
            not self.gated
            and self.judge is not None
            and self.judge >= AAA_BAR
            and self.confirm is not None
            and self.confirm >= AAA_BAR
        )


def gate_fix(i: int, text: str) -> dict:
    return {"id": f"G{i}", "text": text}


def smoke_lock_fix() -> dict:
    return {"id": "S0", "text": "Round 0: the smoke test is not locked. It must fail on a blank page, a frozen game and blocked input, and pass on the game. The game must expose aaaGame.state(), and the state checks must pass."}


def judged_fixes(raw, known_ids, prefix: str = "J") -> list:
    """Keep only fixes that cite at least one known evidence ID. Renumber from 1, at most MAX_FIXES."""
    out = []
    for f in raw if isinstance(raw, list) else []:
        if not isinstance(f, dict):
            continue
        ev = [str(x) for x in (f.get("evidence") or [])]
        text = str(f.get("text", "")).strip()
        if not ev or not text or not set(ev) <= set(known_ids):
            continue
        out.append({"id": f"{prefix}{len(out) + 1}", "text": text, "evidence": ev})
        if len(out) == MAX_FIXES:
            break
    return out


def keep_list(raw) -> list:
    """The judge's KEEP list: short strings of what already works. Non-strings and blanks are dropped."""
    if not isinstance(raw, list):
        return []
    out = [str(x).strip() for x in raw if isinstance(x, str) and str(x).strip()]
    return out[:MAX_KEEP]


def top_complaint_fix(score: int, fixes: list, complaint: str) -> list:
    """Brief 6: a score below 8 with no valid fix makes the judge's top complaint J1."""
    if score < AAA_BAR and not fixes and complaint:
        return [{"id": "J1", "text": complaint, "evidence": []}]
    return fixes


def pick_fixes(judge_fixes: list, confirm_fixes: list, confirm_disagreed: bool) -> list:
    """C fixes replace the J fixes for the round when confirm disagrees."""
    return confirm_fixes if (confirm_disagreed and confirm_fixes) else judge_fixes


def plateau(rounds: list) -> bool:
    judged = [r for r in rounds if r.score is not None]
    counted = [r for r in judged if not r.confirm_disagreed]
    if len(counted) < PLATEAU_ROUNDS:
        return False
    for r in counted[-PLATEAU_ROUNDS:]:
        prior = [p.score for p in judged if p.n < r.n]
        if not prior or r.score > max(prior):
            return False
    return True


def status(rounds: list, worker_runs_after_round0: int) -> str:
    """Precedence: UNREVIEWED > AAA > CAP > PLATEAU > CONTINUE (brief section 4)."""
    cur = rounds[-1]
    if cur.unreviewed:
        return "UNREVIEWED"
    if cur.aaa_pass:
        return "AAA"
    if worker_runs_after_round0 >= CAP_WORKER_RUNS:
        return "CAP"
    if plateau(rounds):
        return "PLATEAU"
    return "CONTINUE"


def best_round(rounds: list) -> tuple:
    """Keep-best. Returns (round, judged). Highest score wins; ties go to the later round.
    If no round was judged, returns the latest round with judged=False, so its failing gates can be shown."""
    scored = [r for r in rounds if r.score is not None]
    if not scored:
        return rounds[-1], False
    return max(scored, key=lambda r: (r.score, r.n)), True


def status_line(state: str, score: int | None) -> str:
    return f"STATUS: {state}" + (f" · score {score}/10" if score is not None else "")


def why_below_8(state: str, rounds: list, smoke_locked: bool) -> str:
    """Design decision: a run that ends final below 8 says why in plain words and names the one next step
    most likely to help. One extra line after the code-stamped STATUS line; it never changes the STATUS."""
    if state not in ("CAP", "PLATEAU", "UNREVIEWED"):
        return ""
    judged = [r for r in rounds if r.score is not None]
    stronger = "Next: try a stronger worker model (/aaa runs the worker on your Hermes default model)."
    if state == "UNREVIEWED":
        reason = next((r.unreviewed for r in reversed(rounds) if r.unreviewed), None) or "it stopped early (see .aaa/run.log)"
        reason = " ".join(str(reason).split())[:300]  # one line: page text in a reason can never start a STATUS line
        return f"Why: the check could not finish: {reason}. Next: run /aaa again; if it happens again, send .aaa/run.log."
    if not smoke_locked:
        return "Why: the smoke test never locked, so no round was judged. " + stronger
    if not judged:
        return "Why: no round passed the code gates, so none was judged. " + stronger
    best = max(r.score for r in judged)
    first = min(r.n for r in judged if r.score == best)
    held = len([r for r in judged if r.n >= first])
    split = [r for r in judged if r.judge is not None and r.judge >= AAA_BAR and r.confirm_disagreed]
    if split:
        why = f"the judge gave {split[-1].judge} but the second opinion gave {split[-1].confirm}"
    elif state == "PLATEAU":
        why = f"scores levelled off at {best} for {held} rounds"
    else:
        why = f"the round budget ran out ({CAP_WORKER_RUNS} worker runs after round 0) at a best score of {best}"
    nxt = ("Next: give reference pictures of the look you want with /aaa <prompt> --refs <folder> (up to 3)."
           if best >= 7 else stronger)
    return f"Why: {why}. {nxt}"
