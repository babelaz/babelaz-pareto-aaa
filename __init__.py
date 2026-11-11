"""HERMES ADAPTER for Pareto AAA by babelaz: register(ctx) and the three hooks. /aaa is in hermes_aaa.py, aaa_round
and the run state in hermes_round.py, the worker process in hermes_worker.py; the core (babelaz_pareto_aaa/) never
imports Hermes.

Every hook body is wrapped in try/except BaseException: pre_tool_call blocks on error, the others change nothing.
Hooks key on session_id, never on environment variables, because the worker's turn and hooks run in this same gateway
(M1 fix): the orchestrator is the session that ran /aaa; the worker is found by its stderr "Session:" line or by
ctx.session_cwd() == its run folder.
"""

import json
import re

try:
    from . import hermes_aaa
    from . import hermes_round as hr
except ImportError:
    if __package__:  # loaded by Hermes as the plugin package: a real import error must surface
        raise
    hermes_aaa = hr = None  # imported as a lone file (pytest collecting the repo root): nothing to register

SLOTS = {"aaa_judge": "AAA judge", "aaa_confirm": "AAA second opinion", "aaa_smoke": "AAA smoke-test writer"}
BRIDGE = ("tool_search", "tool_describe", "tool_call")
SLOT_TIMEOUT = 900  # s per slot call: Hermes's aux default is 30 s, too short at xhigh/max reasoning (real run 5);
# a user's auxiliary.<slot>.timeout still wins
ROUND_DEADLINE = 3900  # s: the worker's 60 min runaway ceiling plus the check
STATUS_LINE = re.compile(r"^[\W_]*status[\W_]*:|status\W{0,4}(aaa|cap|plateau|unreviewed|continue)\b",
                         re.IGNORECASE)  # also **STATUS**:, > STATUS :, "1. STATUS: AAA", "Final STATUS — AAA"
WITHHELD = "STATUS: CONTINUE\nThis reply was withheld: the STATUS stamp failed. Call aaa_round again."  # never final
ROUND_SCHEMA = {
    "name": "aaa_round",
    "description": ("Pareto AAA: run one round (a fresh worker, then the check) and return the STATUS. Call it with "
                    "build=true once, then with keep = the offered fix IDs you accept, in order. Drop fixes that "
                    "conflict with the preset or taste or add scope. Loop in one turn until the STATUS is final."),
    "parameters": {"type": "object", "properties": {
        "build": {"type": "boolean", "description": "true for round 0 only"},
        "keep": {"type": "array", "items": {"type": "string"}, "description": "offered fix IDs to apply, in order"}}},
}


# ---------------------------------------------------------------- hooks
def _targets_round(tool_name, args) -> bool:
    """Option B (Brief issue 9): tool_describe and tool_call only for aaa_round (tool_search with any words: the caller)."""
    if tool_name == "tool_describe":
        names = args.get("names")
        names = [names] if isinstance(names, str) else names
        return isinstance(names, list) and bool(names) and all(n == "aaa_round" for n in names)
    calls = args.get("calls", [{"name": args.get("name")}])
    calls = json.loads(calls) if isinstance(calls, str) else calls
    calls = [calls] if isinstance(calls, dict) else calls
    return isinstance(calls, list) and bool(calls) and all(isinstance(c, dict) and c.get("name") == "aaa_round"
                                                          for c in calls)


def _pre_tool_call(tool_name=None, args=None, session_id=None, **_):
    try:
        args = args if isinstance(args, dict) else {}
        if session_id in hr.STOPPED["sessions"] or (session_id not in hr.RUNS and hr.STOPPED["dirs"]
                                                  and hr._cwd() in hr.STOPPED["dirs"] and hr._worker_of(session_id) is None):
            return {"action": "block", "message": "This run was stopped. Do not call tools; end your reply now."}
        names_round = tool_name == "aaa_round" or (tool_name in BRIDGE[1:] and _targets_round(tool_name, args))
        if session_id in hr.RUNS:
            if names_round or tool_name == "tool_search":
                return None
            return {"action": "block", "message": f"Pareto AAA: the orchestrator's only tool is aaa_round "
                                                  f"('{tool_name}' was blocked)."}
        if names_round:
            return {"action": "block", "message": "aaa_round belongs to the session that ran /aaa."}
        return None
    except BaseException:
        return {"action": "block", "message": "Blocked: the Pareto AAA allowlist failed (fail closed)."}


def _transform_llm_output(response_text=None, session_id=None, **_):
    """Stamp the STATUS held in memory at the start of the orchestrator's reply (the /goal judge reads 4,000 chars).
    Hermes keeps the model's own text when this returns None or "" (agent/turn_finalizer.py:540-543), so a failure
    returns WITHHELD instead: the model's text, and any STATUS line it wrote, never reaches the /goal judge."""
    try:
        run = hr.RUNS.get(session_id)
        if run is None:
            return None
        kept = [ln for ln in (response_text or "").splitlines() if not STATUS_LINE.search(ln)]
        return run.line + "\n" + (run.why + "\n" if getattr(run, "why", "") else "") + "\n".join(kept)
    except BaseException:
        return WITHHELD


def _pre_verify(session_id=None, **_):
    """Worker only: no finish until every ID is done or blocked in aaa-done.json (Hermes caps the nudges at 3)."""
    try:
        run = hr._worker_of(session_id)
        missing = hr._missing(run) if run else []
        if missing:
            return {"action": "continue", "message": "not finished: " + ", ".join(missing)
                    + ". Do them, or mark them in aaa-done.json, then stop."}
        return None
    except BaseException:
        return None


def _round_deadline() -> bool:
    """aaa_round gets its own tool deadline, ROUND_DEADLINE s (Brief issue 10, DECIDED Nov 7), so /aaa need not change
    the user's timeouts.tools.sequential_call. Hermes applies max(that setting, the entry's execution_timeout)
    (agent/tool_dispatch_helpers.py:107-114; tools/registry.py:232). ctx.register_tool takes no such argument
    (hermes_cli/plugins.py:463), so this sets it on the registry entry: an internal call. True only when the entry is
    this plugin's own in this process. Under plugins.isolation: host the plugin runs in a host process whose registry
    never holds the entry (hermes_cli/plugin_host_child.py:1-7), so this returns False and /aaa asks instead."""
    try:
        from tools.registry import registry
        entry = registry.get_entry("aaa_round")
        if entry is None or entry.handler is not hr.aaa_round or not hasattr(entry, "execution_timeout"):
            return False  # not our entry here, or a Hermes without the field: never set a dead attribute
        entry.execution_timeout = float(ROUND_DEADLINE)
        return True
    except Exception:
        return False


def register(ctx):
    hr.CTX["ctx"] = ctx
    ctx.register_hook("pre_tool_call", _pre_tool_call)
    ctx.register_hook("pre_verify", _pre_verify)
    ctx.register_hook("transform_llm_output", _transform_llm_output)
    ctx.register_tool(name="aaa_round", toolset="aaa", schema=ROUND_SCHEMA, handler=hr.aaa_round,
                      description=ROUND_SCHEMA["description"])
    hr.CTX["deadline"] = _round_deadline()  # False: /aaa asks to raise timeouts.tools.sequential_call (the fallback)
    for key, name in SLOTS.items():
        ctx.register_auxiliary_task(key, display_name=name, description=f"Pareto AAA: {name}.",
                                    defaults={"provider": "auto", "timeout": SLOT_TIMEOUT})
    ctx.register_command("aaa", handler=hermes_aaa.aaa_command, description="Pareto AAA: build a game slice to 8/10",
                         args_hint="<prompt> [--refs <folder>] | models 1|2|3")
    ctx.register_skill("aaa-studio", hr.STUDIO / "SKILL.md", description="Pareto AAA studio rules (core).")
