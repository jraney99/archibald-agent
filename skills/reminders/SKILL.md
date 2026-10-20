---
name: reminders
description: Create and manage one-time or recurring personal reminders delivered through Telegram.
---

# Reminders Skill

One-shot "remind me to X at Y" utility. Replaces Todoist. No categories, no
persistent list, no weekly review — a reminder is a cron job that fires once,
delivers to Telegram, and deletes itself. `openclaw cron list` is the only
store; there is nothing else to keep in sync or let go stale.

## Why this shape

Todoist accumulated 34 projects (23 empty) and a 43-task list that was 84%
overdue — the failure mode was open-ended lists nobody reviewed, not bad
tooling. This skill deliberately has no surface for that to happen again:
nothing persists past its fire time, so there is no backlog to accumulate.

## Trigger phrases

`remind me to X`, `remind me at Y to X`, `set a reminder for X`

## Creating a reminder

1. Extract the task text and the target date/time from what the user said.
   Resolve relative dates ("tomorrow," "next Tuesday," "in an hour") against
   today's date in the user's current timezone.
2. **If no time was given at all, ask once** — "When should I remind you?"
   Never guess a default time. No time means no reminder.
3. Run:
   ```
   python3 ~/.openclaw/workspace/Skills/reminders/scripts/create_reminder.py \
     --text "<text>" --at "<local ISO datetime, e.g. 2026-07-07T09:00:00>"
   ```
   Omit `--tz` unless the user is traveling and means a timezone other than his
   current one — the script defaults to his current location.
4. No approval/confirmation needed before creating — reminders are low-stakes
   and reversible. Confirm: `"Reminder set for [day, time], Master Wayne. 🎩"`

## Creating a recurring reminder

Trigger: `remind me every X to do Y`, `every [weekday/N days/months] remind me to Y`

Unlike Todoist, a recurring reminder here has **no task instance to mark
done or that can go overdue** — it just fires the nudge again next cycle
regardless of whether the last one was acted on. That's the deliberate fix
for the pileup problem: recurrence lives in the scheduler, not in a list.

1. Extract the text and cadence. Prefer `--cron` (calendar-precise: exact
   weekday, day-of-month, or month) when the cadence names a specific
   anchor point ("every Thursday at 7pm," "every 6 months" starting from a
   known date). Use `--every <Nd>` for a plain rolling interval that doesn't
   land on a clean calendar boundary ("every 45 days").
2. Run:
   ```
   python3 ~/.openclaw/workspace/Skills/reminders/scripts/create_recurring_reminder.py \
     --text "<text>" --cron "<expr>"          # e.g. "0 19 * * 4" = every Thursday 7pm
   # or
   python3 ~/.openclaw/workspace/Skills/reminders/scripts/create_recurring_reminder.py \
     --text "<text>" --every "<Nd>"           # e.g. "45d"
   ```
3. No approval needed. Confirm: `"Recurring reminder set — [cadence], Master Wayne. 🎩"`

## Listing reminders

Trigger: `what reminders do I have`, `show my reminders`

Run `python3 ~/.openclaw/workspace/Skills/reminders/scripts/list_reminders.py`
and present each as `[text] — [schedule]`, noting one-shot vs. recurring.
Empty list → `"No reminders currently set, Master Wayne. 🎩"`

## Cancelling a reminder

Trigger: `cancel that reminder`, `forget the reminder about X`, `stop reminding me about X`

1. Run `list_reminders.py`, match by keyword against `text`.
2. Run `openclaw cron rm <id>`.
3. Confirm: `"Cancelled, Master Wayne. 🎩"` — or if no match: `"No matching reminder found, Master Wayne. 🎩"`

## Mechanics

Delivery goes straight to Telegram: the fired job is a **command payload** that
runs `send_telegram.py --text "<message>"` with `--no-deliver` (no runner
fallback). The message is composed at creation time, so no agent turn is needed
to decide what to say — and a command payload avoids the `[cron:<id> <name>]`
provenance header that `--announce` prepends to every delivered message.
`--delete-after-run` means a fired one-shot reminder cleans itself up.
