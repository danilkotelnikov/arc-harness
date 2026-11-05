"""Word checker for every agent and for the Molecullage Motion engine (English and Russian).

    py -3 -I tools/lint_prose.py FILE... [--mode chat|doc|slide|film] [--technical] [--json]
    py -3 -I tools/lint_prose.py --selftest

English house rules come from the private prose guard (repo tooling/prose/prose_guard.py) when it is
found; the extra English words, the Russian rules, the term list, the ASD-STE100 sentence limits and the
slide and film rules are added here. Exit code 1 when any finding is an error.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEX = ROOT / "shared" / "lexicon"
GUARD = Path(os.environ.get("ARC_PROSE_GUARD",
                            r"privatethe owner\private-work\repo\tooling\prose\prose_guard.py"))

STRICT_MODES = {"slide", "film"}        # where a refused word or a wrong term is an error
STATUS_MARKS = re.compile(r"\b(preliminary|tbd|wip|coming soon|draft|placeholder)\b|предварительн|черновик|в разработке",
                          re.I)
RU_CONTRAST = re.compile(r"(?<![\w-])не\s+[^,.;:!?\n]{1,40},\s*а\s", re.I)
EN_CONTRAST = re.compile(r"\b(?:is|are|was|were)\s+not\s+[^,.;\n]{1,40},\s*(?:but|it is|it's)\b", re.I)
GENITIVE = re.compile(r"(?:\b[а-яё]+(?:ого|его|ых|их|ия|ии|ости|ения|ания|ции|ства)\b[\s,]+){4,}", re.I)
IMPERATIVE_EN = re.compile(r"^(?:add|apply|build|call|check|copy|create|delete|do|enter|fill|install|keep|make|move|open|put|read|remove|run|save|send|set|start|stop|turn|type|use|write)\b", re.I)
SENT = re.compile(r"(?<=[.!?])\s+")
# A slide header names the thing (the owner, 2026-11-05: "just write a simple Snoggo"). No comma, number, verb or long phrase.
HEADER = re.compile(r"<h[12][^>]*>(.*?)</h[12]>", re.S | re.I)
SLOGAN = re.compile(r",|\d|\b(?:one|two|three|four|five|six|seven|eight|nine|ten|sixty|hundred|thousand|many|every|all|only)\b"
                    r"|\b(?:is|are|was|be|become|becomes|decide|decides|hold|holds|price|prices|learn|learns|leave|leaves|"
                    r"win|wins|beat|beats|prove|proves|meet|meets|turn|turns|make|makes|stop|stops|show|shows|keep|keeps|"
                    r"need|needs|pass|passes|fit|fits)\b", re.I)
CAP = re.compile(r"""\bcap:\s*(['"`])(.*?)\1""")
FENCE = re.compile(r"```.*?```", re.S)
INLINE = re.compile(r"`[^`\n]+`")
URL = re.compile(r"https?://\S+")
# A bare verdict, or a short negative tacked on as its own sentence (the owner, 2026-11-06: forbidden with no exception).
CLIP_VERDICT = re.compile(r"^(?:and |but |so |all )?(?:[\w'-]+ ){0,3}(?:check|checks|test|tests|gate|gates|scan|scans|build|builds|run|runs)\s+(?:pass|passes|passed|fail|fails|failed|work|works|is green|are green)[.!]$"
                          r"|^(?:проверк\w+|тест\w*|сборк\w+)\s+(?:прошл\w*|пройден\w*|не прошл\w*)[.!]$", re.I)
CLIP_TAIL = re.compile(r"^(?:no|nothing|none)\b(?:\s+[\w'-]+){0,5}?\s+(?:yet|so far)[.!]$"
                       r"|^(?:nothing|no\s+[\w'-]+)\s+(?:is|was|has been|are|were|have been)\s+[\w'-]+(?:ed|en|t)[.!]$"
                       r"|^(?:ничего|никто)\s+не\s+\w+[.!]$"
                       r"|^(?:[\w-]+\s+){1,3}пока\s+не\s+\w+[.!]$", re.I)


def _lines(path: Path):
    p = LEX / path
    if not p.exists():
        return []
    return [l.strip() for l in p.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]


def _tsv(path: Path):
    rows = _lines(path)
    if not rows:
        return []
    head = rows[0].split("\t")
    return [dict(zip(head, r.split("\t") + [""] * len(head))) for r in rows[1:]]


def _guard():
    if not GUARD.exists():
        return None
    spec = importlib.util.spec_from_file_location("prose_guard", GUARD)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


def texts_of(path: Path, raw: str):
    """Return [(line, text)] to check. Scene JSON gives every string under a "text" key, per language."""
    if path.suffix == ".json":
        out = []
        try:
            data = json.loads(raw)
        except ValueError:
            return [(1, raw)]

        def walk(node):
            if isinstance(node, dict):
                for k, v in node.items():
                    if k in ("text", "say", "title", "caption", "notes") and isinstance(v, (str, dict)):
                        if isinstance(v, str):
                            out.append((1, v))
                        else:
                            out.extend((1, s) for s in v.values() if isinstance(s, str))
                    else:
                        walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)
        walk(data)
        return out
    if path.suffix == ".html":                       # slide fragment: visible text, then the captions held in scripts
        body = re.sub(r"<(script|style)\b.*?</\1>", lambda m: "\n" * m.group(0).count("\n"), raw, flags=re.S | re.I)
        out = [(i, t) for i, l in enumerate(body.splitlines(), 1) if (t := re.sub(r"<[^>]+>", " ", l).strip())]
        out += [(raw.count("\n", 0, m.start()) + 1, m.group(2)) for m in CAP.finditer(raw)]
        return out
    clean = URL.sub(" ", INLINE.sub(" ", FENCE.sub(lambda m: "\n" * m.group(0).count("\n"), raw)))
    return [(i, l) for i, l in enumerate(clean.splitlines(), 1) if l.strip()]


def check(path: Path, raw: str, mode: str = "doc", technical: bool = False) -> list[dict]:
    strict = mode in STRICT_MODES
    out: list[dict] = []

    def add(line, sev, rule, msg, ex=""):
        out.append({"file": str(path), "line": line, "severity": sev, "rule": rule, "message": msg, "excerpt": ex[:160]})

    guard = _guard()
    if guard is not None and path.suffix != ".json":
        src = "\n".join(t for _, t in texts_of(path, raw)) if path.suffix == ".html" else raw
        for f in guard.check_text(src, set(getattr(guard, "GLOSSARY_FALLBACK", ()))):
            sev = "error" if strict and f["severity"] == "warn" and f["rule"].startswith("REF") else f["severity"]
            add(f["line"], sev, "EN-" + f["rule"], f["message"], f.get("excerpt", ""))

    refused_en = _lines(Path("refused.en.txt"))
    refused_ru = _lines(Path("refused.ru.txt"))
    terms = _tsv(Path("terms.en-ru.tsv"))
    angl = _tsv(Path("anglicisms.ru.tsv"))
    sev_word = "error" if strict else "warn"

    for ln, text in texts_of(path, raw):
        low = text.lower()
        for w in refused_en:
            if re.search(rf"\b{re.escape(w)}\b", low):
                add(ln, sev_word, "EN-REFUSED", f"refused word: {w}", text)
        for w in refused_ru:
            if re.search(rf"(?<![а-яё]){re.escape(w)}", low):
                add(ln, sev_word, "RU-REFUSED", f"refused word: {w}…", text)
        if guard is None and EN_CONTRAST.search(text):
            add(ln, "warn", "EN-CONTRAST", "\"X, not Y\" contrast: say what it is", text)
        if RU_CONTRAST.search(text):
            add(ln, "warn", "RU-CONTRAST", "«не X, а Y»: say what it is", text)
        if GENITIVE.search(low):
            add(ln, "warn", "RU-GENITIVE", "chain of four or more genitives", text)
        for row in angl:
            if re.search(rf"(?<![а-яё]){re.escape(row['anglicism'])}", low):
                add(ln, "warn", "RU-ANGL", f"use «{row['russian']}» for «{row['anglicism']}»", text)
        for row in terms:
            for col, lang in (("avoid_en", "en"), ("avoid_ru", "ru")):
                for bad in filter(None, row.get(col, "").split("|")):
                    if re.search(rf"(?<!\w){re.escape(bad.lower())}(?!\w)", low):
                        good = row["english" if lang == "en" else "russian"]
                        add(ln, sev_word, "TERM", f"use the term '{good}' for '{bad}'", text)
        for s in SENT.split(text.strip(" -*|\t")):
            s = s.strip()
            if CLIP_VERDICT.match(s) or CLIP_TAIL.match(s):
                add(ln, "error", "CLIPPED-STATUS", "bare verdict or tacked-on negative: say what was checked, what it shows and what remains", s)
        if mode == "slide" and STATUS_MARKS.search(text):
            add(ln, "error", "SLIDE-STATUS", "status or claim mark on a slide", text)
        if technical:
            for s in SENT.split(text):
                n = len(s.split())
                limit = 20 if IMPERATIVE_EN.match(s.strip()) else 25
                if n > limit:
                    add(ln, "warn", "STE-LEN", f"{n} words; ASD-STE100 limit is {limit}", s)

    if mode == "slide" and path.suffix == ".html":
        for m in HEADER.finditer(raw):
            head = " ".join(re.sub(r"<[^>]+>", " ", m.group(1)).split())
            if SLOGAN.search(head) or len(head.split()) > 5:
                add(raw.count("\n", 0, m.start()) + 1, "error", "SLIDE-HEADER", "a header names the thing, with no slogan, number or verb", head)
        for m in CAP.finditer(raw):
            if len(m.group(2).split()) > 15:
                add(raw.count("\n", 0, m.start()) + 1, "warn", "SLIDE-CAP", "caption over 15 words", m.group(2))

    if mode == "film" and path.suffix == ".json":
        words = {}
        try:
            data = json.loads(raw)
            for el in data.get("elements", []):
                t = el.get("text")
                if isinstance(t, dict):
                    for lang, s in t.items():
                        words[lang] = words.get(lang, 0) + len(str(s).split())
        except (ValueError, AttributeError):
            pass
        for lang, n in words.items():
            if n > 12:
                add(1, "error", "FILM-WORDS", f"{n} on-screen words in {lang}; the limit is 12", "")
    return out


def selftest():
    p = Path("t.md")
    assert any(f["rule"] == "EN-REFUSED" for f in check(p, "We leverage data.")), "English refused word"
    assert any(f["rule"] == "RU-REFUSED" for f in check(p, "Это уникальная платформа.")), "Russian refused word"
    assert any(f["rule"] == "RU-CONTRAST" for f in check(p, "Это не игрушка, а концепция.")), "Russian contrast"
    assert any(f["rule"] == "RU-ANGL" for f in check(p, "Мы собрали датасет.")), "Anglicism"
    assert any(f["rule"] == "TERM" and f["severity"] == "error"
               for f in check(p, "Our VHH binder works.", mode="slide")), "term on a slide is an error"
    assert any(f["rule"] == "SLIDE-STATUS" for f in check(p, "Preliminary design", mode="slide")), "status mark"
    long_cmd = "Run the script " + "with care " * 10 + "now."
    assert any(f["rule"] == "STE-LEN" for f in check(p, long_cmd, technical=True)), "STE length"
    scene = json.dumps({"elements": [{"text": {"en": " ".join(["word"] * 13), "ru": "три слова здесь"}}]})
    assert any(f["rule"] == "FILM-WORDS" for f in check(Path("s.json"), scene, mode="film")), "film words"
    assert not [f for f in check(p, "The model reads the sequence.") if f["severity"] == "error"], "clean text"
    frag = Path("o.html")
    assert any(f["rule"] == "SLIDE-HEADER" for f in check(frag, '<h2 class="big">Four lists become one</h2>', mode="slide")), "slogan header"
    assert any(f["rule"] == "SLIDE-HEADER" for f in check(frag, "<h2>A cycle prices every change</h2>", mode="slide")), "verb header"
    assert not [f for f in check(frag, "<h2>Sn\u00f6ggo</h2>", mode="slide") if f["rule"] == "SLIDE-HEADER"], "plain header"
    clip = lambda s: [f for f in check(p, s) if f["rule"] == "CLIPPED-STATUS"]
    for bad in ("The corpus is built and its check passes. No model is trained yet.", "And its check passes.", "Nothing is committed.",
                "Tests pass. Nothing deployed yet.", "\u041f\u0440\u043e\u0432\u0435\u0440\u043a\u0430 \u043f\u0440\u043e\u0448\u043b\u0430. \u041c\u043e\u0434\u0435\u043b\u044c \u043f\u043e\u043a\u0430 \u043d\u0435 \u043e\u0431\u0443\u0447\u0435\u043d\u0430.", "\u041d\u0438\u0447\u0435\u0433\u043e \u043d\u0435 \u0437\u0430\u043a\u043e\u043c\u043c\u0438\u0447\u0435\u043d\u043e."):
        assert clip(bad) and clip(bad)[0]["severity"] == "error", bad
    for good in ("The check found no leaks in any file, which means the data can stay on this machine.", "No model was trained, because the licence question is open.",
                 "The test passes when the file holds at least one row."):
        assert not clip(good), good
    print("lint_prose selftest: ok (guard %s)" % ("found" if _guard() else "missing"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="lint_prose")
    ap.add_argument("files", nargs="*", type=Path)
    ap.add_argument("--mode", choices=("chat", "doc", "slide", "film"), default="doc")
    ap.add_argument("--technical", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        selftest()
        return 0
    findings = []
    for f in a.files:
        findings += check(f, f.read_text(encoding="utf-8", errors="replace"), a.mode, a.technical)
    if a.json:
        print(json.dumps(findings, ensure_ascii=False, indent=1))
    else:
        for f in findings:
            print(f"{f['file']}:{f['line']}: {f['severity']} {f['rule']}: {f['message']}")
    return 1 if any(f["severity"] == "error" for f in findings) else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
