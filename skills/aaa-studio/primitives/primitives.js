// aaa-studio primitives: copy what you need into the slice (no import, no build step). Plain browser JS.
// sfx(): synth sounds from Web Audio. juice(): eased effects that respect reduced motion. loop(): delta-time loop.
// pool(): reusable particles. tokens: read CSS custom properties so colour lives in one place.

const REDUCED = matchMedia('(prefers-reduced-motion: reduce)');

// One AudioContext, made on the first input (browsers block audio before a gesture).
let AC = null;
function sfx(freq = 440, ms = 90, type = 'triangle', gain = 0.12, slide = 0) {
  AC = AC || new AudioContext();
  const t = AC.currentTime, o = AC.createOscillator(), g = AC.createGain();
  o.type = type;
  o.frequency.setValueAtTime(freq, t);
  if (slide) o.frequency.exponentialRampToValueAtTime(Math.max(20, freq + slide), t + ms / 1000);
  g.gain.setValueAtTime(gain, t);
  g.gain.exponentialRampToValueAtTime(0.0001, t + ms / 1000);
  o.connect(g).connect(AC.destination);
  o.start(t);
  o.stop(t + ms / 1000 + 0.02);
}

// juice(from, to, ms, apply): an eased tween. Under reduced motion it jumps to the end (no movement).
const easeOut = (x) => 1 - Math.pow(1 - x, 3);
function juice(from, to, ms, apply) {
  if (REDUCED.matches) { apply(to); return; }
  const t0 = performance.now();
  (function step(now) {
    const k = Math.min(1, (now - t0) / ms);
    apply(from + (to - from) * easeOut(k));
    if (k < 1) requestAnimationFrame(step);
  })(t0);
}

// loop(update, draw): fixed simulation speed whatever the frame rate; dt in seconds, capped after a tab switch.
function loop(update, draw) {
  let last = performance.now();
  (function frame(now) {
    const dt = Math.min(0.05, (now - last) / 1000);
    last = now;
    update(dt);
    draw();
    requestAnimationFrame(frame);
  })(last);
}

// pool(size, make): fixed-size reuse, so effects never allocate in the loop or grow without limit.
function pool(size, make) {
  const items = Array.from({ length: size }, make);
  return {
    take() { const it = items.find((p) => !p.alive); if (it) it.alive = true; return it || null; },
    each(fn) { for (const it of items) if (it.alive) fn(it); },
  };
}

// tokens('--ink', '--bg'): colours from CSS custom properties on :root.
function tokens(...names) {
  const css = getComputedStyle(document.documentElement);
  return Object.fromEntries(names.map((n) => [n, css.getPropertyValue(n).trim()]));
}

// Sharp canvas: backing store = CSS size x devicePixelRatio (the sharp-drawing gate checks this).
function fitCanvas(canvas) {
  const dpr = window.devicePixelRatio || 1, r = canvas.getBoundingClientRect();
  canvas.width = Math.round(r.width * dpr);
  canvas.height = Math.round(r.height * dpr);
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return ctx;
}
