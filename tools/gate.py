"""Gate log: one flat record for every check, so a model can take a gate over later (the owner, 2026-11-05).

    py -3 -I tools/gate.py record GATE SUBJECT DECISION [--by NAME] [--note TEXT] [--f key=value ...] [--prediction 0.7]
    py -3 -I tools/gate.py label GATE SUBJECT OUTCOME [--note TEXT]     # what the owner said later: accepted, rejected
    py -3 -I tools/gate.py stats                                        # per gate: decisions, how often a pass was rejected later
    py -3 -I tools/gate.py export OUT.csv                               # flat table for a classifier
    py -3 -I tools/gate.py cover D-ID [PROMPT.txt]                      # prompt sentences with no ledger row
    py -3 -I tools/gate.py tasks D-ID                                   # ledger as a readable table
    py -3 -I tools/gate.py --selftest

A row is: at, gate, subject, decision (pass, fail, warn, skip), prediction, outcome, by, note, features.
The gate decides now. The outcome comes later from a person. A pass with a rejected outcome is what a model must learn.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.environ.get("ARC_HARNESS", Path(__file__).resolve().parent.parent))
STOP = set("the a an and or but of to in on at is are be it this that with for as not by from you your i my we our must should "
           "can could will would just really very also so do does did have has them they there what how why which".split())


def log_path() -> Path:
    return ROOT / "gates" / "gates.jsonl"


def rows() -> list[dict]:
    f = log_path()
    return [json.loads(l) for l in f.read_text(encoding="utf8").splitlines() if l.strip()] if f.exists() else []


def number(v: str):
    try:
        return float(v) if "." in v else int(v)
    except ValueError:
        return v


def record(gate, subject, decision, by="claude-code", note="", features=None, prediction=None):
    row = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "gate": gate, "subject": subject, "decision": decision,
           "prediction": prediction, "outcome": None, "by": by, "note": note, "features": features or {}}
    f = log_path()
    f.parent.mkdir(parents=True, exist_ok=True)
    with f.open("a", encoding="utf8") as h:
        h.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def label(gate, subject, outcome, note=""):
    """Set the outcome on the newest row of this gate and subject."""
    all_rows = rows()
    for row in reversed(all_rows):
        if row["gate"] == gate and row["subject"] == subject:
            row["outcome"] = outcome
            if note:
                row["note"] = (row["note"] + " | " + note).strip(" |")
            log_path().write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in all_rows), encoding="utf8")
            return True
    return False


def stats():
    by = {}
    for r in rows():
        g = by.setdefault(r["gate"], Counter())
        g[r["decision"]] += 1
        if r["outcome"]:
            g["labelled"] += 1
            g["missed"] += r["decision"] == "pass" and r["outcome"] == "rejected"
    return by


def export(out: Path):
    data = rows()
    keys = sorted({k for r in data for k in r["features"]})
    head = ["at", "gate", "subject", "decision", "prediction", "outcome", "by"]
    with out.open("w", newline="", encoding="utf8") as h:
        w = csv.writer(h)
        w.writerow(head + keys)
        for r in data:
            w.writerow([r.get(k) for k in head] + [r["features"].get(k) for k in keys])
    return len(data)


def bag(text: str) -> set:
    return {w for w in re.findall(r"[a-zа-яё0-9-]{3,}", text.lower()) if w not in STOP}


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+(?=[A-ZА-ЯЁ\"«(])|\n+", text)
    return [p.strip() for p in parts if len(p.split()) >= 3]


def ledger_rows(tag: str) -> list[dict]:
    f = ROOT / "intake" / f"{tag}.jsonl"
    return [json.loads(l) for l in f.read_text(encoding="utf8").splitlines() if l.strip()]


def cover(tag: str, prompt: Path | None = None, floor: float = 0.25) -> list[str]:
    """Sentences of the prompt that share under a quarter of their content words with every ledger row."""
    if prompt:
        said = sentences(prompt.read_text(encoding="utf8"))
    else:
        said = []
        for l in (ROOT / "intake" / "ledger.jsonl").read_text(encoding="utf8").splitlines():
            r = json.loads(l)
            if r.get("id") == tag and r.get("tasks"):
                said = [t["text"] for t in r["tasks"]]
    asks = [bag(r["ask"] + " " + " ".join(r.get("tags", []))) for r in ledger_rows(tag)]
    miss = []
    for s in said:
        b = bag(s)
        if b and max(len(b & a) / len(b) for a in asks) < floor:
            miss.append(s)
    return miss


def tasks(tag: str) -> str:
    out = [f"# {tag}", "", "| Id | Block | Slide | Request | Check | Status |", "|---|---|---|---|---|---|"]
    for r in ledger_rows(tag):
        out.append(f"| {r['id']} | {r['block']} | {r.get('slide') or ''} | {r['ask']} | {r['check']} | {r['status']} |")
    return "\n".join(out) + "\n"


def selftest():
    global ROOT
    keep = ROOT
    with tempfile.TemporaryDirectory() as d:
        ROOT = Path(d)
        record("words", "o1", "pass", features={"numbers": 3})
        record("words", "m3", "fail", features={"numbers": 9})
        assert label("words", "o1", "rejected")
        assert not label("words", "nothing", "rejected")
        s = stats()["words"]
        assert s["pass"] == 1 and s["fail"] == 1 and s["missed"] == 1
        assert export(Path(d) / "x.csv") == 2
        (ROOT / "intake").mkdir()
        (ROOT / "intake" / "D-1.jsonl").write_text(json.dumps({"id": "A", "ask": "Fix the broken line on slide twelve", "status": "open"}) + "\n", encoding="utf8")
        p = Path(d) / "p.txt"
        p.write_text("Fix the broken line. Paint the whole kitchen yellow today.", encoding="utf8")
        assert cover("D-1", p) == ["Paint the whole kitchen yellow today."]
    ROOT = keep
    print("gate selftest: ok")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="gate")
    ap.add_argument("--selftest", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    a = sub.add_parser("record")
    a.add_argument("gate"); a.add_argument("subject"); a.add_argument("decision", choices=("pass", "fail", "warn", "skip"))
    a.add_argument("--by", default="claude-code"); a.add_argument("--note", default=""); a.add_argument("--f", action="append", default=[])
    a.add_argument("--prediction", type=float)
    a = sub.add_parser("label")
    a.add_argument("gate"); a.add_argument("subject"); a.add_argument("outcome"); a.add_argument("--note", default="")
    sub.add_parser("stats")
    a = sub.add_parser("export"); a.add_argument("out", type=Path)
    a = sub.add_parser("cover"); a.add_argument("tag"); a.add_argument("prompt", nargs="?", type=Path)
    a = sub.add_parser("tasks"); a.add_argument("tag")
    ns = ap.parse_args(argv)
    if ns.selftest:
        selftest(); return 0
    if ns.cmd == "record":
        record(ns.gate, ns.subject, ns.decision, ns.by, ns.note, {k: number(v) for k, v in (x.split("=", 1) for x in ns.f)}, ns.prediction)
    elif ns.cmd == "label":
        if not label(ns.gate, ns.subject, ns.outcome, ns.note):
            print("no such row"); return 1
    elif ns.cmd == "stats":
        for g, c in sorted(stats().items()):
            print(g, dict(c))
    elif ns.cmd == "export":
        print("rows", export(ns.out))
    elif ns.cmd == "cover":
        miss = cover(ns.tag, ns.prompt)
        print("\n".join(miss) or "every sentence has a row")
        return 1 if miss else 0
    elif ns.cmd == "tasks":
        sys.stdout.write(tasks(ns.tag))
    else:
        ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
