---
name: to-do
description: Track promises, follow-ups, returns, appointments, and project actions from capture through verified completion.
---

# To-do

Use `scripts/todo.py` for open actions and `memory/projects/current.md` for current project context.
The tracker complements calendar events and reminder delivery. A reminder firing does not mean its task is complete.

Commands:
- `add --title "..." --source "..." [--project work] [--due YYYY-MM-DD] [--waiting-on "..."] [--next-step "..."]`
- `list [--project work] [--brief]`
- `update ID --status open|waiting|completed|cancelled [--evidence "..."] [--due YYYY-MM-DD] [--next-step "..."]`
- `sync` imports explicit pending memory actions and one-shot user reminders without creating duplicate reminders.

`MEMORY.md` pending actions and Loop Closer use the same stable source key. Closing a Loop Closer item first updates the canonical SQLite commitment with the user's resolution evidence, then removes the pending bullet.

Capture an explicit promise or requested task immediately. Preserve who owns it, its source, and the next step.
Use `waiting` for another person's response, not repeated daily escalation. Require the user's confirmation or a verified result as evidence before completion.
Do not infer that a past deadline, fired reminder, or quiet email thread means a task was done.
Before meetings, check the project and relevant commitments, then prepare a short brief and any unsent follow-up draft.
No messages, bookings, or calendar writes are sent by the tracker itself.

## Output Mode
Reports from this skill follow the one shared presentation standard:
`workspace/Documentation/rules/33-output-mode.md`, rendered with
`Skills/shared/output_mode.py`. Do not restate the layout here and do not
hand-space rows — `todo.py status` is the Output Mode view; `list` stays JSON.

## Capture is the default

**If you tell the user to do something, record it before the turn ends.** One line:

    from open_loop import opened
    opened(key="<stable unique key>", title="<the task, as an instruction>",
           source="<where it came from>", due="YYYY-MM-DD", next_step="<why / how>")

Not a nice-to-have. A task that lives only in a reply dies with the conversation: the
2026-10-08 finance review produced eight real tasks, and none of them existed
anywhere an hour later. The user will
not type them in — he used Todoist for years and it decayed into one stale reminder.

`opened()` is idempotent on `key`, so saying the same thing twice opens one task. It
swallows its own errors, so a broken store can never cost him the reply. A dated task
is chased on its due date; an undated one after two days. `answered(key, evidence)`
closes it — and a skill that can SEE the work done should close it itself rather than
ask him to confirm.

This lived in `RULES.md` until 2026-10-11, when the always-injected bootstrap set hit
99.8% of `bootstrapTotalMaxChars` (65 characters spare). RULES.md keeps the one-line
imperative so the trigger is always in context; the signature and the reasoning live
here, loaded when this skill is. Follow the same split for anything added later:
the instruction goes in RULES.md, the explanation goes in a skill doc.
