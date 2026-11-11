"""Evidence PNG (brief section 6, step 1): every screenshot and frame strip in one HTML grid, screenshotted once."""

import base64
import html
from pathlib import Path

from playwright.sync_api import sync_playwright

from .capture import _browser


def grid_html(items, gate_text: str, headline: str) -> str:
    cards = "".join(
        f'<figure><img src="data:image/png;base64,{base64.b64encode(png).decode()}">'
        f"<figcaption>{html.escape(eid)} · {html.escape(label)}</figcaption></figure>"
        for eid, label, png in items
    )
    return (
        '<!doctype html><meta charset="utf-8"><style>'
        "body{margin:0;padding:24px;width:1400px;font:14px/1.4 system-ui,sans-serif;background:#fff;color:#111}"
        "h1{font-size:20px;margin:0 0 8px}pre{background:#f4f4f4;padding:12px;white-space:pre-wrap}"
        ".grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}figure{margin:0}"
        "img{width:100%;border:1px solid #bbb}figcaption{margin-top:4px}</style>"
        f"<h1>{html.escape(headline)}</h1><pre>{html.escape(gate_text)}</pre><div class=grid>{cards}</div>"
    )


def render(items, gate_text: str, headline: str, path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = grid_html(items, gate_text, headline)
    with sync_playwright() as p:
        browser = _browser(p)
        try:
            page = browser.new_page(viewport={"width": 1448, "height": 900})
            page.set_content(doc)
            page.screenshot(path=str(path), full_page=True)
        finally:
            browser.close()
    return path
