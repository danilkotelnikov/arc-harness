// version: 1 | date: 2026-11-05
// Daily reconcile. Reads local files and read-only git, compares them with yesterday's record, and writes
// ~/.cammut/daily.json. The arc-rules hook runs this once a day and shows the changes on the first prompt.
// It reads no secret, calls no Cammut write, and never stops on a missing source: a missing source is a row.
//   node tools/daily_reconcile.mjs              write today's record
//   node tools/daily_reconcile.mjs --selftest   run the checks on a throwaway folder
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const firstMatch = (file, re) => {
  try { return fs.readFileSync(file, 'utf8').slice(0, 4096).match(re)?.[1] ?? 'no version line'; } catch { return 'missing'; }
};
const git = (dir, ...args) => {
  const r = spawnSync('git', ['-C', dir, ...args], { encoding: 'utf8', timeout: 8000, windowsHide: true });
  return r.status === 0 ? r.stdout.trim() : 'missing';
};
const lines = (file) => { try { return fs.readFileSync(file, 'utf8').split('\n').filter(Boolean).length; } catch { return -1; } };

export function readState(h) {
  const arc = path.join(h, 'arc-harness');
  const sci = path.join(h, 'private', 'private', 'Arc Science');
  const policy = /\bversion (\d+), \d{4}-\d{2}-\d{2}/;
  const header = /version: (\S+) \| date: (?:\S+)/;
  const hooks = (() => { try { return fs.readdirSync(path.join(h, '.claude', 'hooks')).filter((f) => /\.(mjs|py)$/.test(f)).sort().join(' '); } catch { return 'missing'; } })();
  return {
    'policy source': firstMatch(path.join(arc, 'shared', 'rules', 'common.md'), policy),
    'policy Claude Code': firstMatch2(path.join(h, '.claude', 'CLAUDE.md'), /Working policy[^\n]*version (\d+),/),
    'policy Codex': firstMatch2(path.join(h, '.codex', 'AGENTS.md'), /Working policy[^\n]*version (\d+),/),
    'policy Gemini': firstMatch2(path.join(h, '.gemini', 'GEMINI.md'), /Working policy[^\n]*version (\d+),/),
    'policy GLM': firstMatch2(path.join(h, '.zcode', 'AGENTS.md'), /Working policy[^\n]*version (\d+),/),
    'hook files': hooks,
    'road map': firstMatch(path.join(sci, 'concept', 'roadmap.md'), header),
    'Cammut spec lines': String(lines(path.join(arc, 'workflows', 'w04-cammut', 'SPEC.md'))),
    'Cammut code': git(path.join(h, 'Documents', 'Cammut'), 'log', '-1', '--all', '--format=%h %cs'),
    'Arc Science main': git(path.join(sci, 'Arc-Science'), 'log', '-1', 'origin/main', '--format=%h %cs'),
    'Molecullage main': git(path.join(h, 'Molecullage'), 'log', '-1', '--all', '--format=%h %cs'),
    'gate rows': String(lines(path.join(arc, 'gates', 'gates.jsonl'))),
    'lesson candidates': String(lines(path.join(arc, 'lessons', 'inbox.jsonl'))),
  };
}
function firstMatch2(file, re) {
  try { return fs.readFileSync(file, 'utf8').match(re)?.[1] ?? 'no version line'; } catch { return 'missing'; }
}

export function compare(prev, now) {
  const changed = [];
  for (const [k, v] of Object.entries(now)) if (prev && prev[k] !== undefined && prev[k] !== v) changed.push(`${k}: ${prev[k]} -> ${v}`);
  const installed = ['policy Claude Code', 'policy Codex', 'policy Gemini', 'policy GLM'];
  const behind = installed.filter((k) => now[k] !== now['policy source']);
  const problems = behind.map((k) => `${k} is ${now[k]}, source is ${now['policy source']}`);
  for (const [k, v] of Object.entries(now)) if (v === 'missing') problems.push(`${k} is missing`);
  return { changed, problems };
}

export const render = (r) => {
  if (!r.changed.length && !r.problems.length) return '';
  const parts = [];
  if (r.changed.length) parts.push('Changed since the last daily check: ' + r.changed.join('; ') + '.');
  if (r.problems.length) parts.push('Out of step: ' + r.problems.join('; ') + '.');
  return 'DAILY RECONCILE. ' + parts.join(' ') + ' The state cards in Cammut hold the memory state: read them before a wide recall.';
};

export function run(h, day) {
  const file = path.join(h, '.cammut', 'daily.json');
  let old = null;
  try { old = JSON.parse(fs.readFileSync(file, 'utf8')); } catch {}
  if (old?.day === day) return old;
  const state = readState(h);
  const found = compare(old?.state, state);
  const rec = { day, shown: [], notice: render(found), state, ...found };
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify(rec, null, 1));
  return rec;
}

function selftest() {
  const assert = (ok, m) => { if (!ok) { console.error('FAIL ' + m); process.exit(1); } };
  const a = { 'policy source': '6', 'policy Claude Code': '6', 'policy Codex': '5', 'policy Gemini': '6', 'policy GLM': '6', 'gate rows': '10' };
  const r = compare({ ...a, 'gate rows': '9' }, a);
  assert(r.changed.length === 1 && r.changed[0] === 'gate rows: 9 -> 10', 'change is found');
  assert(r.problems.length === 1 && r.problems[0].includes('Codex'), 'an agent behind the source is found');
  assert(compare(null, { ...a, 'policy Codex': '6' }).changed.length === 0, 'the first day shows no change');
  assert(render({ changed: [], problems: [] }) === '', 'a quiet day prints nothing');
  assert(render(r).startsWith('DAILY RECONCILE.'), 'render');
  const h = fs.mkdtempSync(path.join(os.tmpdir(), 'daily-'));
  const one = run(h, '2026-11-05');
  assert(one.problems.some((p) => p.includes('hook files')), 'a missing source is a row and the run goes on');
  assert(run(h, '2026-11-05').day === '2026-11-05' && !fs.readdirSync(h).includes('x'), 'a second run the same day returns the record');
  const raw = fs.readFileSync(path.join(h, '.cammut', 'daily.json'), 'utf8');
  assert(!/Bearer|api[_-]?key|BEGIN [A-Z ]*KEY/i.test(raw), 'the record holds no secret words');
  fs.rmSync(h, { recursive: true, force: true });
  console.log('daily_reconcile selftest: PASS');
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  if (process.argv.includes('--selftest')) selftest();
  else { const r = run(os.homedir(), new Date().toISOString().slice(0, 10)); console.log(render(r) || 'nothing changed'); }
}
