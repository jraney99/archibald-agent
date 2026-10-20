#!/usr/bin/env python3
"""
Send the recurring "take meals out of the freezer" reminder with a single
"Done" inline button. Fired directly by cron (command payload) — no agent
turn needed to compose the message, so there's no LLM call in the path that
could fail or reword it. Tapping the button logs a timestamp via
log_meals.py; not tapping it has no consequence, the next reminder still
fires on schedule regardless (same no-backlog guarantee as every other
reminder in this skill).

Skips silently when the user is away from home (travel) — there is no freezer to
reach into, and an unactionable nudge every night of a trip is just noise.
Travel state comes from Skills/shared/current_location.is_away_from_home(),
the same signal the nutrition skill's meal reminders use.

Usage:
    python3 send_meals_reminder.py
"""
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))
from current_location import is_away_from_home  # noqa: E402
from misfire_guard import near_slot  # noqa: E402

TG_TOKEN = __import__("runpy").run_path("~/.openclaw/workspace/Skills/shared/runtime_secrets.py")["require_secret"]("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID = "1000000001"
STATE_PATH = Path("~/.openclaw/freezer_meals_reminder_state.json")

TEXT = "\U0001F501 Take out two meals from freezer to fridge \U0001F3A9"

# Cron-scheduled for 20:30 America/Chicago. Guards the post-restart catch-up
# run that would otherwise fire this thaw reminder in the afternoon.
SLOT = "20:30"


def main():
    # A late thaw reminder is still actionable (you can move meals to the fridge
    # at 11pm); an early one is a misfire.
    ok, reason = near_slot([SLOT], grace_min=120, late_grace_min=210)
    if not ok:
        print(f"misfire-guard: {reason}", file=sys.stderr)
        print(json.dumps({"sent": False, "skipped": "misfire"}))
        return

    if is_away_from_home():
        print(json.dumps({"sent": False, "skipped": "traveling"}))
        return

    payload = {
        "chat_id": TG_CHAT_ID,
        "text": TEXT,
        "reply_markup": {"inline_keyboard": [[
            {"text": "✅ Done", "callback_data": "confirm:meals"},
        ]]},
    }
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            result = json.loads(r.read())
    except Exception as e:
        print(json.dumps({"sent": False, "error": str(e)}))
        sys.exit(1)

    msg_id = result.get("result", {}).get("message_id", 0)
    if msg_id:
        STATE_PATH.write_text(json.dumps({"source_tg_msg_id": msg_id}))
    print(json.dumps({"sent": True, "msg_id": msg_id}))


if __name__ == "__main__":
    main()
