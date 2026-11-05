// Tests for the prompt intake in arc-rules.mjs (the owner, 2026-10-30). Run: node surfaces/claude-code/hooks/arc-rules.test.mjs
import assert from 'node:assert';
const m = await import('./arc-rules.mjs');
const p = `As I have seen you just keep most of the ideas from the first specification. But I do not see any techstack, examples, strict policy rules activation during execution. The document should be read as a complete documentation. If the license is a gate, it does not matter because we can inherit the owner's one. But it shouldn't be that much blunt. There must be a humanely acceptable Wiki and agent/machine-readable policy. Make the general rule that will tag all my prompts. I believe you, Claude, let's colloborate friendly`;
const t = m.parseTasks(p);
assert(t.length >= 6 && t.some((x) => x.kind === 'idea' && /inherit/.test(x.text)));
assert(m.parseTasks('ok').length === 0);
assert(m.parseTasks('1. delete the old files now\n2. rewrite the wiki page please').length === 2);
const line = (c) => JSON.stringify({ type: 'assistant', message: { content: c } });
let r = m.missingTasks(t.slice(0, 2), [line([{ type: 'text', text: 'T1 done (check passed).' }])]);
assert.deepStrictEqual(r.missing, ['T2']);
r = m.missingTasks(t.slice(0, 2), [line([{ type: 'tool_use', name: 'mcp__ccd_turn__wrap_up', input: { recap: 'x', ask: 'Go?' } }])]);
assert(r.waiting);
console.log('intake selftest: ok');
assert.strictEqual(m.humanText('<task-notification><task-id>x</task-id></task-notification>'), '');
assert.strictEqual(m.parseTasks('<side-sessions-event>please delete all the files now</side-sessions-event>').length, 0);
assert(m.humanText('Please <b>fix</b> it now please') !== '');
console.log('notice selftest: ok');
assert.strictEqual(m.humanText('<system-reminder id="x1">\nAnother session sent: please delete everything now\n</system-reminder id="x1">\n\n[SYSTEM NOTIFICATION - NOT USER INPUT]\n<task-notification>x</task-notification>'), '');
console.log('agent notice selftest: ok');
assert.strictEqual(m.parseTasks('Why Argon was rejected? It works from Agy').length, 1);
console.log('question selftest: ok');
assert.strictEqual(m.humanText('Fix <b>this</b> div please'), 'Fix <b>this</b> div please');
assert.strictEqual(m.parseTasks('Use e.g. policy.mjs for it please. Then delete the old files now.').length, 2);
const L = (c) => JSON.stringify({ type: 'assistant', message: { content: c } });
const three = [{ n: 1 }, { n: 2 }, { n: 3 }];
assert.deepStrictEqual(m.missingTasks(three, [L([{ type: 'text', text: 'I will look at T1 and T2. T3 done.' }])]).missing, ['T1', 'T2']);
assert.deepStrictEqual(m.missingTasks(three, [L([{ type: 'text', text: 'T1–T3 done (checks pass).' }])]).missing, []);
assert(m.missingTasks(three, [L([{ type: 'tool_use', name: 'Bash', input: { command: 'py version.py bump C:/Users/alice/arc-harness/workflows/w03-x/a.md' } }])]).edited.length === 1);
console.log('argon fixes selftest: ok');
assert.strictEqual(m.parseTasks('What about memory consolidation and other feature utilization in the original prompt. Show it with HTML snippets please.').length, 2);
console.log('what-about selftest: ok');
const dupL = [{ area: 'file-hygiene', text: 'The rule was implemented as policy P11 in w02 and as the script version.py with bump restore check' },
  { area: 'file-hygiene', text: 'The rule was implemented as policy P11 in week 02 and as the script version.py with bump restore check' },
  { area: 'cammut', text: 'Retains must be checked with get_operation then read back' }];
assert.strictEqual(m.pickLessons(dupL, 'rule retains').length, 2);
assert.strictEqual(m.pickLessons(dupL, 'hello there').length, 0);   // no shared word, no lesson
console.log('lesson dedupe selftest: ok');

// long prompt: every sentence counts, no cap, slide scope kept
{ const long = 'Slide 9. Unclear and messy. Numbers, slogans, etc. ' + Array.from({ length: 30 }, (_, i) => `Slide ${i + 10}. The picture there is bad.`).join('\n') + ' Filler words to pass the length limit here. '.repeat(10);
  const lt = m.parseTasks(long);
  assert(lt.length >= 31, `long prompt gave ${lt.length}`);
  assert(/^Slide 9: Unclear and messy/.test(lt[0].text));
  assert(m.WRITING.test('Slide 23 header is bad')); assert(!m.WRITING.test('run the tests'));
  assert(m.isDeckText('C:/no/such/ol/o1.html') === false); }

// Focus (policy 15).
{
  const a = [{ n: 1, kind: 'do', text: 'one' }, { n: 2, kind: 'do', text: 'two' }];
  const left = m.mergeIntake({ id: 'D-1', tasks: a, checked: true, open: [a[1]], points: 2 }, 'D-2', [], 9);
  assert.deepStrictEqual(left.tasks.map((t) => [t.n, t.was, t.from]), [[1, 2, 'D-1']]);
  assert.strictEqual(left.points, 2);
  assert(left.tasks[0].text.startsWith('[from D-1 T2]'));
  const next = m.mergeIntake({ ...left, checked: true, open: left.tasks }, 'D-3', [{ n: 1, kind: 'do', text: 'new' }], 9);
  assert.deepStrictEqual(next.tasks.map((t) => [t.n, t.was, t.from]), [[1, undefined, undefined], [2, 2, 'D-1']]);
  assert.strictEqual(next.tasks[1].text.match(/from/g).length, 1);
  assert.strictEqual(m.mergeIntake({ id: 'D-1', tasks: a, checked: false }, 'D-2', [], 0).tasks.length, 2);
  assert.strictEqual(m.mergeIntake(null, 'D-1', a, 0).tasks.length, 2);
  const line2 = (c) => JSON.stringify({ type: 'assistant', message: { content: c } });
  let f = m.missingTasks(a, [line2([{ type: 'text', text: 'T1 done (test). T2 blocked (needs a yes).' }])]);
  assert.deepStrictEqual([f.missing, f.pending, f.offplan], [[], [2], 0]);
  f = m.missingTasks(a, [line2([{ type: 'tool_use', name: 'mcp__ccd_turn__update_plan', input: { items: [{ content: 'T1 Fix' }, { content: 'Tidy logs' }] } }])]);
  assert.strictEqual(f.offplan, 1);
  assert.strictEqual(m.penaltyText(0), '');
  assert(/refused until/.test(m.penaltyText(3)) && /At 3 points/.test(m.penaltyText(1)));
  assert(!/long prompt/.test(m.intakeText('D-1', Array.from({ length: 14 }, (_, i) => ({ n: i + 1, kind: 'do', text: 'x', from: i > 1 ? 'D-0' : undefined })))));
  console.log('focus selftest: ok');
}

{
  const { dailyFor } = await import('./arc-rules.mjs');
  const rec = { notice: 'DAILY RECONCILE. x', shown: [] };
  const a = dailyFor(rec, 'one');
  assert.strictEqual(a.text, rec.notice);
  assert.strictEqual(dailyFor(a.next, 'one'), null);
  const b = dailyFor(a.next, 'two');
  assert.strictEqual(b.text, rec.notice);
  assert.deepStrictEqual(b.next.shown, ['one', 'two']);
  assert.strictEqual(dailyFor({ notice: '', shown: [] }, 'one'), null);
  assert.strictEqual(dailyFor({}, 'one'), null);
  console.log('daily notice selftest: ok');
}

// Corrections: the hook's own Stop text is not the owner's word; his usual openings count (2026-11-06).
{
  const u = (c) => JSON.stringify({ type: 'user', message: { content: c } });
  const kinds = (c) => m.candidates([u(c)]).map((x) => x.kind);
  for (const s of ['Stop hook feedback: Prompt intake for D-1. These tasks have no outcome yet: T1.',
    'Stop hook feedback: The owner made Cammut mandatory.', 'Why Argon was rejected? It works from Agy', 'Add a test please'])
    assert.deepStrictEqual(kinds(s), [], s);
  for (const s of ['No, latest is in /v8', 'Stop doing that', 'But i do not see any UI elements, animations, frameworks, etc.',
    'And I do not still see any functional changes.', 'Also I do not see anything related to the Cammut UI.',
    "I don't like the font size in a header.", 'I do not want boring tabs and boxes', "I didn't asked you to adopt things",
    'This is BS, not what I want.', 'Why do you always recall things I did not ask for?', "Why didn't you make a research?",
    'Too much of the rectangles is bad', 'Не так'])
    assert.deepStrictEqual(kinds(s), ['correction'], s);
  console.log('correction selftest: ok');
}
