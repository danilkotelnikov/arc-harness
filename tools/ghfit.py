"""Fit gate for a picture on a GitHub page (the owner, 2026-11-05: "fit it properly on GitHub so it won't blend with background").

    py -3 -I tools/ghfit.py PICTURE [--subject NAME] [--no-log]
    py -3 -I tools/ghfit.py --selftest

Shows the picture as an <img>, the way a README does, on GitHub light, dark and dark dimmed, at wide, laptop and phone widths.
It walks in from the page across the middle of each side. The worst side must reach 3 to 1 against the page, and the shape must hold.
A picture that fills the page width with its own colour fails on a page of that colour. This is the mistake the gate exists for.
Each run writes one row to the gate log. Exit 0 only when every case passes. It needs Playwright and Chromium.
"""
from __future__ import annotations

import argparse
import base64
import io
import mimetypes
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image
from playwright.sync_api import sync_playwright

PAGES = {"light": "#FFFFFF", "dark": "#0D1117", "dimmed": "#212830"}
WIDTHS = {"wide": 980, "laptop": 790, "phone": 343}
MIN_CONTRAST, PAD, WALK = 3.0, 20, 6
SCALE = 2


def luminance(c) -> float:
    v = np.asarray(c[:3], dtype=float) / 255
    v = np.where(v <= 0.03928, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)
    return float(0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2])


def contrast(a, b) -> float:
    x, y = sorted((luminance(a), luminance(b)), reverse=True)
    return (x + 0.05) / (y + 0.05)


def side_contrast(im: np.ndarray, page, box) -> dict:
    """The strongest colour step in the first pixels of each side, against the page colour."""
    x0, y0, x1, y1 = box
    n, mx, my = WALK * SCALE, (x0 + x1) // 2, (y0 + y1) // 2
    lines = {"left": im[my, x0:x0 + n], "right": im[my, x1 - n:x1], "top": im[y0:y0 + n, mx], "bottom": im[y1 - n:y1, mx]}
    return {k: max(contrast(p, page) for p in v) for k, v in lines.items()}


def measure(picture: Path) -> list[dict]:
    mime = mimetypes.guess_type(picture.name)[0] or "image/png"
    if picture.suffix.lower() == ".svg":
        mime = "image/svg+xml"
    data = base64.b64encode(picture.read_bytes()).decode()
    out = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        for pname, bg in PAGES.items():
            for wname, w in WIDTHS.items():
                pg = b.new_page(viewport={"width": w + 2 * PAD, "height": 900}, device_scale_factor=SCALE)
                pg.set_content(f'<body style="margin:0;background:{bg}"><div style="padding:{PAD}px"><img id=i src="data:{mime};base64,{data}" width="100%" style="display:block"></div>')
                pg.wait_for_timeout(1800)  # an animation has ended by now
                r = pg.evaluate("(()=>{const i=document.getElementById('i'),r=i.getBoundingClientRect();return [r.left,r.top,r.right,r.bottom,i.naturalWidth,i.naturalHeight]})()")
                im = np.array(Image.open(io.BytesIO(pg.screenshot())).convert("RGB"))
                pg.close()
                box = [round(v * SCALE) for v in r[:4]]
                sides = side_contrast(im, im[2, 2], box)
                shape = abs((r[3] - r[1]) / (r[2] - r[0]) - r[5] / r[4]) < 0.004
                worst = min(sides.values())
                out.append({"page": pname, "width": wname, "worst": round(worst, 2), "sides": {k: round(v, 2) for k, v in sides.items()},
                            "shape": shape, "ok": worst >= MIN_CONTRAST and shape and abs(r[2] - r[0] - w) < 1})
        b.close()
    return out


def selftest() -> int:
    def svg(body):
        return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 880 148" width="880" height="148">{body}</svg>'
    with tempfile.TemporaryDirectory() as d:
        pale = Path(d) / "pale.svg"
        pale.write_text(svg('<rect width="880" height="148" fill="#F5F3EE"/>'), encoding="utf8")
        edged = Path(d) / "edged.svg"
        edged.write_text(svg('<rect x="1.25" y="1.25" width="877.5" height="145.5" fill="#1B1A18" stroke="#F5F3EE" stroke-width="2.5"/>'), encoding="utf8")
        a = measure(pale)
        c = measure(edged)
    bad = [x for x in a if not x["ok"]]
    assert {x["page"] for x in bad} == {"light"}, f"a pale band must fail on the light page only: {bad}"
    assert all(x["ok"] for x in c), f"a dark band with a pale edge must pass everywhere: {[x for x in c if not x['ok']]}"
    assert contrast((255, 255, 255), (0, 0, 0)) > 20.9
    print("ghfit selftest: PASS")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("picture", nargs="?")
    ap.add_argument("--subject")
    ap.add_argument("--no-log", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.picture:
        ap.error("name a picture")
    rows = measure(Path(a.picture))
    for r in rows:
        print(f'{"PASS" if r["ok"] else "FAIL"} {r["page"]:7} {r["width"]:7} worst side {r["worst"]}:1 {r["sides"]}')
    good = all(r["ok"] for r in rows)
    worst = min(r["worst"] for r in rows)
    if not a.no_log:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import gate
        gate.record("gh-fit", a.subject or Path(a.picture).name, "pass" if good else "fail", note="picture on GitHub page colours",
                    features={"worst_contrast": worst, "cases": len(rows), "failed": sum(not r["ok"] for r in rows)})
    print("RESULT", "PASS" if good else "FAIL", f"worst {worst}:1")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
