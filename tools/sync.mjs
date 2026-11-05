// Install the shared rules into every agent (plan part 2.1).
//   node tools/sync.mjs            same as --check
//   node tools/sync.mjs --check    report which targets differ from arc-harness (exit 1 if any)
//   node tools/sync.mjs --apply    back up each changed target to ~/.arc-harness-backup/<stamp>/, then write it
//
// Only the block between the markers is ours in each instruction file; the rest of the file is left as it is.
// Hook entries are merged into ~/.claude/settings.json; other entries are never removed.
// The two Antigravity connector files are made identical (config/ is the newer, fuller one). They hold keys, so
// their content is never printed and never copied into this folder. ~/.zcode/cli/config.json is never opened.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const HOME = os.homedir();
const BEGIN = '<!-- arc-harness:begin (managed by arc-harness/tools/sync.mjs; edit the source there) -->';
const END = '<!-- arc-harness:end -->';
const NODE = '"C:/Program Files/nodejs/node.exe"';
const HOOK = `${NODE} "${path.join(HOME, '.claude', 'hooks', 'arc-rules.mjs').replace(/\\/g, '/')}"`;

const rules = ['common.md', 'routing.md'].map((f) => fs.readFileSync(path.join(ROOT, 'shared', 'rules', f), 'utf8').trim()).join('\n\n');
const block = `${BEGIN}\n${rules}\n${END}`;

export function withBlock(text, blk) {
  const i = text.indexOf('<!-- arc-harness:begin');
  const j = text.indexOf(END);
  if (i >= 0 && j > i) return text.slice(0, i) + blk + text.slice(j + END.length);
  return text.replace(/\s*$/, '\n\n') + blk + '\n';
}

export function withHooks(settings) {
  const s = structuredClone(settings);
  s.hooks = s.hooks || {};
  const want = { SessionStart: null, UserPromptSubmit: null, PreToolUse: 'Agent|Task|Workflow|mcp__ccd_session__start_session', PostToolUse: 'Write|Edit', Stop: null };
  for (const [event, matcher] of Object.entries(want)) {
    const list = (s.hooks[event] = s.hooks[event] || []);
    if (list.some((g) => (g.hooks || []).some((h) => String(h.command).includes('arc-rules.mjs')))) continue;
    list.push({ ...(matcher ? { matcher } : {}), hooks: [{ type: 'command', command: HOOK, timeout: event === 'PostToolUse' ? 6 : 5 }] });
  }
  return s;
}

const read = (f) => { try { return fs.readFileSync(f, 'utf8'); } catch { return null; } };
const targets = [
  ...[
    path.join(HOME, '.claude', 'CLAUDE.md'),
    path.join(HOME, '.gemini', 'GEMINI.md'),
    path.join(HOME, '.zcode', 'AGENTS.md'),
    path.join(HOME, '.codex', 'AGENTS.md'),
  ].map((file) => ({ file, secret: false, next: (cur) => withBlock(cur ?? '', block) })),
  { file: path.join(HOME, '.claude', 'hooks', 'arc-rules.mjs'), secret: false,
    next: () => read(path.join(ROOT, 'surfaces', 'claude-code', 'hooks', 'arc-rules.mjs')) },
  { file: path.join(HOME, '.claude', 'settings.json'), secret: true,
    next: (cur) => JSON.stringify(withHooks(JSON.parse(cur || '{}')), null, 2) + '\n' },
  { file: path.join(HOME, '.gemini', 'antigravity', 'mcp_config.json'), secret: true,
    next: () => read(path.join(HOME, '.gemini', 'config', 'mcp_config.json')) },
];

function main(argv) {
  const apply = argv.includes('--apply');
  const stamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
  const backup = path.join(HOME, '.arc-harness-backup', stamp);
  let drift = 0;
  for (const t of targets) {
    if (t.file.includes(path.join('.zcode', 'cli'))) continue;          // never touched
    const cur = read(t.file);
    const nxt = t.next(cur);
    if (nxt == null) { console.log(`skip    ${t.file} (source missing)`); continue; }
    if (cur === nxt) { console.log(`same    ${t.file}`); continue; }
    drift++;
    if (!apply) { console.log(`differs ${t.file}`); continue; }
    if (cur != null) {
      const dst = path.join(backup, t.file.replace(HOME, '').replace(/^[\\/]/, ''));
      fs.mkdirSync(path.dirname(dst), { recursive: true });
      fs.copyFileSync(t.file, dst);
    }
    fs.mkdirSync(path.dirname(t.file), { recursive: true });
    fs.writeFileSync(t.file, nxt);
    console.log(`written ${t.file}`);
  }
  if (apply && drift) console.log(`backup  ${backup}`);
  return apply ? 0 : drift ? 1 : 0;
}

function selftest() {
  const a = withBlock('# Title\n', '<!-- arc-harness:begin X -->\nR\n<!-- arc-harness:end -->');
  const b = withBlock(a.replace('R', 'old'), `${BEGIN}\nnew\n${END}`);
  if (!a.includes('R') || !b.includes('new') || b.includes('old') || !b.startsWith('# Title')) throw new Error('withBlock');
  const h = withHooks(withHooks({ hooks: { Stop: [{ hooks: [{ type: 'command', command: 'x' }] }] } }));
  if (h.hooks.Stop.length !== 2 || h.hooks.PostToolUse[0].matcher !== 'Write|Edit') throw new Error('withHooks');
  console.log('sync selftest: ok');
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  if (process.argv.includes('--selftest')) selftest();
  else process.exitCode = main(process.argv.slice(2));
}
