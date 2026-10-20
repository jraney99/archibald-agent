## Maintenance
Remove obsolete rules rather than adding exceptions. No false claims about integrations or health checks.

**Automation rollout gate:** before a cron/script sends the user a new Telegram message for the
first time, or before changing the wording/trigger of one that already sends live, show
The user the exact message text and firing condition and get his explicit go-ahead first. Ship
new/changed ones disabled or dry-run until approved, then enable. Does not apply to bug
fixes restoring previously-approved behavior, or ordinary live-conversation replies. See
root `CLAUDE.md` and `feedback-automation-rollout-gate` memory.
