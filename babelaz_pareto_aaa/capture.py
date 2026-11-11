"""Serve, capture and the in-page smoke run (brief section 6, step 1). Playwright drives its own Chromium.

Local runs without the Playwright download can set AAA_CHROME to a system Chrome binary (recorded in PROGRESS.md).
Captures run at device scale 2 (brief: sharp drawing). The round-0 strength lock lives in lock.py.
"""

import base64
import os
import random
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from playwright.sync_api import sync_playwright

from . import gates, integrity
from .detail import CROP_JS, DETAIL_JS, detail_box
from .lock import CONTEXT, INJECT_JS, PIN_JS, SMOKE_JS, strength

ASSETS = Path(__file__).resolve().parents[1] / "assets"
ARGS = ["--autoplay-policy=no-user-gesture-required", "--disable-blink-features=AutomationControlled"]
DSF = CONTEXT["device_scale_factor"]  # 2: captures run like a modern screen

# Runs in every page before its scripts: taps AudioContext output, records frame times and long tasks, and logs
# state() changes and input between __rec.start() and __rec.stop() (the state checks in gates.cycle_check).
# Everything it measures stays in this closure (review 2, note 5): a smoke can't write the frames, the sound or the
# log, and the recorder calls its own copies of the clock, requestAnimationFrame and the analyser read.
INIT_JS = """(() => {
  const now = performance.now.bind(performance), later = setTimeout.bind(window), apply = Reflect.apply;
  const raf = requestAnimationFrame.bind(window), level = AnalyserNode.prototype.getByteTimeDomainData;
  const taps = [], frames = [], stamps = [];
  let loud = 0, longtasks = 0;
  const oc = AudioNode.prototype.connect;
  AudioNode.prototype.connect = function (dest, ...rest) {
    const ctx = this.context;
    if (dest && dest === ctx.destination) {
      if (!ctx.__tap) { const an = ctx.createAnalyser(); an.fftSize = 512; oc.call(an, ctx.destination); ctx.__tap = an; taps.push(an); }
      return oc.call(this, ctx.__tap, ...rest);
    }
    return oc.call(this, dest, ...rest);
  };
  setInterval(() => {
    for (const t of taps) {
      const buf = new Uint8Array(t.fftSize); apply(level, t, [buf]);
      let s = 0; for (const v of buf) s += Math.abs(v - 128);
      loud = Math.max(loud, s / buf.length / 128);
    }
  }, 20);
  // Each frame: the wall clock at the start of this callback (what the gate uses: a stall shows at its full length), and
  // the rAF timestamp beside it for the evidence (the frame's scheduled start, which can hide a stall; Brief issue 25).
  (function tick(t) { frames[frames.length] = now(); stamps[stamps.length] = t; raf(tick); })(now());
  try { new PerformanceObserver(l => { longtasks += l.getEntries().length; }).observe({ type: 'longtask', buffered: true }); } catch (e) {}
  // The state log is private (review 2): pin() keeps the game's own state() here, once, before the smoke is injected,
  // and only copies of the log leave. The smoke can replace window.aaaGame, but not this copy, the log or window.__rec.
  let read = () => 'missing', pinned = false, on = false, last = null, log = [];
  const state = () => { try { return read(); } catch (e) { return 'missing'; } };
  const sample = () => { const s = state(); if (s !== last) { last = s; if (on) log[log.length] = { k: 'state', s, t: now() }; } };
  Object.defineProperty(window, '__rec', { value: Object.freeze({
    pin() { if (pinned) return; pinned = true; try { const g = window.aaaGame, f = g.state; if (typeof f === 'function') read = () => apply(f, g, []); } catch (e) {} },
    state,
    start() { sample(); log = [{ k: 'state', s: last || 'missing', t: now() }]; on = true; },
    stop() { sample(); on = false; const out = []; for (let i = 0; i < log.length; i++) out[i] = { ...log[i] }; return out; },
    sound() { const v = loud; loud = 0; return v; },  // the loudest level since the last read
    frames() { const out = []; for (let i = 0; i < frames.length; i++) out[i] = [frames[i], stamps[i]]; return out; },
    mark() { return { t: now(), lt: longtasks }; },
  }) });
  setInterval(sample, 10);
  for (const type of ['keydown', 'pointerdown'])
    window.addEventListener(type, () => { if (on) { sample(); log[log.length] = { k: 'input', t: now() }; later(sample, 0); } }, true);
})();"""

START_JS = "() => window.__rec.start()"
STOP_JS = "() => window.__rec.stop()"
STATE_JS = "() => window.__rec.state()"
SHARP_JS = """() => [...document.querySelectorAll('canvas')].map((c) => {
  const r = c.getBoundingClientRect();
  if (!(r.width > 0 && r.height > 0) || getComputedStyle(c).visibility === 'hidden') return null;
  return { id: c.id || 'canvas', w: c.width, h: c.height, cssW: r.width, cssH: r.height };
}).filter(Boolean)"""
LAYOUT_JS = """() => {
  const W = document.documentElement.clientWidth, out = [];
  if (document.documentElement.scrollWidth > W + 1) out.push(`page is ${document.documentElement.scrollWidth}px wide on a ${W}px screen`);
  // A skewed or rotated empty decoration's box is its transformed bounding box, so neighbours touch by a pixel or
  // two: skip it (M4). A plain translate or scale (matrix b = c = 0) moves nothing out of shape: still compared
  // (M4 review 1). Text, controls and media are always compared.
  const bent = t => t.startsWith('matrix3d') || (t.startsWith('matrix(') && t.slice(7, -1).split(',').slice(1, 3)
    .some(v => Math.abs(parseFloat(v)) > 1e-6));
  const moved = e => { for (let n = e; n && n !== document.body; n = n.parentElement)
    if (bent(getComputedStyle(n).transform)) return true; return false; };
  const decor = e => !e.textContent.trim() && !e.matches('a,button,input,select,textarea,label,[tabindex],[role],canvas,img,video,svg');
  const boxes = [...document.body.querySelectorAll('*')].filter(e => !e.ownerSVGElement)  // one box per <svg>
    .filter(e => !(decor(e) && moved(e)))
    .map(e => ({ e, r: e.getBoundingClientRect(), vis: getComputedStyle(e).visibility !== 'hidden' }))
    .filter(b => b.vis && b.r.width > 0 && b.r.height > 0);
  for (const { e, r } of boxes) if (r.right > W + 1 || r.left < -1) out.push(`${e.id || e.tagName.toLowerCase()} runs off screen`);
  for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) {
    const a = boxes[i], b = boxes[j];
    if (a.e.contains(b.e) || b.e.contains(a.e)) continue;
    const ox = Math.min(a.r.right, b.r.right) - Math.max(a.r.left, b.r.left);
    const oy = Math.min(a.r.bottom, b.r.bottom) - Math.max(a.r.top, b.r.top);
    if (ox > 2 && oy > 2) out.push(`${a.e.id || a.e.tagName} overlaps ${b.e.id || b.e.tagName}`);
  }
  return out;
}"""
AXE_JS = """async () => { const r = await axe.run(document, { resultTypes: ['violations'] });
  return r.violations.map(v => ({ id: v.id, impact: v.impact })); }"""
MARK_JS = "() => window.__rec.mark()"
SOUND_JS = "() => window.__rec.sound()"
FRAMES_JS = "() => window.__rec.frames()"


def _console(m, msg):
    """A console error, with its URL so the worker can fix it; the browser's own /favicon.ico request is not one."""
    url = (msg.location or {}).get("url") or ""
    if msg.type == "error" and not url.endswith("/favicon.ico"):
        m["console_errors"].append(f"{msg.text} ({url})" if url else msg.text)


@contextmanager
def serve(root):
    """python -m http.server on 127.0.0.1, on a free port, for the run folder. Stopped on exit."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    proc = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1", "--directory", str(root)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.05)
        yield f"http://127.0.0.1:{port}"
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def _fps(frames, t0, t1, col=0):
    """Average FPS and 1% low (from the 99th-percentile frame time) inside one window. frames holds [wall, rAF] pairs;
    col 0 (the wall clock at each callback's start) is what the gate uses, col 1 (the rAF timestamp) is evidence only."""
    f = [p[col] for p in frames if t0 <= p[0] <= t1]
    if len(f) < 3:
        return 0.0, 0.0
    deltas = sorted(b - a for a, b in zip(f, f[1:]))
    avg = (len(f) - 1) * 1000 / (f[-1] - f[0])
    p99 = deltas[min(len(deltas) - 1, int(0.99 * (len(deltas) - 1)))]
    return avg, 1000 / p99 if p99 > 0 else 0.0


def _png(data_url: str) -> bytes:
    return base64.b64decode(data_url.split(",", 1)[1])


def _browser(p):
    exe = os.environ.get("AAA_CHROME")  # then Playwright's own Chromium, then an installed Chrome or Edge (no download)
    for kw in ([{"executable_path": exe}] if exe else [{}, {"channel": "chrome"}, {"channel": "msedge"}]):
        try:
            return p.chromium.launch(headless=True, args=ARGS, **kw)
        except Exception as exc:  # noqa: BLE001 - try the next browser; the last error is raised
            err = exc
    raise err


def _state_walk(page, shot) -> dict:
    """Title, playing, then won or lost: one screenshot each, labelled by the state read just before it. Space starts
    and restarts the game (as in the fixtures), so the walk ends on title. Returns each state's screenshot hash."""

    def until(names, limit_s):
        t0 = time.monotonic()
        while True:
            s = page.evaluate(STATE_JS)
            if s in names or time.monotonic() - t0 > limit_s:
                return s
            page.wait_for_timeout(30)

    shots = {}
    s = page.evaluate(STATE_JS)
    shots[s] = shot("S1", f"State: {s}")
    page.keyboard.press(" ")
    if until(("playing",), 5) == "playing":
        shots["playing"] = shot("S2", "State: playing")
    end = until(("won", "lost"), 60)
    if end in ("won", "lost"):
        shots[end] = shot("S3", f"State: {end}")
    page.keyboard.press(" ")
    until(("title",), 2)
    return {k: integrity.sha256(v) for k, v in shots.items()}


def capture(root, out_dir, smoke_src: str, lock: bool) -> dict:
    """One browser session per round: the smoke run (with the state log), a state walk, a 3 s chaos burst and a 375 px
    snap, then a reduced-motion frame strip. Every page screenshot gets a 2x detail crop (its ID plus d).
    Returns {"measures": {...}, "items": [(id, label, png_bytes), ...]}. Every PNG is also saved in out_dir."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    items = []
    m = {"page_errors": [], "console_errors": []}

    with serve(root) as base, sync_playwright() as p:
        browser = _browser(p)
        try:
            ctx = browser.new_context(**CONTEXT)
            ctx.add_init_script(INIT_JS)
            page = ctx.new_page()
            helper = browser.new_page()  # the crops are cut in this page, from the screenshot bytes
            page.on("pageerror", lambda e: m["page_errors"].append(str(e)))
            page.on("console", lambda msg: _console(m, msg))

            def shot(eid, label):
                png = page.screenshot(type="png")
                (out_dir / f"{eid}.png").write_bytes(png)
                items.append((eid, label, png))
                vp = page.viewport_size
                box = detail_box(page.evaluate(DETAIL_JS), vp["width"], vp["height"], DSF)
                if box:
                    crop = base64.b64decode(helper.evaluate(CROP_JS, [base64.b64encode(png).decode(), *box]))
                    (out_dir / f"{eid}d.png").write_bytes(crop)
                    items.append((f"{eid}d", f"Detail of {eid} (2x crop, main play area)", crop))
                return png

            page.goto(base + "/index.html", wait_until="load")
            page.evaluate(PIN_JS)
            page.evaluate(INJECT_JS, smoke_src)
            m["dpr"] = page.evaluate("() => window.devicePixelRatio")
            shot("E1", "Desktop, first load")
            m["sharp"] = gates.sharp_problems(page.evaluate(SHARP_JS), m["dpr"], "1280")

            # Smoke run, inside the page, with the state log around it.
            s0 = page.evaluate(MARK_JS)
            page.evaluate(START_JS)
            smoke = page.evaluate(SMOKE_JS)
            s1 = page.evaluate(MARK_JS)
            m["cycle"] = gates.cycle_check(page.evaluate(STOP_JS))
            fr = page.evaluate(FRAMES_JS)  # this page's frames: read before the reload
            fa, fl = _fps(fr, s0["t"], s1["t"])
            ra, rl = _fps(fr, s0["t"], s1["t"], 1)
            m["smoke_ok"] = bool(smoke["ok"] and smoke["r"].get("ok"))
            m["smoke_detail"] = (
                f"ran to {smoke['r'].get('ended')} and restarted" if m["smoke_ok"] else f"failed: {smoke.get('err')}"
            )
            shot("E2", "Desktop, after smoke test")
            # A fresh page for the check's own measures (review 2, fix 5): a tone or a listener the smoke left behind
            # can't reach the sound gate, the state shots or the chaos burst. Sound: the quiet level before any input,
            # then the loudest level during the check's own state walk (its own key presses).
            page.reload(wait_until="load")
            page.evaluate(PIN_JS)
            page.evaluate(SOUND_JS)
            page.wait_for_timeout(500)
            m["sound_before"] = page.evaluate(SOUND_JS)
            m["state_shots"] = _state_walk(page, shot)
            m["sound_after"] = page.evaluate(SOUND_JS)

            # Chaos burst: 3 s of random keys and clicks, then snap to 375 px.
            rng = random.Random(7)
            c0 = page.evaluate(MARK_JS)
            end = time.monotonic() + 3
            while time.monotonic() < end:
                if rng.random() < 0.5:
                    page.keyboard.press(rng.choice([" ", "ArrowLeft", "ArrowRight"]))
                else:
                    page.mouse.click(rng.randint(10, 1270), rng.randint(10, 710))
                page.wait_for_timeout(rng.randint(20, 60))
            c1 = page.evaluate(MARK_JS)
            m["longtasks"] = (s1["lt"] - s0["lt"]) + (c1["lt"] - c0["lt"])
            page.set_viewport_size({"width": 375, "height": 667})
            page.wait_for_timeout(400)
            m["layout"] = page.evaluate(LAYOUT_JS)
            m["sharp"] += gates.sharp_problems(page.evaluate(SHARP_JS), m["dpr"], "375")
            shot("E3", "Phone width 375 px, after chaos burst")
            axe_src = (ASSETS / "axe.min.js").read_text(encoding="utf-8")
            page.add_script_tag(content=axe_src)
            v = page.evaluate(AXE_JS)
            m["axe_serious"] = sum(1 for x in v if x["impact"] in ("serious", "critical"))

            fr = page.evaluate(FRAMES_JS)
            fb, fbl = _fps(fr, c0["t"], c1["t"])
            rb, rbl = _fps(fr, c0["t"], c1["t"], 1)
            m["fps_avg"], m["fps_low"] = min(fa, fb), min(fl, fbl)
            m["fps_avg_raf"], m["fps_low_raf"] = min(ra, rb), min(rl, rbl)  # evidence only (Brief issue 25)

            # Reduced-motion frame strip: three canvas frames after a start key.
            page.set_viewport_size({"width": 1280, "height": 720})
            page.emulate_media(reduced_motion="reduce")
            page.reload(wait_until="load")
            page.keyboard.press(" ")
            for i, delay in enumerate((0, 120, 240), start=4):
                page.wait_for_timeout(delay)
                canvas = page.query_selector("canvas")
                data = canvas.evaluate("c => c.toDataURL('image/png')") if canvas else None
                png = _png(data) if data else page.screenshot(type="png")
                (out_dir / f"E{i}.png").write_bytes(png)
                items.append((f"E{i}", f"Reduced motion frame {i - 3} of 3", png))

            m["lock"] = strength(browser, base, smoke_src, INIT_JS) if lock else None
        finally:
            browser.close()

    return {"measures": m, "items": items}
