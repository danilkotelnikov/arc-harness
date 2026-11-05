"""Deck gate: one command that checks every slide fragment and logs a row per slide (the owner, 2026-11-05).

    py -3 -I tools/deckgate.py FOLDER [--base base-m.html] [--label verdicts.json] [--no-log]
    py -3 -I tools/deckgate.py --selftest

For each fragment listed in the base file it measures plain features, runs the word check and writes a gate row.
verdicts.json maps a slide key to what the owner said (accepted, rejected). Those rows become training labels.
Exit code 1 when any slide fails. Browser checks stay in steps.mjs and look.mjs.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate  # noqa: E402
import lint_prose  # noqa: E402

# A phone number has ten digits after +7 and no base64 letter in front. A bare "+79" shows up inside embedded 3D data.
BANNED = re.compile(r"mail\.ru|(?<![A-Za-z0-9+/=])\+7[\s(-]*9\d\d[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}(?!\d)|127\.0\.0\.1|firewall", re.I)


def features(raw: str, path: Path) -> dict:
    """Numbers a person would notice on the slide. These are the inputs of a later classifier."""
    texts = [t for _, t in lint_prose.texts_of(path, raw)]
    words = sum(len(t.split()) for t in texts)
    heads = [" ".join(re.sub(r"<[^>]+>", " ", m.group(1)).split()) for m in lint_prose.HEADER.finditer(raw)]
    return {"words": words, "digits": sum(len(re.findall(r"\d[\d,.]*", t)) for t in texts), "captions": len(lint_prose.CAP.findall(raw)),
            "webgl": int("X3.stage" in raw), "formulas": len(re.findall(r"MK\.tex|\\frac|\\sum", raw)),
            "chart": int(bool(re.search(r"<(polyline|rect)\b.*?(bar|plot|axis)", raw, re.S | re.I))),
            "header_words": len(heads[0].split()) if heads else 0, "kb": len(raw) // 1024}


def check(folder: Path, base: str, log: bool, labels: dict) -> int:
    keys = re.findall(r"<!--frag:(\w+)-->", (folder / base).read_text(encoding="utf8"))
    bad = 0
    for k in keys:
        f = folder / f"{k}.html"
        if not f.exists():
            print(k, "MISSING"); bad += 1; continue
        raw = f.read_text(encoding="utf8")
        found = lint_prose.check(f, raw, mode="slide")
        errs = [x for x in found if x["severity"] == "error"]
        ban = BANNED.findall(raw)
        fx = features(raw, f)
        fx["lint_errors"], fx["lint_warns"], fx["banned"] = len(errs), len(found) - len(errs), len(ban)
        verdict = "fail" if errs or ban else "pass"
        bad += verdict == "fail"
        print(f"{k:9s} {verdict} {fx}", *[x["rule"] + ": " + x["excerpt"][:50] for x in errs[:3]], *ban[:3])
        if log:
            gate.record("deck-slide", k, verdict, by="deckgate", features=fx)
            if k in labels:
                gate.label("deck-slide", k, labels[k])
    return 1 if bad else 0


def selftest():
    raw = '<h2 class="big">Snöggo</h2><p>We compute 2 things.</p><script>x({cap: "A short caption"})</script>'
    fx = features(raw, Path("a.html"))
    assert fx["header_words"] == 1 and fx["digits"] == 1 and fx["captions"] == 1 and fx["webgl"] == 0
    assert BANNED.search("Evidence firewall") and not BANNED.search("Snöggo")
    assert BANNED.search("call +7 (912) 345-67-89") and not BANNED.search("AAAA+79bcdEFGH+7912")
    print("deckgate selftest: ok")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", nargs="?", type=Path)
    ap.add_argument("--base", default="base-m.html")
    ap.add_argument("--label", type=Path)
    ap.add_argument("--no-log", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ns = ap.parse_args()
    if ns.selftest:
        selftest(); return 0
    labels = json.loads(ns.label.read_text(encoding="utf8")) if ns.label else {}
    return check(ns.folder, ns.base, not ns.no_log, labels)


if __name__ == "__main__":
    sys.exit(main())
