#!/usr/bin/env python3
"""
The single nightly Hatch alarm sync, run at 9:10pm -- after
hevy-night-prep-9pm has actually booked tomorrow's workout onto the
calendar (not just previewed it).

History: this used to run at 8pm as part of the evening briefing (STEP 0A),
folded into that message per the user's original request to avoid a separate
notification. But hevy-night-prep-9pm -- which does the REAL workout
booking via schedule_workouts.py -- runs an hour later; the 8pm briefing
only ever saw a --dry-run *preview* of tomorrow's workout (STEP 2B there),
not the real calendar event. So on any workout morning, the 8pm computation
was reliably wrong and got silently thrown away.

First fix (2026-08-17, same day) added a *second* run at 9:10pm that only
spoke up if the wake time changed from the 8pm result -- but that still
computed the alarm twice every night (two Hatch logins against a
documented tight rate limit, two calendar fetches) for a value the first
one already knew would usually be wrong on workout days. The user asked to
just run it once. This script is that: the only alarm sync, timed to run
after the calendar is actually complete for tomorrow. The 8pm evening
briefing no longer touches the alarm at all (see RULES.md/evening cron).

Reuses sync_alarm.py's build_result() directly -- same fail-safe compute +
apply + conflict-check logic, not reimplemented -- and formats/sends the
exact same message shape the old STEP 0A instructions used to build,
so what the user sees is unchanged in substance, just correctly timed and
arriving as its own message instead of folded into the evening briefing.
Always sends (never conditional) -- "The user wants to always be told his wake
time, no exceptions" still holds, there's just one authoritative computation
now instead of two.

Usage:
    python3 sync_alarm_and_notify.py
"""
import json
import sys
import urllib.request
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import sync_alarm  # noqa: E402

TG_TOKEN = __import__("runpy").run_path("~/.openclaw/workspace/Skills/shared/runtime_secrets.py")["require_secret"]("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID = "1000000001"


def notify(text: str) -> None:
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = json.dumps({"chat_id": TG_CHAT_ID, "text": text}).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"notify failed: {e}", file=sys.stderr)


def format_message(result: dict) -> str:
    """Mirrors the old evening-briefing STEP 0A formatting rules exactly,
    so the message the user sees is the same shape as before -- just arriving
    at the correct time from its own script instead of an LLM-parsed step."""
    if result["sync_failed"]:
        # Same command-in-a-message fault as the nightly sync (2026-10-07).
        known = result["wake_time_fmt"] or "a time I couldn't read"
        lines = [f"⏰ Alarm: sync failed ({result['error']}) — currently set to {known}."]
    elif result.get("away"):
        if result["is_default"]:
            lines = [f"📱 Away — Hatch off. Set your phone alarm for {result['wake_time_fmt']} (default, nothing early)."]
        else:
            lines = [
                f"📱 Away — Hatch off. Set your phone alarm for {result['wake_time_fmt']} — "
                f"'{result['event_title']}' (actually {result['event_start_fmt']}) means leaving by "
                f"{result['leave_time_fmt']}."
            ]
    elif result["is_default"]:
        lines = [f"⏰ Alarm: {result['wake_time_fmt']} (default)."]
    else:
        lines = [
            f"⏰ Alarm: {result['wake_time_fmt']} — '{result['event_title']}' "
            f"(actually {result['event_start_fmt']}) means leaving by "
            f"{result['leave_time_fmt']}."
        ]
    for c in result["conflicts"]:
        lines.append(
            f"⚠️ Can't make it: '{c['to_event']}' at {c['to_start_fmt']} needs you "
            f"leaving '{c['from_event']}' by {c['required_leave_fmt']}, but it doesn't "
            f"end until {c['from_end_fmt']} — {c['shortfall_minutes']} min short."
        )
    return "\n".join(lines) + " 🎩"


def main() -> None:
    config = sync_alarm.load_config()
    tz = sync_alarm.get_tz()
    result = sync_alarm.build_result(config, tz)
    message = format_message(result)
    print(message)
    notify(message)


if __name__ == "__main__":
    main()
