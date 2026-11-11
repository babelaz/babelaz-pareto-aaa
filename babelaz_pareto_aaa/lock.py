"""Round-0 lock: testing the test (brief section 6). The same aaaSmoke() must FAIL three ways, each for a game reason:
on about:blank, with the game loop frozen (requestAnimationFrame stubbed), and with input blocked (synthetic key and
pointer events dropped before they reach the game). Only a lock that passes all three stores and locks the smoke file.
"""

import json

from . import gates

CONTEXT = {"viewport": {"width": 1280, "height": 720}, "device_scale_factor": 2}  # the real capture's page
PIN_JS = "() => window.__rec && window.__rec.pin()"  # keep the game's own state() before the smoke is injected
INJECT_JS = """(src) => { const s = document.createElement('script'); s.textContent = src; document.head.appendChild(s); }"""
SMOKE_JS = """async () => { try { const r = await window.aaaSmoke(); return { ok: true, r }; } catch (e) { return { ok: false, err: String(e) }; } }"""
# Added before the game's own scripts, so the game starts with its loop or its input already cut.
FREEZE_JS = "window.requestAnimationFrame = () => 0;"
BLOCK_JS = (
    "for (const t of ['keydown', 'keyup', 'keypress', 'pointerdown', 'pointerup', 'mousedown', 'mouseup', 'click',"
    " 'touchstart', 'touchend']) window.addEventListener(t, (e) => e.stopImmediatePropagation(), true);"
)


def _run(browser, url: str, init, smoke_src: str, recorder: str) -> dict:
    """One run on the same page as the real capture (size, scale, recorder, pin), so a smoke can't tell them apart."""
    ctx = browser.new_context(**CONTEXT)
    try:
        ctx.add_init_script(recorder)
        if init:
            ctx.add_init_script(init)
        page = ctx.new_page()
        page.goto(url, wait_until="load")
        page.evaluate(PIN_JS)
        page.evaluate(INJECT_JS, smoke_src)
        return page.evaluate(SMOKE_JS)
    finally:
        ctx.close()


def strength(browser, base: str, smoke_src: str, recorder: str) -> dict:
    """The three runs. ok only when every run fails for a game reason (gates.lock_passed). recorder: capture.INIT_JS."""
    runs = {
        "blank page": _run(browser, "about:blank", None, smoke_src, recorder),
        "frozen loop": _run(browser, base + "/index.html", FREEZE_JS, smoke_src, recorder),
        "input blocked": _run(browser, base + "/index.html", BLOCK_JS, smoke_src, recorder),
    }
    parts = []
    for name, res in runs.items():
        verdict = "fails, as it must" if gates.lock_passed(res) else "PASSES, so the test is too weak"
        parts.append(f"{name}: {verdict} ({json.dumps(res, default=str)[:120]})")
    return {"ok": all(gates.lock_passed(r) for r in runs.values()), "detail": "; ".join(parts)}
