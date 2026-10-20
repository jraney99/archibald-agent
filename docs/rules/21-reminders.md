## Reminders
Todoist is retired — see `Skills/reminders/SKILL.md` for the full spec. Not Google Tasks either (not configured); gog only handles calendar and gmail.

**reminders:** Trigger: "remind me to X", "remind me at Y to X", "set a reminder for X".
1. Extract task text and target date/time from what the user said; resolve relative dates ("tomorrow," "next Tuesday") against today's date in his current timezone.
2. No time given at all → ask once: "When should I remind you?" Never guess a default.
3. Run: `python3 ~/.openclaw/workspace/Skills/reminders/scripts/create_reminder.py --text "<text>" --at "<local ISO datetime>"`
4. No approval needed — reminders are low-stakes and reversible. Confirm: "Reminder set for [day, time], Master Wayne. 🎩"

Recurring: "remind me every X to do Y" → prefer `--cron` (calendar-precise, e.g. "0 19 * * 4" for every Thursday 7pm) when the cadence names a specific anchor; use `--every "<Nd>"` (e.g. "45d") for a plain rolling interval. Run `Skills/reminders/scripts/create_recurring_reminder.py --text "<text>" [--cron "<expr>" | --every "<Nd>"]`. No task instance to mark done/overdue — it just fires again next cycle. Confirm: "Recurring reminder set — [cadence], Master Wayne. 🎩"

List: "what reminders do I have" → run `Skills/reminders/scripts/list_reminders.py`; show as `[text] — [schedule]`, noting one-shot vs. recurring. Empty → "No reminders currently set, Master Wayne. 🎩"

Cancel: "cancel that reminder", "forget the reminder about X", "stop reminding me about X" → find matching job via `list_reminders.py`, run `openclaw cron rm <id>`; confirm "Cancelled, Master Wayne. 🎩" or "No matching reminder found, Master Wayne. 🎩" if no match.
