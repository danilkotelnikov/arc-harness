// Shared working rules from arc-harness (the owner, 2026-10-26). Plan: ~/.claude/plans/hazy-honking-lollipop.md, part 2.2.
// The rule text lives in ~/arc-harness/shared (reminders.json); this hook only delivers it.
//
// SessionStart     -> refreshes the local lessons cache in a detached child (the session never waits for the network).
// UserPromptSubmit -> adds "ask before proposing" and the house style every turn; on a new task also "research first"
//                     plus the top lessons from the cache. Local files only, 200 ms limit.
//                     Prompt intake (the owner, 2026-10-30): every prompt gets a tag (D-YYYYMMDD-NN) and a mechanical task
//                     list (T1, T2 ...), logged to arc-harness/intake/ledger.jsonl (redacted, 160 characters a task).
// PostToolUse      -> after Write or Edit of a .md, .txt or scene .json file, runs the word checker and reports findings.
//                     It never blocks.
// Stop             -> writes candidate lessons (failed tool calls, rejected calls, the owner's corrections) to
//                     arc-harness/lessons/inbox.jsonl. Then the intake check: it blocks the reply once if a task of
//                     the tagged prompt has no outcome, or if a changed workflow folder fails `version.py check`.
//                     A turn that ends with a question to the owner is not blocked.
//                     Focus (the owner, 2026-11-05, policy 15): a task without a finished outcome moves to the next prompt.
//                     Each missed outcome and each plan step without a T number adds one distraction point.
// PreToolUse       -> at three points it refuses new subagents, workflows and sessions.
// Disable: remove the arc-rules entries in ~/.claude/settings.json.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const HARNESS = process.env.ARC_HARNESS || path.join(os.homedir(), 'arc-harness');
const CACHE = path.join(os.homedir(), '.cammut', 'lessons-cache.json');
const DAILY = path.join(os.homedir(), '.cammut', 'daily.json');   // written by tools/daily_reconcile.mjs
const STATE = path.join(os.homedir(), '.claude', 'hooks', 'state');
const SM = path.join(os.homedir(), '.claude', 'scripts', 'shared-memory', 'common.mjs');
const readJson = (f, d) => { try { return JSON.parse(fs.readFileSync(f, 'utf8')); } catch { return d; } };
const out = (o) => process.stdout.write(JSON.stringify(o));

// A prompt that starts a task: the first prompt of a session, or a longer prompt with an action word.
const ACTION = /\b(make|build|create|write|fix|add|plan|design|develop|implement|render|draw|research|find|review|refactor|integrate|configure|set up|remake|prepare|update|run|start)\b|сдела|созда|напиш|исправ|добав|разработ|настро|подготов|обнов|запуст/i;
export function isNewTask(prompt, first) {
  const p = String(prompt || '').trim();
  if (!p) return false;
  return first || (p.length > 60 && ACTION.test(p));
}

// ---- prompt intake. The split is mechanical; the reminder asks the agent to fix the list.
const ASK = /\b(i want|i need|i'?d like|must(n'?t)?|should(n'?t)?|shall|have to|need to|let'?s|please|make sure|do not|don'?t|never|always|delete|remove|overwrite|rewrite|check|stop|tag|parse|follow)\b|нужно|надо|должн|хочу|удали|проверь/i;
const GAP = /\b(i (do not|don'?t) see|missing|lacks?|there (is|are) no|what about|how about)\b|не вижу|нет\s|а как же|что насч[её]т/i;
const IDEA = /\b(we can|we could|could we|what if|maybe|perhaps|i think|i believe|idea|instead|might)\b|можно|может быть|идея|вместо/i;
// What is left of a prompt once code fences and system blocks are removed: pasted text, reminders, task, session and
// agent notices. Empty for a notice the app or a helper agent sends, which is not the owner's prompt. Only these known tags
// are removed, so HTML that the owner pastes stays (Argon review, 2026-10-30).
const SYS = /<(system-reminder|task-notification|side-sessions-event|ci-monitor-event|agent-message|pasted_content|(?:local-)?command-[\w-]+)(\s[^>]*)?>[\s\S]*?<\/\1[^>]*>/g;
export const humanText = (p) => String(p || '')
  .replace(/```[\s\S]*?```/g, ' ')
  .replace(SYS, ' ')
  .replace(/\[SYSTEM NOTIFICATION[\s\S]*$/, ' ')
  .trim();

// A long prompt is a list of critiques, so every sentence counts and keeps the slide it is about.
// (D-20261104-25: a cap of 12 and the ASK filter merged about 40 requests into 12.)
export const LONG = 800;
const SENTENCE = /(?<=[.!?])\s+(?=[A-ZА-ЯЁ"«(])/;
function longTasks(lines) {
  const found = [];
  for (const line of lines) {
    let scope = '';
    for (let s of line.split(SENTENCE)) {
      s = s.trim().replace(/^(\d+[.)]|[-*•])\s+/, '');
      const m = /^(Slides?\s+\d+(?:\s*(?:and|,|-)\s*\d+)*)\b[.:]?\s*(.*)$/i.exec(s);
      if (m) { scope = m[1]; s = m[2]; }
      if (s.split(/\s+/).length >= 3) found.push(scope ? `${scope}: ${s}` : s);
    }
  }
  return found;
}

export function parseTasks(prompt) {
  const text = humanText(prompt);
  const lines = text.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
  if (text.length >= LONG) return longTasks(lines).slice(0, 100).map((s, i) => ({ n: i + 1, kind: IDEA.test(s) ? 'idea' : 'do', text: s.slice(0, 160) }));
  const items = lines.filter((l) => /^(\d+[.)]|[-*•])\s+/.test(l));
  const rest = lines.filter((l) => !items.includes(l)).join(' ').split(/(?<=[.!?])\s+(?=[A-ZА-ЯЁ"«(])/);   // "e.g. policy.mjs" stays whole
  const keep = (s) => /\?\s*$/.test(s) || (s.split(/\s+/).length >= 4 && (ASK.test(s) || GAP.test(s) || IDEA.test(s) || ACTION.test(s)));
  const picked = [...items.map((l) => l.replace(/^(\d+[.)]|[-*•])\s+/, '')), ...rest.filter(keep)];
  return picked.slice(0, 12).map((s, i) => ({ n: i + 1, kind: IDEA.test(s) ? 'idea' : 'do', text: s.trim().slice(0, 160) }));
}

export function intakeText(id, tasks) {
  if (tasks.filter((t) => !t.from).length > 12) return `PROMPT INTAKE (the owner, 2026-11-05). This prompt is ${id}, a long prompt with ${tasks.length} sentences of requests. ` +
    'Read it three times: for what is asked, for each slide or object it names, for what it implies. Check each claim against the real file before you plan. ' +
    `Before the first build write arc-harness/intake/${id}.jsonl, one row per request with: id, block, tags, kind, slide, ask, check, status. ` +
    `Then run: py -3 -I ~/arc-harness/tools/gate.py cover ${id}. It lists sentences with no row. Put the block names in your task list. ` +
    'Each row ends as done (with its check), rejected (reason), blocked (need) or open. Mechanical list: ' +
    tasks.map((t) => `T${t.n} ${t.text.slice(0, 70)}`).join('; ') + '.';
  return `PROMPT INTAKE (the owner, 2026-10-30). This prompt is ${id}. Mechanical task list: ` +
    tasks.map((t) => `T${t.n} [${t.kind}] "${t.text}"`).join('; ') + '. ' +
    'Fix the list if it missed, merged or split a request. Put the T numbers in your task list. ' +
    'Follow a [do] task exactly. Research an [idea] task first (memory, files, sources), then accept or reject it with the reason. ' +
    'Before the turn ends, give every task one outcome: done (with the check that proves it), rejected (reason), blocked (what is needed) or open. ' +
    'Changed files get the automatic checks.';
}

// Focus (policy 15). The new state of a prompt: its own tasks, then each task of the last prompt that has no finished outcome.
// A notice or a short "Proceed" no longer wipes the list (D-20261104-26: that lost the seven tasks of the prompt before it).
export const PENALTY_LIMIT = 3;
export function mergeIntake(prev, id, tasks, offset) {
  const left = prev ? (prev.checked ? prev.open || [] : prev.tasks || []) : [];
  const carried = left.slice(-8).map((t, i) => ({ kind: t.kind, from: t.from || prev.id, was: t.was || t.n, n: tasks.length + i + 1,   // ponytail: keeps the last 8; the ledger holds the rest
    text: t.from ? t.text : `[from ${prev.id} T${t.n}] ${t.text}` }));
  return { id, tasks: [...tasks, ...carried], offset, checked: false, points: prev?.points || 0 };
}
export const penaltyText = (points) => (points ? `DISTRACTION PENALTY (policy 15): ${points} point(s). ` +
  'Give the outcome of every carried task before any other work. Put a T number on every plan step. Build no side idea. ' +
  (points >= PENALTY_LIMIT ? 'New subagents, workflows and sessions are refused until every task has an outcome. ' : `At ${PENALTY_LIMIT} points new subagents, workflows and sessions are refused. `) : '');

// Which tasks have no outcome in this turn: each T number must be followed by an outcome word in the reply text, the task
// list or the wrap-up. "T4-T6 done" covers T4, T5 and T6. A bare "T1" does not count (Argon review, 2026-10-30).
export function missingTasks(tasks, lines) {
  let hay = '', waiting = false, offplan = 0;
  const edited = new Set();
  for (const raw of lines) {
    let m; try { m = JSON.parse(raw); } catch { continue; }
    if (m.type !== 'assistant' || !Array.isArray(m.message?.content)) continue;
    for (const b of m.message.content) {
      if (b.type === 'text') hay += ' ' + b.text;
      if (b.type !== 'tool_use') continue;
      if (/update_plan|wrap_up|AskUserQuestion/.test(b.name)) hay += ' ' + JSON.stringify(b.input);
      if (/update_plan/.test(b.name) && Array.isArray(b.input?.items)) offplan = b.input.items.filter((x) => !/\bT\d+/.test(x.content || '')).length;   // the last full list counts
      if (/AskUserQuestion/.test(b.name) || (/wrap_up/.test(b.name) && b.input?.ask)) waiting = true;
      if (/^(Write|Edit|MultiEdit|NotebookEdit)$/.test(b.name) && b.input?.file_path) edited.add(b.input.file_path);
      if (b.name === 'Bash') for (const w of String(b.input?.command || '').match(/arc-harness[\\/]workflows[\\/][^\\/\s"']+/gi) || []) edited.add(`/${w}/`);
    }
  }
  const covered = new Set(), pending = new Set();
  for (const m of hay.matchAll(/\bT(\d+)(?:\s*[-–]\s*T?(\d+))?\b(?:(?!\bT\d)[^\n]){0,120}?\b(done|rejected|blocked|open|accepted|answered|completed)\b/gi)) {   // the word belongs to the nearest T
    for (let n = +m[1]; n <= +(m[2] || m[1]); n++) {
      covered.add(n);
      if (/^(open|blocked)$/i.test(m[3])) pending.add(n); else pending.delete(n);
    }
  }
  return { missing: tasks.filter((t) => !covered.has(t.n)).map((t) => `T${t.n}`), pending: [...pending], offplan, waiting, edited: [...edited] };
}

function intakeQA(edited) {
  const wf = new Set();
  for (const f of edited) {
    const m = /[\\/]arc-harness[\\/]workflows[\\/]([^\\/]+)/i.exec(f);
    if (m) wf.add(path.join(HARNESS, 'workflows', m[1]));
  }
  const found = [];
  for (const d of wf) {
    if (!fs.existsSync(d)) continue;
    const r = spawnSync('py', ['-3', '-I', path.join(HARNESS, 'tools', 'version.py'), 'check', d], { encoding: 'utf8', timeout: 8000, windowsHide: true });
    if (r.status) found.push((r.stdout || '').trim().split('\n').slice(0, 4).join('; '));
  }
  return found;
}

async function checkIntake(input) {
  const sid = String(input.session_id || 'unknown').replace(/[^\w-]/g, '_');
  const file = path.join(STATE, `intake-${sid}.json`);
  const st = readJson(file, null);
  const again = input.stop_hook_active;   // the reply after a block: it may close the tasks, and it never blocks again
  if (!st || (st.checked && !again) || !st.tasks?.length) return;
  let buf;
  try { buf = fs.readFileSync(input.transcript_path); } catch { return; }
  const r = missingTasks(st.tasks, buf.subarray(Math.min(st.offset || 0, buf.length)).toString('utf8').split('\n').filter(Boolean));
  const lf = path.join(HARNESS, 'intake', `${st.id}.jsonl`);   // a long prompt keeps its outcomes in a ledger file
  if (fs.existsSync(lf)) r.missing = fs.readFileSync(lf, 'utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l)).filter((x) => !/^(done|rejected|blocked|open)$/.test(x.status)).map((x) => x.id);
  else if (st.tasks.filter((t) => !t.from).length > 12) r.missing = [`ledger file intake/${st.id}.jsonl`];
  st.open = st.tasks.filter((t) => r.missing.includes(`T${t.n}`) || r.pending?.includes(t.n));
  const clean = !r.missing.length && !r.offplan;
  if (clean) st.points = 0;
  else if (!r.waiting && !again) st.points = (st.points || 0) + r.missing.length + (r.offplan || 0);
  st.checked = true;
  fs.writeFileSync(file, JSON.stringify(st));
  if (again) return;
  const qa = intakeQA(r.edited);
  const outcome = r.waiting ? 'waiting' : r.missing.length || qa.length ? 'blocked-once' : 'covered';
  fs.appendFileSync(path.join(HARNESS, 'intake', 'ledger.jsonl'), JSON.stringify({ at: new Date().toISOString(), id: st.id, outcome, missing: r.missing, pending: r.pending, offplan: r.offplan, points: st.points || 0, qa }) + '\n');
  if (r.waiting || (!r.missing.length && !qa.length)) return;
  out({ decision: 'block', reason: `Prompt intake (the owner, 2026-10-30) for ${st.id}. ` +
    (r.missing.length ? `These tasks have no outcome yet: ${r.missing.join(', ')}. Add one line each: done (check), rejected (reason), blocked (need) or open. ` : '') +
    (qa.length ? `Automatic QA found problems: ${qa.join(' | ')}. Fix them or say why they stay.` : '') });
}

// Only lessons whose area or text shares a word with the prompt; best match first. At most five.
// (2026-11-02: lessons with no shared word are left out; they pulled unrelated topics into every turn.)
export function pickLessons(lessons, prompt, k = 5) {
  const words = new Set(String(prompt || '').toLowerCase().match(/[a-zа-яё0-9-]{4,}/g) || []);
  const score = (l) => [l.area, l.text].join(' ').toLowerCase().split(/[^a-zа-яё0-9-]+/).filter((w) => words.has(w)).length;
  const ranked = [...lessons].map((l, i) => ({ l, s: score(l), i })).filter((x) => x.s > 0).sort((a, b) => b.s - a.s || a.i - b.i).map((x) => x.l);
  // Consolidation at read time (2026-10-31): Hindsight returns near-copies of one lesson ("w02" and "week 02").
  // Keep the first of any pair in the same area whose word sets overlap by 80 % or more (Jaccard).
  const bag = (t) => new Set(String(t).toLowerCase().match(/[a-zа-яё0-9]{3,}/g) || []);
  const near = (a, b) => { const x = bag(a), y = bag(b); let n = 0; for (const w of x) if (y.has(w)) n++; return n / (x.size + y.size - n || 1) >= 0.8; };
  const kept = [];
  for (const l of ranked) if (!kept.some((m) => m.area === l.area && near(m.text, l.text))) { kept.push(l); if (kept.length === k) break; }
  return kept;
}

// Cammut is reached the way Claude Code reaches it: the cammut entry in ~/.claude.json (the direct Hindsight tunnel on
// this laptop was found stale on 2026-10-26). The token is read at run time and never printed or copied.
async function cammut(tool, args) {
  const cfg = readJson(path.join(os.homedir(), '.claude.json'), {}).mcpServers?.cammut;
  if (!cfg?.url) throw new Error('no cammut entry');
  const base = { 'content-type': 'application/json', accept: 'application/json, text/event-stream', ...(cfg.headers || {}) };
  let sid;
  const rpc = async (body) => {
    const res = await fetch(cfg.url, { method: 'POST', headers: sid ? { ...base, 'mcp-session-id': sid } : base,
      body: JSON.stringify(body), signal: AbortSignal.timeout(20000) });
    sid = sid || res.headers.get('mcp-session-id');
    const text = await res.text();
    const data = text.includes('data:') ? text.split('\n').filter((l) => l.startsWith('data:')).map((l) => l.slice(5)).pop() : text;
    return data ? JSON.parse(data) : {};
  };
  await rpc({ jsonrpc: '2.0', id: 1, method: 'initialize', params: { protocolVersion: '2025-06-18', capabilities: {}, clientInfo: { name: 'arc-rules', version: '1' } } });
  await rpc({ jsonrpc: '2.0', method: 'notifications/initialized' });
  const r = await rpc({ jsonrpc: '2.0', id: 2, method: 'tools/call', params: { name: 'cammut_call', arguments: { tool, arguments: args } } });
  return JSON.parse(r.result?.content?.[0]?.text || '{}');
}

async function refreshLessons() {
  const tags = await cammut('hindsight/list_tags', { q: 'lesson*', limit: 100 });
  const lessonTags = (tags.items || tags.tags || []).map((t) => (typeof t === 'string' ? t : t.tag || t.name)).filter((t) => t === 'lessons' || t?.startsWith('lesson:'));
  if (!lessonTags.length) return;
  const results = (await cammut('hindsight/recall', { query: 'lesson: what failed, why, and the rule or fix that works',
    tags: lessonTags, budget: 'low', max_tokens: 3000 })).results || [];
  const lessons = results
    .map((r) => ({ area: (r.tags || []).find((t) => t.startsWith('lesson:'))?.slice(7) || ((r.tags || []).includes('lessons') ? 'general' : undefined), text: String(r.text || '').split(' | ')[0].slice(0, 300) }))
    .filter((l) => l.area);
  fs.mkdirSync(path.dirname(CACHE), { recursive: true });
  fs.writeFileSync(CACHE, JSON.stringify({ at: new Date().toISOString(), lessons }, null, 1));
}

// A slide fragment: an .html file in a deck folder, small enough to be text and not a built deck.
export function isDeckText(file) {
  if (path.extname(file).toLowerCase() !== '.html' || !/[\\/](deck_src|ol|slides|decks?)[\\/]/i.test(file)) return false;
  try { return fs.statSync(file).size < 400000; } catch { return false; }
}
// Writing work: the voice card goes in by itself (the owner, 2026-11-05: "a hook that autonomously triggers write like me").
export const WRITING = /\b(slides?|decks?|headers?|headings?|titles?|captions?|slogans?|wording|texts?|writes?|rewrite|readme|abstract|proposal|pitch|talk|speech|prose|describe|explain)\b|слайд|заголов|текст|напиш|перепиш/i;

function lint(file) {
  const ext = path.extname(file).toLowerCase();
  const deck = isDeckText(file);
  if (!deck && !['.md', '.txt', '.json'].includes(ext)) return null;
  if (ext === '.json' && !/\.scene\.json$|[\\/](slides|deck|scenes)[\\/]/i.test(file)) return null;
  const mode = /\.scene\.json$/i.test(file) ? 'film' : deck || /[\\/](slides|deck)[\\/]/i.test(file) ? 'slide' : 'doc';
  const r = spawnSync('py', ['-3', '-I', path.join(HARNESS, 'tools', 'lint_prose.py'), file, '--mode', mode, '--json'],
    { encoding: 'utf8', timeout: 4000, windowsHide: true });
  try { return JSON.parse(r.stdout || '[]'); } catch { return null; }
}

// Candidate lessons from this turn's part of the transcript.
export function candidates(lines) {
  const found = [];
  for (const raw of lines) {
    let m; try { m = JSON.parse(raw); } catch { continue; }
    if (m.type !== 'user' || !m.message) continue;
    const c = m.message.content;
    if (Array.isArray(c)) {
      for (const b of c) {
        if (b.type !== 'tool_result') continue;
        const text = Array.isArray(b.content) ? b.content.map((x) => x.text || '').join(' ') : String(b.content || '');
        if (/doesn't want to proceed|rejected|was denied/i.test(text)) found.push({ kind: 'rejected', text });
        else if (b.is_error) found.push({ kind: 'tool_error', text });
      }
    } else if (typeof c === 'string' && !c.startsWith('<') && c.length < 4000 && !/^\s*Stop hook feedback/i.test(c) &&   // a hook's own block comes back as a user turn
               /^\s*(?:(?:and|but|also|so)\s+)?(no\b|wrong|stop\b|not (?:like )?this|i said|i asked|that's not|this is (?:bs|bad|wrong|not)\b|why (?:do|did|didn't|are|too)\b|too much\b|i (?:still )?(?:do not|don't|didn't|did not) (?:still )?(?:see|like|want|ask)|нет\b|не так|я же)/i.test(c)) {
      found.push({ kind: 'correction', text: c });
    }
  }
  return found;
}

async function captureLessons(input) {
  const sid = String(input.session_id || 'unknown').replace(/[^\w-]/g, '_');
  const stateFile = path.join(STATE, `arc-${sid}.json`);
  const st = readJson(stateFile, { offset: 0 });
  let buf;
  try { buf = fs.readFileSync(input.transcript_path); } catch { return; }
  if (buf.length < st.offset) st.offset = 0;
  const lines = buf.subarray(st.offset).toString('utf8').split('\n').filter(Boolean);
  st.offset = buf.length;
  fs.mkdirSync(STATE, { recursive: true });
  fs.writeFileSync(stateFile, JSON.stringify(st));
  const found = candidates(lines);
  if (!found.length) return;
  const { redact } = await import(`file:///${SM.replace(/\\/g, '/')}`);
  const at = new Date().toISOString();
  const rows = found.map((f) => JSON.stringify({ at, agent: 'claude-code', session: sid, cwd: input.cwd, kind: f.kind, text: redact(f.text).slice(0, 300) }));
  const inbox = path.join(HARNESS, 'lessons', 'inbox.jsonl');
  fs.mkdirSync(path.dirname(inbox), { recursive: true });
  fs.appendFileSync(inbox, rows.join('\n') + '\n');
}

// The daily notice reaches each session once. The record lists the sessions that saw it.
export function dailyFor(rec, sid) {
  const seen = Array.isArray(rec.shown) ? rec.shown : [];
  if (!rec.notice || seen.includes(sid)) return null;
  return { text: rec.notice, next: { ...rec, shown: [...seen, sid] } };
}

async function main() {
  if (process.argv[2] === 'refresh-lessons') { try { await refreshLessons(); } catch (e) { if (process.env.ARC_DEBUG) console.error(e); } return; }
  let input = {};
  try { input = JSON.parse(fs.readFileSync(0, 'utf8') || '{}'); } catch { return; }
  const event = input.hook_event_name;

  if (event === 'SessionStart') {
    const age = Date.now() - (fs.existsSync(CACHE) ? fs.statSync(CACHE).mtimeMs : 0);
    if (age > 6 * 3600 * 1000) {
      spawn(process.execPath, [fileURLToPath(import.meta.url), 'refresh-lessons'], { detached: true, stdio: 'ignore', windowsHide: true }).unref();
    }
    if (readJson(DAILY, {}).day !== new Date().toISOString().slice(0, 10)) {   // once a day: what changed in policy, hooks, Cammut and the road map
      spawn(process.execPath, [path.join(HARNESS, 'tools', 'daily_reconcile.mjs')], { detached: true, stdio: 'ignore', windowsHide: true }).unref();
    }
    return;
  }

  if (event === 'UserPromptSubmit') {
    const timer = setTimeout(() => process.exit(0), 200);   // past 200 ms the turn goes on without the reminders
    const r = readJson(path.join(HARNESS, 'shared', 'reminders.json'), null);
    if (!r) return;
    const sid = String(input.session_id || 'unknown').replace(/[^\w-]/g, '_');
    const seen = path.join(STATE, `arc-first-${sid}`);
    const first = !fs.existsSync(seen);
    if (first) { try { fs.mkdirSync(STATE, { recursive: true }); fs.writeFileSync(seen, ''); } catch { /* fine */ } }
    const parts = [r.ask, r.style];
    if (r.argon) parts.push(r.argon);
    if (r.voice && WRITING.test(humanText(input.prompt))) parts.push(r.voice);
    const daily = humanText(input.prompt) ? dailyFor(readJson(DAILY, {}), sid) : null;
    if (daily) {
      parts.unshift(daily.text);
      try { fs.writeFileSync(DAILY, JSON.stringify(daily.next, null, 1)); } catch { /* it shows again on the next prompt */ }
    }
    if (isNewTask(input.prompt, first)) {
      const lessons = pickLessons(readJson(CACHE, { lessons: [] }).lessons || [], input.prompt);
      parts.unshift(r.research + (lessons.length ? ' Lessons: ' + lessons.map((l) => `[${l.area}] ${l.text}`).join(' ') : ''));
    }
    try {
      const day = new Date().toISOString().slice(0, 10);
      const cf = path.join(STATE, 'intake-counter.json');
      if (!humanText(input.prompt)) throw new Error('notice');   // no tag for app or agent notices
      const { redact } = await import(`file:///${SM.replace(/\\/g, '/')}`);
      const tasks = parseTasks(input.prompt).map((t) => ({ ...t, text: redact(t.text) }));
      const c = readJson(cf, {});   // ponytail: no lock; two sessions in one instant may share a number, the ledger row names the session
      const n = c.day === day ? c.n + 1 : 1;   // the counter moves only after the slow import, so a timeout leaves no gap
      fs.writeFileSync(cf, JSON.stringify({ day, n }));
      const id = `D-${day.replace(/-/g, '')}-${String(n).padStart(2, '0')}`;
      let offset = 0; try { offset = fs.statSync(input.transcript_path).size; } catch { /* new transcript */ }
      const state = mergeIntake(readJson(path.join(STATE, `intake-${sid}.json`), null), id, tasks, offset);
      fs.writeFileSync(path.join(STATE, `intake-${sid}.json`), JSON.stringify(state));
      fs.mkdirSync(path.join(HARNESS, 'intake'), { recursive: true });
      fs.appendFileSync(path.join(HARNESS, 'intake', 'ledger.jsonl'), JSON.stringify({ at: new Date().toISOString(), id, session: sid, cwd: input.cwd, tasks }) + '\n');
      if (state.tasks.length) parts.unshift(penaltyText(state.points) + intakeText(id, state.tasks));
    } catch { /* the turn goes on without the intake */ }
    clearTimeout(timer);
    out({ hookSpecificOutput: { hookEventName: 'UserPromptSubmit', additionalContext: parts.join(' ') } });
    return;
  }

  // During a workflow run (run.ps1 sets ARC_WORKFLOW to the package folder) every tool call is mapped to a policy event,
  // and the package's policy.mjs decides before the call runs. Outside a run this does nothing.
  if (event === 'PreToolUse') {
    if (/^(Agent|Task|Workflow|mcp__ccd_session__start_session)$/.test(input.tool_name)) {
      const sid = String(input.session_id || 'unknown').replace(/[^\w-]/g, '_');
      const points = readJson(path.join(STATE, `intake-${sid}.json`), {}).points || 0;
      if (points >= PENALTY_LIMIT) {
        out({ hookSpecificOutput: { hookEventName: 'PreToolUse', permissionDecision: 'deny',
          permissionDecisionReason: `Distraction penalty (policy 15): ${points} points. Give every task an outcome first.` } });
        return;
      }
    }
    const pkg = process.env.ARC_WORKFLOW;
    const runner = pkg && path.join(pkg, 'tools', 'policy.mjs');
    if (!runner || !fs.existsSync(runner)) return;
    const ti = input.tool_input || {};
    const ev = spawnSync(process.execPath, [runner, 'event', '--tool', input.tool_name, '--file', ti.file_path || ti.notebook_path || '', '--command', ti.command || ''], { encoding: 'utf8', timeout: 4000, windowsHide: true });
    const [name, file = ''] = (ev.stdout || '').trim().split('\t');   // a shell command that writes names its file here
    if (!name || name === 'none') return;
    const r = spawnSync(process.execPath, [runner, 'check', '--event', name, ...(file ? ['--file', file] : []), '--json'], { encoding: 'utf8', timeout: 20000, windowsHide: true });
    let res; try { res = JSON.parse(r.stdout); } catch { return; }
    if (!res.code) return;
    const failed = res.rows.filter((x) => x.result === 'fail').map((x) => `${x.id} (${x.on_fail}): ${x.rule}`);
    out({ hookSpecificOutput: { hookEventName: 'PreToolUse', permissionDecision: 'deny',
      permissionDecisionReason: `Workflow policy refused this ${name} step. ${failed.join(' | ')}` } });
    return;
  }

  if (event === 'PostToolUse') {
    const file = input.tool_input?.file_path;
    if (!file || !['Write', 'Edit'].includes(input.tool_name)) return;
    const findings = lint(file);
    if (!findings || !findings.length) return;
    const card = findings.some((f) => /^SLIDE-(HEADER|CAP)/.test(f.rule)) ? (readJson(path.join(HARNESS, 'shared', 'reminders.json'), {}).voice || '') + ' ' : '';
    const top = findings.slice(0, 6).map((f) => `line ${f.line} ${f.severity} ${f.rule}: ${f.message}`).join('; ');
    out({ hookSpecificOutput: { hookEventName: 'PostToolUse', additionalContext:
      card + `Word check (arc-harness lint_prose, does not block) found ${findings.length} item(s) in ${path.basename(file)}: ${top}. ` +
      'Fix the errors in text meant for the owner, slides or films; judge the warnings.' } });
    return;
  }

  if (event === 'Stop') {
    try { await captureLessons(input); } catch { /* never break a session */ }
    try { await checkIntake(input); } catch { /* never break a session */ }
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) await main();
