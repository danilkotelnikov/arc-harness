### Which model does what (the owner, 2026-10-26)

| Work | Model |
|---|---|
| Planning, joining results, engine core code, review of every change | Claude Code (orchestrator); helpers only Sonnet 5.5 High or Haiku 5.5 |
| Storyboards, motion critique, design and plan reviews | Gemini 4 Argon High (Antigravity) |
| Blind reads of changed frames, English and Russian layout screening | Gemini 3.8 Flash (Antigravity) |
| Bulk Russian first drafts, term-list checks, simple file chores | GLM (ZCode), always checked by automatic tests |
| Code review and science claims | Codex (Sol, Astra) from 17 November 2026; until then Argon |

Antigravity from the command line: `agy.exe --model gemini-4-argon-high --disable-slash-commands --add-dir <folder> -p="Read <prompt file> and follow it"`. The prompt must be attached to `-p=`; a long prompt goes in a file.
