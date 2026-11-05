"""Share of input tokens read from the prompt cache, per Claude Code session (plan part 1.7).

    py -3 -I tools/cache_share.py [--project-dir DIR] [--last N]

Reads the transcripts (*.jsonl) of one Claude Code project folder. For every assistant turn Claude Code records
input_tokens, cache_creation_input_tokens and cache_read_input_tokens; the share is read / (all three).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

DEFAULT = Path.home() / ".claude" / "projects" / "C--Users-the owner-private--------------private"


def share(path: Path) -> tuple[int, int, int, int]:
    fresh = made = read = turns = 0
    seen: set[str] = set()
    for line in path.open(encoding="utf-8", errors="replace"):
        try:
            m = json.loads(line)
        except ValueError:
            continue
        msg = m.get("message") or {}
        u = msg.get("usage") if m.get("type") == "assistant" else None
        if not u or msg.get("id") in seen:          # one API call can span several transcript lines
            continue
        seen.add(str(msg.get("id")))
        fresh += u.get("input_tokens", 0)
        made += u.get("cache_creation_input_tokens", 0)
        read += u.get("cache_read_input_tokens", 0)
        turns += 1
    return fresh, made, read, turns


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-dir", type=Path, default=DEFAULT)
    ap.add_argument("--last", type=int, default=10)
    a = ap.parse_args()
    files = sorted(a.project_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)[-a.last:]
    tot = [0, 0, 0]
    print(f"{'session':10} {'calls':>6} {'cache read':>12} {'share':>7}")
    for f in files:
        fresh, made, read, turns = share(f)
        allin = fresh + made + read
        if not allin:
            continue
        tot = [tot[0] + fresh, tot[1] + made, tot[2] + read]
        print(f"{f.stem[:8]:10} {turns:6d} {read:12,d} {read / allin:7.1%}")
    s = sum(tot)
    if s:
        print(f"{'all':10} {'':6} {tot[2]:12,d} {tot[2] / s:7.1%}")


if __name__ == "__main__":
    main()
