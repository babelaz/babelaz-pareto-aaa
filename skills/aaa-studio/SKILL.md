---
name: aaa-studio
description: Pareto AAA studio rules for building and polishing one small web game slice to 8/10. Slice rule, ladder, anchors, review rules, worker rules, the aaaGame contract, and an excuse table. Presets, primitives and checklists load on demand.
---

# aaa-studio (core)

**Slice rule.** One ~60 s level: title → core action → feedback → win or lose → restart in under 1 s; pause. Web only. Polish what exists; never add levels, screens or modes.

**Ladder, before any new code.** 1 Will the player notice it? No → skip. 2 Already in the slice? → reuse. 3 Native browser feature? → use it. 4 Bundled primitive (`primitives/`)? → copy it in. 5 One line? → do that. 6 Only then, the minimum code.

**Anchors.** Each preset has three pictures of the same moment of play: dated (6), clean (7), current (8). An 8 is cohesive, has instant layered feedback, all states, steady frames, no bugs, and looks current, not dated. It has character: an identity, a material or pattern, purposeful colour, and a named shape language. Clean but plain is a 7. Two or more dated tells (glossy or bevelled buttons, heavy drop shadows, bloom on everything, gradient-heavy panels, untuned default fonts, clip-art icons, a cluttered HUD, particles with no restraint, motion with no easing) can't score 8. Textures must be crisp at play size and better than flat colour.

**Review rules (orchestrator).** You pass IDs only. Drop a judge fix that conflicts with the preset or the user's taste, or that adds scope (features, screens, levels, tests, refactors). Order the rest and call `aaa_round(keep=[…])`. Keep at least one unless none were offered. Loop in one turn until the STATUS is final, then reply. Code stamps the STATUS; you can't score, finish or override a gate.

**Worker rules.**
- The fix list is the whole job. Nobody will answer; there is no next turn. Assume and note.
- Read each file whole, once. No plan, preamble or progress reports.
- Do only the listed IDs; when each is done or blocked, write `aaa-done.json` and stop. G and S fixes can't be blocked.
- No new dependencies: no engine beyond the one your brief allows at round 0, no package manager files, CDN or web font. Inline SVG icons, system font stacks.
- After round 0 the file list is locked: edit those files, add new ones only under `assets/`.
- Don't touch `aaa.smoke.js` unless an S0 fix asks (it then gives the contract). Don't change anything on the KEEP list.
- On-screen words avoid `checklists/overused-words.md`, unless the user's own prompt uses them.

**aaaGame contract** (the check reads it; the smoke test drives it):
- `window.aaaGame = { state() }`, set once by page load, never replaced. Optional `focus()` → `{x, y, w, h}` in CSS px around the main play objects.
- `state()` → `'title'`, `'playing'`, `'won'` or `'lost'` (pause is `'playing'`).
- Keys on `window`. Space starts from the title; from won or lost, Space returns to the title in under 1 s.
- The title starts play only on a key or pointer input, never by itself.
- Loop: run ALL game logic and drawing from requestAnimationFrame (a fixed-step accumulator is fine). No setInterval or setTimeout game loop: the check freezes requestAnimationFrame, and a game that keeps running then can never pass.
- Started with Space and left alone, the game reaches won or lost within 60 s.
- Canvas backing store = CSS size × devicePixelRatio. Honour `prefers-reduced-motion`. A Web Audio sound on input. 0 console errors. Nothing off screen or overlapping at 375 px.

**Excuse table.**

| Excuse | Answer |
|---|---|
| "I should ask the user first." | Nobody will answer. Assume, note it in `aaa-done.json`, finish. |
| "Next I'll…" | There is no next run for you. Finish every ID now or mark it blocked. |
| "A test or helper module would make this safer." | No new test files or modules. The check is the test. |
| "A library would be faster." | 0 new dependencies is a gate. Copy a primitive or write the few lines. |
| "This fix is wrong; I'll improve something else." | Do the fix as worded, inside the frozen spec. Leave the KEEP list alone. |

**On demand (read only what you need).** `presets/brick.md`, `presets/catch.md`, `presets/paper.md` (the house style for any other game) (art direction with shape language). `primitives/primitives.js` (synth SFX, `juice()` that respects reduced motion, delta-time loop, pools, tokens). `checklists/game.md` (what the judge looks for), `checklists/overused-words.md`.
