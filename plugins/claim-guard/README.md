# claim-guard

**Status: built and tested 2026-09-20. NOT enabled** — it is not in `openclaw.json` →
`plugins.allow` / `plugins.entries`. Enabling changes how Archibald answers the user in Telegram, so
it waits for his go-ahead on the trigger below (CLAUDE.md, Automation rollout gate).

## Why it exists

The 2026-09-20 accuracy audit (366 owner turns since 08-20) found one through line: every wrong
number and every lost fact came from the model answering from its own head.

- A meal count stated after a script ran: right 10 of 10. Stated with no tool call: wrong 4 of 4
  ("2 of 5 meals (Earlier meal logged)", 09-19 — the log had 1).
- "Logged" / "Recorded" / "Noted" with zero tool calls, verified on disk as never saved: two cash
  purchases (09-01), a set of measurements (08-28), a standing scheduling rule (08-23), a workout
  for the physician's log (09-11), food names (09-11, 09-16).
- The rules telling the model to run the script already existed. Rule 06 was opened 0 times in 190
  sessions. A paragraph the model skips is not enforcement; this is.

## What it does

Hook: `before_agent_finalize` (embedded agent). When the final reply

1. matches an explicit claim — a sentence opening with Logged / Recorded / Noted / Saved / Updated /
   Added / Filed / Marked / Scheduled / Booked / Cancelled / Moved / Removed / Deleted / Sent / Done;
   "I've logged…"; "…are recorded" / "is now set to…"; a logged count ("2 of 5 meals", "4/5
   windows"); or a standing promise ("I'll remind / flag / reroute / keep that in mind") that does
   not end in a question to the user — **and**
2. the turn (everything after the last user message) has **no** tool call that both could change
   state (anything outside `READ_ONLY_TOOLS`) and returned without `isError`,

it returns `{action: "revise"}` with an instruction: run the script that owns it and reply with what
the script printed, or say plainly that nothing was saved. The runtime gives the model one more
pass (hard cap 3 in the runtime; this plugin asks for at most 2).

Nothing is sent to the user by the plugin itself. He sees Archibald's corrected reply instead of the
unbacked one.

## What it deliberately does not do

- It does not judge whether a tool that *did* run covered the whole claim ("supplements taken" after
  only a meal script ran). That takes judgment; a guard earns its keep on the exact-match case.
- It does not see button taps or replies to meal nudges — `callback-dispatch` answers those before an
  agent turn exists, and those paths were accurate in the audit (14/14).
- It does not rewrite text. If the model still makes the claim after the retry passes, the reply goes
  out; the `[claim-guard] revise` lines in `~/Library/Logs/openclaw/openclaw.log` are the count of
  how often it fired, and the audit replay below is how escapes are found.

## Evidence it is narrow enough

Replayed over 336 real owner turns (08-20 → 09-20): fires on 29 (8.6%). Hand-reviewed: the lost facts
above, the 09-16 and 09-19 wrong counts, "Done. Bedroom lights off." with no tool, and two
conversational "Noted."s where nothing was in fact saved. Three false alarms found in replay were
fixed and are regression tests: a dated data row ending in "session done", and sentences inside a
quotation of an old message.

## Verified runtime contract (OpenClaw 2026.6.10)

- `dist/selection-*.js` builds the event: `{runId, sessionId, sessionKey?, provider, model, cwd?,
  transcriptPath?, stopHookActive: false, lastAssistantMessage, messages}`; `messages` is the whole
  active session in native transcript shape (`role: user | assistant | toolResult`, assistant
  `content[]` blocks of `type: "toolCall"` with `{id, name, arguments}`, results carrying
  `toolCallId`, `toolName`, `isError`). Context is a separate second argument.
- The hook is skipped for errored, aborted, timed-out, silent (`NO_REPLY`) and client-tool turns, and
  a requested revision is dropped if the turn already had a deterministic side effect.
- `dist/lifecycle-hook-helpers-*.js` normalizes the result; `dist/embedded-agent-*.js` honors
  `revise` by re-prompting (`MAX_BEFORE_AGENT_FINALIZE_REVISIONS = 3`).
- All three files are pinned in `test-fixtures/runtime-contract.json`; an upgrade fails
  `node test-fixtures/test-all.mjs` until the boundary is re-reviewed.

Not yet proven: that the hook fires in the live gateway for Telegram turns. First live firing =
a `[claim-guard] revise` line in the gateway log. Check for it after enabling.

## Enabling (after the user's yes)

1. `openclaw.json` → add `"claim-guard"` to `plugins.allow`, and `plugins.entries["claim-guard"] =
   {"enabled": true}`.
2. `openclaw gateway restart`, then `openclaw plugins list` / `openclaw plugins doctor`.
3. Add the backstop note to the RULES.md lines it enforces.

## Tests

`node plugins/claim-guard/test.mjs` — pure cases taken from the audit's real replies, plus the
registered-hook replay of three captured turns (`test-fixtures/finalize-turns.json`).
