#!/usr/bin/env python3
"""
Fired as a step in the 8am morning-briefing runner. Checks for 🚨 critical
Pending Actions open 3+ days and sends one Telegram message per item with
"Done" / "Still open" buttons — self-sending like the other reminders,
so it works even if nothing reads its stdout.

Tapping a button runs (via callback-dispatch, deterministic, no agent turn):
  confirm:loopclose-<id>  -> loop_closer.py close --id <id> --resolution done
  skip:loopclose-<id>     -> loop_closer.py keep-open --id <id>

Usage:
    python3 send_escalations.py
"""
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))
from loop_closer import cmd_escalate, cmd_mark_pinged  # noqa: E402
from send_telegram import TG_TOKEN, TG_CHAT_ID  # noqa: E402

TIER_PREFIX = {
    1: "🚨",
    2: "⚠️ Open a week+ now —",
    3: "🔴 Two weeks+ open, no closure —",
}


def send(text: str, item_id: str):
    """Returns the sent message's id, or None on failure."""
    payload = {
        "chat_id": TG_CHAT_ID,
        "text": text,
        "reply_markup": {"inline_keyboard": [[
            {"text": "✅ Done", "callback_data": f"confirm:loopclose-{item_id}"},
            {"text": "🔴 Still open", "callback_data": f"skip:loopclose-{item_id}"},
        ]]},
    }
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            result = json.loads(r.read())
        return result.get("result", {}).get("message_id") if result.get("ok") else None
    except Exception as e:
        print(f"send failed for {item_id}: {e}", file=sys.stderr)
        return None


def main() -> None:
    due = cmd_escalate()
    sent = []
    for item in due:
        prefix = TIER_PREFIX[item["tier"]]
        text = f"{prefix} {item['text']} ({item['days_open']}d open)\n\nDone, or still working?"
        msg_id = send(text, item["id"])
        if msg_id:
            cmd_mark_pinged(item["id"], msg_id)
            sent.append(item["id"])
    print(json.dumps({"due": len(due), "sent": sent}))


if __name__ == "__main__":
    main()
