"""HERMES ADAPTER: the worker is `hermes chat -q --oneshot` in its own process (brief sections 3 and 4).

No work-time limit. The worker is stopped only when it shows no sign of life (no new stream-json output and no file
change in the run folder) for STALL_SECS, or at the CEILING_SECS runaway ceiling. In Hermes v0.22.7 the worker's turn
runs in the gateway and outlives its client (Brief issue 11), so on a stop the caller's on_stop() marks the session
stopped (its tools are then blocked) before the client is killed, and sweep() removes the session's tool processes.
"""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from .babelaz_pareto_aaa import integrity

STALL_SECS = float(os.environ.get("AAA_STALL_SECS", 1200))  # 20 min with no sign of life (10 in brief §4: a 7 token/s
# model was stopped while composing its first file, first real run)
CEILING_SECS = float(os.environ.get("AAA_CEILING_SECS", 3600))  # † brief §4: 60 min runaway ceiling
MAX_TURNS = 200  # † bounds the model calls of a run, and of a stopped run whose tools are blocked (Brief issue 11)
POLL = 2.0
DROP_ENV = ("HERMES_KANBAN_TASK",)  # a kanban marker changes exit codes (docs/HERMES_API.md, exit codes)


def command(query_file, run_dir, model=None, provider=None) -> list:
    """The worker's command line. Model and provider only when the user set worker_model (else their default)."""
    cmd = [sys.executable, "-m", "hermes_cli.main", "chat", "--query-file", str(query_file), "--oneshot",
           "--source", "tool", "--format", "stream-json", "--toolsets", "terminal,file", "--in", str(run_dir),
           "--max-turns", str(MAX_TURNS)]
    if model:
        cmd += ["-m", model]
    if provider:
        cmd += ["--provider", provider]
    return cmd


def clean_env() -> dict:
    """The gateway's environment minus the last session it served (gateway/session_context.py:90), so a later sweep
    by HERMES_SESSION_ID can never match the worker's client by mistake."""
    return {k: v for k, v in os.environ.items() if not k.startswith("HERMES_SESSION_") and k not in DROP_ENV}


def _newest(run_dir: Path) -> float:
    """Newest file change in the run folder, outside .git and the check's own .aaa log (integrity.tree_files)."""
    newest = 0.0
    for rel in integrity.tree_files(run_dir):
        try:
            newest = max(newest, os.lstat(Path(run_dir) / rel).st_mtime)
        except OSError:  # a file removed mid-walk
            pass
    return newest


def session_line(err_path: Path) -> str | None:
    """The gateway session id the client prints on stderr ("Session: <id>", hermes_cli/gateway_chat.py:227)."""
    try:
        for line in err_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("Session: "):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return None


def result_event(out_path: Path) -> dict:
    """The stream-json result event (exit_code, text, tokens, error). error is set only when the run could not start."""
    found = {}
    try:
        for line in out_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if '"result"' in line:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("type") == "result":
                    found = ev
    except OSError:
        pass
    return found


def _kill(proc) -> None:
    try:
        if hasattr(os, "killpg"):
            os.killpg(proc.pid, signal.SIGKILL)
        else:  # Windows: no process groups here (the sweep below is Linux-only too)
            proc.kill()
    except OSError:
        pass
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        pass


def sweep(session_id) -> list:
    """Kill every process whose environment holds HERMES_SESSION_ID=<the worker's session> (Brief issue 8, decided).
    Hermes puts that value on every tool process of the session (tools/environments/local.py:245-262). Linux only."""
    if not session_id or not os.path.isdir("/proc"):
        return []
    needle, me, killed = f"HERMES_SESSION_ID={session_id}".encode(), os.getpid(), []
    for pid in os.listdir("/proc"):
        if not pid.isdigit() or int(pid) == me:
            continue
        try:
            with open(f"/proc/{pid}/environ", "rb") as fh:
                env = fh.read().split(b"\0")
        except OSError:
            continue
        if needle in env:
            try:
                os.kill(int(pid), signal.SIGKILL)
                killed.append(int(pid))
            except OSError:
                pass
    return killed


def run(cmd, run_dir, out_dir, on_session, on_stop, interrupted=lambda: False) -> dict:
    """Run one worker to its end, or stop it on a stall, at the ceiling, or on the user's interrupt. Returns
    {"exit", "stopped": None | "stall" | "ceiling" | "interrupt", "session", "error", "tokens", "secs"}."""
    run_dir, out_dir = Path(run_dir), Path(out_dir)
    out_path, err_path = out_dir / "worker.jsonl", out_dir / "worker.err"
    with open(out_path, "wb") as out, open(err_path, "wb") as err:
        proc = subprocess.Popen(cmd, cwd=str(run_dir), env=clean_env(), stdin=subprocess.DEVNULL, stdout=out,
                                stderr=err, start_new_session=True)
    t0 = last = time.time()
    size, mtime, session, stopped = -1, _newest(run_dir), None, None
    while proc.poll() is None:
        time.sleep(POLL)
        now_size, now_mtime = out_path.stat().st_size, _newest(run_dir)
        if now_size != size or now_mtime != mtime:
            size, mtime, last = now_size, now_mtime, time.time()
        if session is None and (session := session_line(err_path)):
            on_session(session)
        if time.time() - last > STALL_SECS:
            stopped = "stall"
        elif time.time() - t0 > CEILING_SECS:
            stopped = "ceiling"
        elif interrupted():
            stopped = "interrupt"
        if stopped:
            on_stop()  # block the session's tools first: its turn outlives the client
            _kill(proc)
            break
    session = session or session_line(err_path)
    res = result_event(out_path)
    return {"exit": proc.returncode, "stopped": stopped, "session": session, "error": res.get("error"),
            "tokens": res.get("tokens"), "secs": round(time.time() - t0, 1)}
