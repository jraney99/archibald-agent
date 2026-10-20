#!/usr/bin/env python3
"""
Log a meal prep as completed. Called when the user taps the ✅ Done button on
a meals reminder (confirm:meals).

Appends a timestamped entry to meals_log.json — a flat append-only log,
not a task list. There is no "pending"/"overdue" state: skipping a tap has
no effect on anything, the next reminder still fires on schedule.

Acknowledges the tap by editing the original reminder message in place
(clearing its button) rather than sending a brand new Telegram message —
prints NO_REPLY on success so the callback dispatcher relays the sentinel
verbatim and stays silent instead of posting a second, redundant "Logged..."
message under the still-visible button (2026-09-13: The user flagged this as
looking bad — matches the fix already used for hydration/medication/etc.).
Falls back to printing the confirmation text normally if the edit can't be
made (e.g. no stored message id), so nothing is silently dropped.

Usage:
    python3 log_meals.py
"""
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path("~/.openclaw/workspace/Skills/shared")))
from pending_proposal import load_message_id, edit_or_fallback  # noqa: E402

LOG_PATH = Path("~/.openclaw/meals_log.json")
STATE_PATH = Path("~/.openclaw/freezer_meals_reminder_state.json")
TZ = ZoneInfo("America/Chicago")


def main():
    entries = json.loads(LOG_PATH.read_text()) if LOG_PATH.exists() else []
    now = datetime.now(TZ)
    entries.append({"task": "meals taken from freezer to fridge", "timestamp": now.isoformat()})
    LOG_PATH.write_text(json.dumps(entries, indent=2))

    msg_id = load_message_id(STATE_PATH)
    text = f"✅ Logged meals prep at {now.strftime('%-I:%M %p')}. 🎩"
    print(edit_or_fallback(msg_id, text))


if __name__ == "__main__":
    main()
