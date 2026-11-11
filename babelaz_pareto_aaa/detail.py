"""Judge detail crop (brief section 6, judge step): a 2x crop of the main play area, cut from the device-scale-2 screenshot.

detail_box is pure. DETAIL_JS and CROP_JS run in the browser (no image library): the game may expose an optional
aaaGame.focus() returning {x, y, w, h} in CSS px of the viewport; if it is absent, the centre 50% of the largest visible
canvas is used. The crop is real pixels, downscaled only to fit 512 px, never upscaled.
"""

DETAIL_JS = """() => {
  const g = window.aaaGame;
  if (g && typeof g.focus === 'function') {
    try {
      const f = g.focus();
      if (f && [f.x, f.y, f.w, f.h].every(Number.isFinite) && f.w > 0 && f.h > 0) return { x: f.x, y: f.y, w: f.w, h: f.h };
    } catch (e) { /* fall back to the canvas centre */ }
  }
  let best = null, area = 0;
  for (const c of document.querySelectorAll('canvas')) {
    const r = c.getBoundingClientRect();
    if (r.width * r.height > area && getComputedStyle(c).visibility !== 'hidden') { area = r.width * r.height; best = r; }
  }
  return best ? { x: best.left + best.width * 0.25, y: best.top + best.height * 0.25, w: best.width * 0.5, h: best.height * 0.5 } : null;
}"""

CROP_JS = """async ([b64, sx, sy, sw, sh]) => {
  const img = new Image(); img.src = 'data:image/png;base64,' + b64; await img.decode();
  const k = Math.min(1, 512 / Math.max(sw, sh));
  const c = document.createElement('canvas');
  c.width = Math.max(1, Math.round(sw * k)); c.height = Math.max(1, Math.round(sh * k));
  c.getContext('2d').drawImage(img, sx, sy, sw, sh, 0, 0, c.width, c.height);
  return c.toDataURL('image/png').split(',')[1];
}"""


def detail_box(box, vw, vh, dsf):
    """box: {x, y, w, h} in CSS px of the viewport, or None. Returns (sx, sy, sw, sh) in device pixels of the
    screenshot, clipped to the viewport, or None when nothing is left to crop."""
    if not box:
        return None
    x0, y0 = max(0.0, box["x"]), max(0.0, box["y"])
    x1, y1 = min(float(vw), box["x"] + box["w"]), min(float(vh), box["y"] + box["h"])
    if x1 - x0 < 1 or y1 - y0 < 1:
        return None
    return round(x0 * dsf), round(y0 * dsf), round((x1 - x0) * dsf), round((y1 - y0) * dsf)
