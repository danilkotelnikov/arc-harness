# Arc Harness

Arc Harness keeps one set of working rules for every AI agent you use. You write the rules once, and a sync tool installs them into Claude Code, Antigravity, ZCode and Codex.

The rules cover how an agent takes a request, how it builds and checks work, how it writes and when it must stop and ask. A hook adds short reminders to each turn and records every request as numbered tasks, so nothing is quietly dropped.

## Contents

| Path | Content |
|---|---|
| `shared/rules/common.md` | The working policy |
| `shared/rules/routing.md` | Which model does which job |
| `shared/reminders.json` | The short lines the hook adds to a turn |
| `shared/lexicon/` | Term list and refused words |
| `surfaces/claude-code/hooks/` | The Claude Code hook and its test |
| `tools/sync.mjs` | Installs the rules into every agent |
| `tools/lint_prose.py` | The word checker |
| `tools/gate.py` | The log of checks and verdicts |
| `tools/version.py` | One live file per artifact, with an archive |

## Install

You need Node.js and Python 3. Clone the repository into your home folder, then check and install:

```bash
node tools/sync.mjs --check
node tools/sync.mjs --apply
```

The install backs up each file it changes. It edits only the block it manages in each instruction file.

## Use your own rules

Edit `shared/rules/common.md`, then run the sync again. Keep each rule short and testable, because agents follow a rule they can check.

## Development

```bash
node --test surfaces/claude-code/hooks/arc-rules.test.mjs
node tools/sync.mjs --selftest
```

See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).

## Licence

Arc Harness is under the GNU Affero General Public License, version 3 only. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
