---
name: loop-closer
description: Resolve or snooze critical pending actions already flagged in memory.
---

# Loop Closer

Forces explicit resolution of critical (🚨) items in MEMORY.md's
"## Pending Actions" section. Built 2026-09-13 because an enrollment-deadline
item sat flagged `🚨 CRITICAL/OVERDUE` from June through September, restated
on every "Last updated" pass, never confirmed done or not-done — the same
failure the "Archived/Stale Items" section enables for anything else that
just ages out of attention instead of getting closed.

## Mechanism

1. **8am cron step** (added to `morning-briefing-daily-8am`'s runner) runs
   `scripts/send_escalations.py` before the briefing itself. It scans
   Pending Actions for 🚨 bullets, and for any open 3+ days (or already
   worded "overdue" — those count as 10 days old on first scan) sends a
   standalone Telegram message with two buttons: **✅ Done** and
   **🔴 Still open**.
2. Tapping a button is handled deterministically by the `callback-dispatch`
   plugin (see `plugins/callback-dispatch/dispatch-table.json` and the
   matching rows added to `RULES.md`'s Inline Button Callbacks table) —
   no agent turn involved:
   - **Done** → `loop_closer.py close --id <id> --resolution done` — deletes
     the bullet from Pending Actions and logs it under a new
     `## Resolved (Loop Closer)` section in MEMORY.md with the date. The same
     action closes the canonical commitment record with explicit evidence.
   - **Still open** → `loop_closer.py keep-open --id <id>` — leaves the
     bullet in place, tags it `_(confirmed still open <date>)_`, and snoozes
     re-pinging for 3 days. It never silently disappears into "Archived/Stale."
3. Escalation wording gets sharper with age: 3–6 days open is a plain 🚨
   ping, 7–13 days adds "Open a week+ now," 14+ days adds "no closure."

## State

`~/.openclaw/loop_closer_state.json` — per-item first-flagged
date, last-pinged date, snooze date, ping count. Only tracks 🚨-marked
bullets; plain pending items are untouched.

## Manual use

```
python3 loop_closer.py scan                                    # sync state from MEMORY.md
python3 loop_closer.py escalate                                 # what would ping today
python3 loop_closer.py close --id <id> --resolution done        # force-close by hand
python3 loop_closer.py keep-open --id <id> --note "waiting on X"
```

Not a substitute for judgment — it only escalates, it never auto-closes.
An item stays open (and keeps getting asked about) until the user explicitly
taps Done or says so.
