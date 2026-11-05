#!/usr/bin/env python3
# version: w02 | date: 2026-10-30
"""One live file per artifact. Old content goes into one compressed archive per workflow folder.

  version.py bump <file|dir> [--from SRC] [--ver V] [--date D] [--root R]
        Archive the old content, write the new content, update the header.
        A dir (a workflow root) with --ver w03 also renames the folder and its name inside the files.
        A dir bump is all-or-nothing as far as the files can tell: everything is read and checked first.
  version.py restore <file> <version|sha-prefix|list> [--to PATH]
        Put an archived version back. In place: the current content is archived first and the header
        is set to the folder version. --to PATH writes the exact archived bytes to a NEW path.
  version.py check <dir>      Report duplicate files, forbidden names, leftover .tmp/.lock files and header
                              problems. Reads only. Deletes nothing.
  version.py ingest <root> <src-dir> <label> [--skip NAME ...]
        Copy existing files into the archive of <root> under <label>. Deletes nothing.
  version.py verify <dir>     Re-hash every archived file and compare with the manifest.
  version.py selftest

Scope: files inside a workflow folder that holds an _archive.tar.xz. Files in a git repository use git.
Archive: <root>/_archive.tar.xz = manifest.json + blobs/<sha256>. xz preset 9 + extreme. Same content is stored once.
Writes take an exclusive lock (<root>/_archive.lock) and use a unique temporary file.
Header: a line that holds 'version: <v> | date: <YYYY-MM-DD>' (a "_header" key in JSON objects).
Only UTF-8 files are touched. A BOM and CRLF line ends are kept. Standard library only. Output is ASCII.
"""
import argparse, contextlib, datetime as dt, hashlib, io, json, lzma, os, re, sys, tarfile, tempfile, time
from pathlib import Path

ARCHIVE = '_archive.tar.xz'
LOCK = '_archive.lock'
HDR = re.compile(r'version:\s*(\S+?)\s*\|\s*date:\s*(\d{4}-\d{2}-\d{2})')
BAD = re.compile(r'(-v\d+(\.\d+)*|[_. ]v\d+|[-_ ]?final|[-_ ]?copy|[-_ ]new|[-_ ]draft|[-_ ]old|[-_ ]backup|'
                 r'\.bak|\.orig|\.old|\.tmp|\.lock|\(\d+\)|~)(\.[^.]*)?$|^copy of |^v\d+$', re.I)
SKIP = {'.git', 'node_modules', 'ledger', '__pycache__'}
TEXT = {'.md', '.json', '.ps1', '.mjs', '.js', '.py', '.html'}
FRONT = re.compile(r'---[ \t]*\r?\n.*?\r?\n---[ \t]*\r?\n', re.S)
sha = lambda b: hashlib.sha256(b).hexdigest()
today = lambda: dt.date.today().isoformat()


def die(msg):
    print('error: ' + msg)
    sys.exit(2)


def gentle():
    """Low priority: a long job must not slow the machine."""
    try:
        if os.name == 'nt':
            import ctypes
            ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
        else:
            os.nice(10)
    except Exception:
        pass


@contextlib.contextmanager
def locked(root, wait=60):
    lk = root / LOCK
    end = time.time() + wait
    while True:
        try:
            os.close(os.open(lk, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            break
        except FileExistsError:
            if time.time() > end:
                die('archive is locked (%s). Remove the lock file only when no bump is running.' % lk)
            time.sleep(0.2)
    try:
        yield
    finally:
        if lk.exists():
            lk.unlink()


def load(root):
    man, blobs = {'format': 1, 'entries': []}, {}
    if (root / ARCHIVE).exists():
        with tarfile.open(root / ARCHIVE, 'r:xz') as t:
            for m in t:
                data = t.extractfile(m).read()
                if m.name == 'manifest.json':
                    man = json.loads(data)
                else:
                    blobs[m.name.split('/')[-1]] = data
    return man, blobs


def save(root, man, blobs):
    fd, tmp = tempfile.mkstemp(dir=root, suffix='.tmp')   # unique name: two writers never share a temp file
    os.close(fd)
    def add(t, name, data):
        ti = tarfile.TarInfo(name)
        ti.size = len(data)
        t.addfile(ti, io.BytesIO(data))
    try:
        with tarfile.open(tmp, 'w:xz', preset=9 | lzma.PRESET_EXTREME) as t:
            add(t, 'manifest.json', json.dumps(man, indent=1).encode())
            for h in sorted(blobs):
                add(t, 'blobs/' + h, blobs[h])
        os.replace(tmp, root / ARCHIVE)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def keep(man, blobs, rel, ver, date, data):
    h = sha(data)
    if not any(e['path'] == rel and e['version'] == ver and e['sha256'] == h for e in man['entries']):
        man['entries'].append(dict(path=rel, version=ver, date=date, sha256=h, size=len(data), archived=today()))
    blobs.setdefault(h, data)


def find_root(f, given=None):
    if given:
        root = Path(given).resolve()
        try:
            f.relative_to(root)
        except ValueError:
            die('%s is not inside the root %s' % (f, root))
        return root
    for p in [f] + list(f.parents):
        if (p / ARCHIVE).exists():
            return p
    die('no %s above %s. This tool serves workflow folders only; files in a git repository use git.' % (ARCHIVE, f))


def decode(data, f):
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError:
        die('%s is not UTF-8; not touched' % f)


def stamp(p, text, ver, date):
    line = 'version: %s | date: %s' % (ver, date)
    bom = text.startswith('﻿')
    if bom:
        text = text[1:]
    nl = '\r\n' if '\r\n' in text else '\n'
    ext = p.suffix.lower()
    if HDR.search(text[:600]):
        out = HDR.sub(line, text[:600], count=1) + text[600:]
    elif ext in ('.md', '.html'):
        m = FRONT.match(text)                       # keep YAML front matter first
        cut = m.end() if m else 0
        out = text[:cut] + '<!-- %s -->%s' % (line, nl) + text[cut:]
    elif ext in ('.ps1', '.py', '.mjs', '.js'):
        c = '#' if ext in ('.ps1', '.py') else '//'
        head, rest = '', text
        if text.startswith('#!'):                   # keep the shebang first
            i = text.find('\n') + 1 or len(text)
            head, rest = text[:i], text[i:]
        out = '%s%s %s%s%s' % (head, c, line, nl, rest)
    elif ext == '.json':
        try:
            obj = json.loads(text)
        except ValueError:
            return ('﻿' if bom else '') + text
        if not isinstance(obj, dict):               # arrays stay as they are: a key would change the data
            return ('﻿' if bom else '') + text
        if not obj:
            out = '{%s  "_header": "%s"%s}%s' % (nl, line, nl, nl)
        else:
            out = text.replace('{', '{%s  "_header": "%s",' % (nl, line), 1)
    else:
        out = text
    return ('﻿' if bom else '') + out


def norm(text):
    """The text with the header date blanked, so a new day alone does not make a new version."""
    return HDR.sub(lambda m: 'version: %s | date: -' % m.group(1), text[:600], count=1) + text[600:]


def old_header(text, f):
    m = HDR.search(text[:600])
    return (m.group(1), m.group(2)) if m else ('unversioned', dt.date.fromtimestamp(f.stat().st_mtime).isoformat())


def write_atomic(f, data):
    fd, tmp = tempfile.mkstemp(dir=f.parent, suffix='.tmp')
    os.close(fd)
    try:
        Path(tmp).write_bytes(data)
        os.replace(tmp, f)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def bump_many(root, items, ver, date):
    """items: [(file, new_text or None)]. One lock, one archive load, one archive save."""
    with locked(root):
        man, blobs = load(root)
        todo = []
        for f, new_text in items:
            old = f.read_bytes()
            oldtext = decode(old, f)
            oldver, olddate = old_header(oldtext, f)
            newver = ver or oldver
            new = stamp(f, new_text if new_text is not None else oldtext, newver, date or today())
            if norm(new) == norm(oldtext):
                continue
            keep(man, blobs, f.relative_to(root).as_posix(), oldver, olddate, old)   # archive first
            todo.append((f, new.encode('utf-8')))
        if todo:
            save(root, man, blobs)
            for f, data in todo:                                                     # then overwrite
                write_atomic(f, data)
        return len(todo)


def cmd_bump(a):
    f = Path(a.path).resolve()
    root = find_root(f, a.root)
    if f.is_dir():
        if root != f:
            die('bump a folder only from its own root (the folder that holds %s)' % ARCHIVE)
        newname = re.sub(r'^w\d+', a.ver, f.name) if a.ver and re.match(r'w\d+$', a.ver) else f.name
        if newname != f.name and f.with_name(newname).exists():
            die('the folder %s already exists' % newname)
        plan = []
        for p in sorted(f.rglob('*')):                  # read and check everything before any change
            if p.is_file() and p.suffix.lower() in TEXT and not (set(p.relative_to(f).parts) & SKIP):
                plan.append((p.relative_to(f), decode(p.read_bytes(), p).replace(f.name, newname)))
        if newname != f.name:
            os.rename(f, f.with_name(newname))          # fails here, on Windows, if the folder is in use: nothing changed
            f = f.with_name(newname)
        n = bump_many(f, [(f / rel, text) for rel, text in plan], a.ver, a.date)
        print('bumped %d file(s)%s' % (n, '; folder is now ' + newname if newname != f.name or newname else ''))
        return
    new = decode(Path(a.src).read_bytes(), Path(a.src)) if a.src else None
    print('bumped' if bump_many(root, [(f, new)], a.ver, a.date) else 'unchanged')


def cmd_restore(a):
    f = Path(a.path).resolve()
    root = find_root(f, a.root)
    rel = f.relative_to(root).as_posix()
    with locked(root):
        man, blobs = load(root)
        mine = [e for e in man['entries'] if e['path'] == rel]
        if a.version == 'list':
            for e in mine:
                print('%-10s %s %s %6d bytes' % (e['version'], e['date'], e['sha256'][:12], e['size']))
            return
        hit = [e for e in mine if e['version'] == a.version or e['sha256'].startswith(a.version)]
        if not hit:
            die('no archived version %r of %s (try: restore %s list)' % (a.version, rel, rel))
        data = blobs[hit[-1]['sha256']]
        if a.to:                                        # exact bytes, to a new path only: no overwrite, no sibling of a live file
            dst = Path(a.to).resolve()
            if dst.exists():
                die('%s exists; --to needs an unused path' % dst)
            dst.parent.mkdir(parents=True, exist_ok=True)
            write_atomic(dst, data)
            print('restored %s %s to %s' % (rel, hit[-1]['version'], dst))
            return
        m = re.match(r'(w\d+)-', root.name)
        text = decode(data, f)
        if f.suffix.lower() in TEXT and m:             # the live file must carry the folder version
            text = stamp(f, text, m.group(1), today())
        if f.exists():
            cur = f.read_bytes()
            if norm(decode(cur, f)) == norm(text):
                print('already restored: %s %s' % (rel, hit[-1]['version']))
                return
            v, d = old_header(decode(cur, f), f)
            keep(man, blobs, rel, v, d, cur)
            save(root, man, blobs)
        write_atomic(f, text.encode('utf-8'))
        print('restored %s %s in place' % (rel, hit[-1]['version']))


def cmd_ingest(a):
    root, src = Path(a.root).resolve(), Path(a.src).resolve()
    with locked(root):
        man, blobs = load(root)
        n = 0
        for p in sorted(src.rglob('*')):
            parts = set(p.relative_to(src).parts)
            if p.is_file() and p.name not in (ARCHIVE, LOCK) and not parts & set(a.skip or []):
                keep(man, blobs, p.relative_to(src).as_posix(), a.label,
                     dt.date.fromtimestamp(p.stat().st_mtime).isoformat(), p.read_bytes())
                n += 1
        save(root, man, blobs)
    print('ingested %d file(s) as %s; archive holds %d blob(s)' % (n, a.label, len(blobs)))


def cmd_verify(a):
    root = Path(a.path).resolve()
    man, blobs = load(root)
    bad = [h for h, d in blobs.items() if sha(d) != h]
    missing = [e['path'] for e in man['entries'] if e['sha256'] not in blobs]
    print('%d entries, %d blobs, %d bad hash, %d missing' % (len(man['entries']), len(blobs), len(bad), len(missing)))
    sys.exit(1 if bad or missing else 0)


def cmd_check(a):
    top = Path(a.path).resolve()
    seen, bad, hdr = {}, [], []
    for dp, dn, fn in os.walk(top):
        dn[:] = [d for d in dn if d not in SKIP]
        for name in dn + fn:
            if BAD.search(name):
                bad.append(str(Path(dp, name).relative_to(top)))
        for name in fn:
            p = Path(dp, name)
            if name in (ARCHIVE, LOCK) or p.stat().st_size == 0:
                continue
            seen.setdefault(sha(p.read_bytes()), []).append(str(p.relative_to(top)))
            wf = next((re.match(r'(w\d+)-', x).group(1) for x in p.relative_to(top.parent).parts + (top.name,)
                       if re.match(r'w\d+-', x)), None) if p.suffix.lower() in TEXT else None
            if wf and p.suffix.lower() == '.json':          # a JSON array cannot carry a header key
                try:
                    wf = wf if isinstance(json.loads(p.read_text('utf-8-sig')), dict) else None
                except ValueError:
                    pass
            if wf:
                m = HDR.search(p.read_text('utf-8', 'replace')[:600])
                if not m or m.group(1) != wf:
                    hdr.append('%s (header %s, folder %s)' % (p.relative_to(top), m.group(1) if m else 'missing', wf))
    dups = [v for v in seen.values() if len(v) > 1]
    for g in dups:
        print('duplicate content: ' + ' == '.join(g))
    for b in bad:
        print('forbidden name: ' + b)
    for h in hdr:
        print('header mismatch: ' + h)
    print('check %s: %d duplicate group(s), %d forbidden name(s), %d header problem(s)' % (top, len(dups), len(bad), len(hdr)))
    sys.exit(1 if dups or bad or hdr else 0)


def selftest():
    import threading
    ns = lambda **k: argparse.Namespace(**{'root': None, 'ver': 'w09', 'date': '2026-10-30', 'src': None, 'to': None, **k})
    with tempfile.TemporaryDirectory() as d:
        root = Path(d, 'w09-demo')
        (root / 'sub').mkdir(parents=True)
        f, j, e, s, arr = root / 'a.md', root / 'sub' / 'k.json', root / 'e.json', root / 's.mjs', root / 'arr.json'
        f.write_text('one\n')
        j.write_text('{\n  "x": 1\n}\n')
        e.write_text('{}')
        arr.write_text('[{"a": 1}]')
        s.write_text('#!/usr/bin/env node\nconsole.log(1)\n')
        crlf = root / 'c.md'
        crlf.write_bytes(b'\xef\xbb\xbfline1\r\nline2\r\n')
        old = Path(d, 'old')
        old.mkdir()
        (old / 'a.md').write_text('zero\n')
        cmd_ingest(argparse.Namespace(root=str(root), src=str(old), label='v0.1', skip=[]))
        for x in (f, j, e, s, arr, crlf):
            cmd_bump(ns(path=str(x)))
        assert f.read_text().startswith('<!-- version: w09'), 'md header'
        assert json.loads(j.read_text())['_header'].startswith('version: w09'), 'json header'
        assert json.loads(e.read_text()) == {'_header': 'version: w09 | date: 2026-10-30'}, 'empty object'
        assert arr.read_text() == '[{"a": 1}]', 'array json is left alone'
        assert s.read_text().startswith('#!/usr/bin/env node\n// version: w09'), 'shebang stays first'
        raw = crlf.read_bytes()
        assert raw.startswith(b'\xef\xbb\xbf<!-- version') and b'\r\n' in raw and b'\n' not in raw.replace(b'\r\n', b''), 'BOM and CRLF kept'
        before = f.read_bytes()
        cmd_bump(ns(path=str(f)))
        cmd_bump(ns(path=str(f), date='2026-11-01'))
        assert f.read_bytes() == before, 'same content on a later day makes no new version'
        new = Path(d, 'new.md')
        new.write_text('two\n')
        cmd_bump(ns(path=str(f), src=str(new)))
        man, blobs = load(root)
        assert len([x for x in man['entries'] if x['path'] == 'a.md']) == 3, 'v0.1 + unversioned + w09 archived'
        cmd_restore(ns(path=str(f), version='v0.1'))
        assert f.read_text().endswith('zero\n') and f.read_text().startswith('<!-- version: w09'), 'in-place restore carries the folder header'
        n = len(load(root)[1])
        cmd_restore(ns(path=str(f), version='v0.1'))
        assert len(load(root)[1]) == n, 'same content is stored once'
        out = Path(d, 'out.md')
        cmd_restore(ns(path=str(f), version='v0.1', to=str(out)))
        assert out.read_text() == 'zero\n', '--to is exact'
        try:
            cmd_restore(ns(path=str(f), version='v0.1', to=str(out)))
            raise AssertionError('--to must refuse an existing path')
        except SystemExit as ex:
            assert ex.code == 2
        # two bumps at the same time must both reach the archive
        p1, p2 = root / 'p1.md', root / 'p2.md'
        p1.write_text('P1 old\n'); p2.write_text('P2 old\n')
        for x in (p1, p2):
            cmd_bump(ns(path=str(x)))
        n1, n2 = Path(d, 'n1.md'), Path(d, 'n2.md')
        n1.write_text('P1 new\n'); n2.write_text('P2 new\n')
        ts = [threading.Thread(target=cmd_bump, args=(ns(path=str(x), src=str(y)),)) for x, y in ((p1, n1), (p2, n2))]
        [t.start() for t in ts]; [t.join() for t in ts]
        olds = [b for b in load(root)[1].values() if b.startswith(b'<!-- version') and b'old' in b]
        assert len(olds) == 2 and not list(root.glob('*.tmp')) and not (root / LOCK).exists(), 'concurrent bumps lose nothing'
        (root / 'b-v2.md').write_text('zero\n')
        try:
            cmd_check(ns(path=str(root)))
            raise AssertionError('check must find the duplicate and the forbidden name')
        except SystemExit as ex:
            assert ex.code == 1
        for bad in ('x - Copy.md', 'x_v2.md', 'x-v2.1.md', 'x-draft.md', 'x.md.tmp', 'x-final.md', 'x (1).md'):
            assert BAD.search(bad), bad
        for good in ('policy.md', 'review-plan.md', 'tasks.json', 'R0_local_survey.md', '_archive.tar.xz'):
            assert not BAD.search(good), good
        try:
            cmd_verify(ns(path=str(root)))
        except SystemExit as ex:
            assert ex.code == 0, 'verify'
    print('version.py selftest: ok')


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    s = p.add_subparsers(dest='cmd', required=True)
    b = s.add_parser('bump'); b.add_argument('path'); b.add_argument('--from', dest='src'); b.add_argument('--ver'); b.add_argument('--date'); b.add_argument('--root')
    r = s.add_parser('restore'); r.add_argument('path'); r.add_argument('version'); r.add_argument('--to'); r.add_argument('--root')
    c = s.add_parser('check'); c.add_argument('path')
    i = s.add_parser('ingest'); i.add_argument('root'); i.add_argument('src'); i.add_argument('label'); i.add_argument('--skip', nargs='*')
    v = s.add_parser('verify'); v.add_argument('path')
    s.add_parser('selftest')
    a = p.parse_args()
    gentle()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    {'bump': cmd_bump, 'restore': cmd_restore, 'check': cmd_check, 'ingest': cmd_ingest, 'verify': cmd_verify,
     'selftest': lambda _: selftest()}[a.cmd](a)


if __name__ == '__main__':
    main()
