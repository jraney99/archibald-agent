#!/usr/bin/env python3
"""
The nightly putting cue: one message, one tap. Fired directly by cron
(command payload, delivery mode "none") so there is no agent turn in the path
that could reword it, time out, or double-send — the same shape as the
hydration nudge and the freezer reminder, for the same reasons.

Tapping "Done" logs the session and edits this message in place (log_putting.py);
not tapping is a miss and nothing chases him about it. The buttons stay live all
night, so coming in late he can still tap it.

Travelling (is_away_from_home): no cue is sent, and the night is logged 'away'
so the streak steps over it instead of breaking. Writing that entry is the point
— without it, nothing later could tell a travel night from a night he blew off.

    python3 send_putting_cue.py             # the real thing
    python3 send_putting_cue.py --dry-run   # print the exact message, send nothing
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))
import putting_status as status  # noqa: E402
from current_location import is_away_from_home  # noqa: E402
from data_paths import path as data_path  # noqa: E402
from misfire_guard import near_slot  # noqa: E402
from runtime_secrets import require_secret  # noqa: E402

TG_CHAT_ID = "1000000001"
STATE_PATH = data_path("putting_cue_state.json")
STREAM = "putting:nightly"

# Fired after the slot may be a genuine gateway recovery and a 10:30pm cue still
# beats none; fired before it never is (2026-09-08: a restart sweep dumped a whole
# evening of reminders at 2:27pm).
BEFORE_GRACE_MIN = 20
AFTER_GRACE_MIN = 90


def save_state(message_id: int, text: str, night: str) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps({
        "message_id": message_id,
        "text": text,
        "night_of": night,
        "sent_at": int(time.time()),
    }))


def compose(streak: int, goal: int) -> str:
    text = f"⛳ {goal} putts before bed, Master Wayne."
    if streak:
        night_word = "night" if streak == 1 else "nights"
        text += f"\n\n\U0001F525 {streak} {night_word} running."
    return text


def send(text: str) -> dict:
    payload = {
        "chat_id": TG_CHAT_ID,
        "text": text,
        "reply_markup": {"inline_keyboard": [[
            {"text": "✅ Done", "callback_data": "confirm:putting"},
            {"text": "Not tonight", "callback_data": "skip:putting"},
        ]]},
    }
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{require_secret('TELEGRAM_BOT_TOKEN')}/sendMessage",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="print the message that would be sent and exit")
    args = parser.parse_args()

    entries = status.load_entries()
    tonight = status.night_of()
    goal = status.goal_putts()
    # Last night's streak is what tonight's cue reports: tonight isn't done yet.
    streak = status.streak_nights(entries, tonight - timedelta(days=1))
    text = compose(streak, goal)

    if args.dry_run:
        print(text)
        print(json.dumps({"dry_run": True, "night_of": tonight.isoformat(), "streak": streak,
                          "buttons": ["confirm:putting", "skip:putting"]}))
        return

    ok, reason = near_slot([status.cue_time()], grace_min=BEFORE_GRACE_MIN,
                           late_grace_min=AFTER_GRACE_MIN)
    if not ok:
        print(f"misfire-guard: {reason}", file=sys.stderr)
        print(json.dumps({"sent": False, "skipped": "misfire"}))
        return

    already = status.status_for_night(entries, tonight)
    if already == status.DONE:
        print(json.dumps({"sent": False, "skipped": "already logged tonight"}))
        return

    if status.skip_while_away() and is_away_from_home():
        status.append_entry(status.AWAY, night=tonight)
        print(json.dumps({"sent": False, "skipped": "away from home", "logged": "away"}))
        return

    try:
        receipt = send(text)
    except Exception as e:
        print(json.dumps({"sent": False, "error": str(e)}))
        sys.exit(1)

    msg_id = receipt.get("result", {}).get("message_id", 0)
    # Last night's cue — answered or not — goes on file and out of the chat, so
    # one putting message lives there at a time (The user's 2026-09-20 declutter
    # rule). Never before the new send: a failed send must not empty the chat.
    removed = 0
    try:
        from chat_cleanup import replace_stream
        removed = replace_stream(STREAM, [msg_id], text, skill="golf-putting")
    except Exception:
        pass
    save_state(msg_id, text, tonight.isoformat())
    print(json.dumps({"sent": True, "msg_id": msg_id, "streak": streak, "removed_previous": removed}))


if __name__ == "__main__":
    main()
