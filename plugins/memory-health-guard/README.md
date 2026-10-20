# memory-health-guard

A `before_tool_call` hook on the `edit` and `write` tools that blocks
meal/supplement/recovery-movement reports from being written into
`workspace/MEMORY.md`'s "## Health" section instead of the real tracking
scripts. That section is a domain pointer + hard-rule reminder — it isn't
read by the Dr. Example weekly journal (`physician_journal.py`), so anything logged
there just sits doing nothing.

## Why this exists

2026-09-13, twice within a few hours, the user free-text-reported real events to
the live Telegram agent ("had dinner + a supplement," later
"breakfast was food+coffee, snack was fruit, did a 20min swim"). Both times the model appended a dated bullet to MEMORY.md's Health
section instead of running `confirm_meal.py`/`log_food.py`,
`log_supplement.py`, or `hevy-workout/log_movement.py` — despite RULES.md's
"Daily Reminder/Check-in Replies in Free Text" section already covering
exactly this case, and despite a prior session having confirmed re-reading
it. Tracing the actual tool calls showed `read workspace/MEMORY.md` ->
`edit workspace/MEMORY.md`, with no RULES.md lookup at all — a model-
adherence miss, not a missing rule or a stale cache.

A cross-file prose rule wasn't enough to stop a model that had already
decided MEMORY.md's Health section was the right landing spot (made worse
the second time by the first entry looking like an established pattern to
extend). This plugin puts the stop sign where the model actually looks:
the `edit`/`write` tool call itself, before it lands — same pattern as
`calendar-write-guard` / `email-send-guard` / `constitution-guard`, just on
the `edit`/`write` tools instead of `exec`.

An in-file guardrail line was also added directly under MEMORY.md's Health
heading (see that file) as a first layer; this plugin is the second,
deterministic one, same "defense in depth" relationship `constitution-guard`
has with `chflags uchg`.

## What it does

Blocks an `edit` or `write` targeting `workspace/MEMORY.md` when:

1. The change is near the Health section — the edit's `old_string`/
   `new_string` (or a full-file `write`'s `content`) mentions `## Health` or
   one of that section's stable anchor phrases, **and**
2. The text being added reads like a health-tracking log entry rather than
   a structural fact — a bold-dated bullet (`**2026-09-13 ... log:**`)
   naming a meal window / supplement / movement, or looser vocabulary
   (dinner, breakfast, supplement names, sauna, ...) paired with a
   report-style verb ("ate," "had," "took," "logged," "swam") or a duration
   in minutes.

On a block, the reply tells the model exactly which script to use instead
(`confirm_meal.py` / `log_food.py`, `log_supplement.py`, `log_movement.py`,
`add_note.py` for anything those scripts' fixed fields can't hold) and
points at the RULES.md section it should have followed.

Deliberately narrow per `plugins/GUARD-PATTERN.md` — this earns its keep on
the exact, twice-recurring shape, not by trying to catch every conceivable
future phrasing. A genuine structural edit to the Health section (updating
the "latest panel" pointer, fixing this guardrail bullet's own wording)
doesn't match and passes through untouched — see `test.mjs` for the
false-positive cases this was checked against.

## What it leaves alone

Every file other than `workspace/MEMORY.md`. Reads of MEMORY.md (only
`edit`/`write` are hooked). Edits to MEMORY.md that don't touch the Health
section or don't look like a log entry. Cron/job-driven writes
(`event.context.jobId` set) — no legitimate cron writes MEMORY.md's Health
section, so this is belt-and-suspenders, not a real exemption.

## Testing

`node test.mjs` — pure-function tests against `inspectMemoryHealthWrite`,
including both real incidents verbatim and the false-positive cases (an
unrelated MEMORY.md edit, a legitimate Health-section structural update, a
self-referential edit to this guard's own note, the same content written to
a *different* file). No live Telegram/exec calls.

## Deployed Tool Schemas (2026-09-14)
The registered hook normalizes `path`/`file_path`, checks every `edits[].newText` entry as well as legacy replacement fields, and inspects added lines in `apply_patch`. Cron calls are not exempt. Hook-level tests exercise all three actual tool shapes and allow the proper journal destination.

## Health log files are script-only (2026-09-18)

Second guard in the same plugin. Trigger: the agent logged a "shake" the user never mentioned, then rewrote
`nutrition/config/food_log.json` with the `edit` tool (after trying a `python3 -c … json.dump` exec).
`HEALTH_LOG_FILES` (exact basenames) are blocked for `edit` / `write` / `apply_patch`, and for `exec`
commands that name one of them alongside a write construct. Reads pass. The block reason points to
`Skills/dr-physician/scripts/log_day.py`. Only the agent is affected — Claude Code sessions and the log scripts
themselves don't go through this hook. Adding a new health log = add its basename to the set + a test.
