#!/usr/bin/env python3
"""
Backup wake-up nudge for when the user's travelling and the Hatch alarm is
disabled (see sync_alarm.py's away path). His phone alarm is the primary
wake-up; this is the failsafe if he forgot to set it, set it wrong, or it
didn't fire. Scheduled as a one-shot cron job at the computed wake time by
nightly_wake_and_workout_sync.py's schedule_travel_wake_nudge().

Sends the wake message a few times, spaced a few minutes apart, then stops —
no acknowledge loop (deliberately: Telegram callback-button ack flows have
been a recurring source of routing bugs in this codebase, and a bounded
handful of messages is a good enough failsafe for something that's already
the backup to a real alarm). The user replying "up" just lands in the session
like any message; the script runs its short course regardless.

Runs in-process for repeats * interval seconds, so the cron job needs a
timeoutSeconds comfortably above that.

Usage:
    python3 travel_wake_nudge.py --message "..." [--repeats 3] [--interval-seconds 240]
"""
import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

TG_TOKEN = __import__("runpy").run_path("~/.openclaw/workspace/Skills/shared/runtime_secrets.py")["require_secret"]("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID = "1000000001"

SESSIONS_FILE = Path("~/.openclaw/agents/main/sessions/sessions.json")
TELEGRAM_SESSION_KEY = "agent:main:telegram:direct:1000000001"


def owner_replied_since(start_ms: float) -> bool:
    """True once the user has interacted with the Telegram session after this run
    began — the point of a backup alarm is to stop the moment he's up."""
    try:
        s = json.loads(SESSIONS_FILE.read_text()).get(TELEGRAM_SESSION_KEY, {})
        return float(s.get("lastInteractionAt", 0)) > start_ms
    except Exception:
        return False


def send(text: str) -> None:
    payload = json.dumps({"chat_id": TG_CHAT_ID, "text": text}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=15)
    except Exception as e:  # never let one failed send abort the rest
        print(json.dumps({"send_failed": str(e)[:200]}), flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--message", required=True)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--interval-seconds", type=int, default=240)
    args = ap.parse_args()

    repeats = max(1, args.repeats)
    start_ms = time.time() * 1000
    body = args.message.lstrip("⏰ ").strip()  # avoid a doubled clock on repeats

    for i in range(repeats):
        if i and owner_replied_since(start_ms):
            print(json.dumps({"stopped_early": True, "after_nudge": i}), flush=True)
            return
        if i == 0:
            text = args.message
        elif i == repeats - 1:
            text = f"⏰ Last nudge — still asleep? {body}"
        else:
            text = f"⏰ Nudge {i + 1}/{repeats} — {body}"
        send(text)
        print(json.dumps({"nudge": i + 1, "of": repeats}), flush=True)
        if i < repeats - 1:
            time.sleep(max(0, args.interval_seconds))


if __name__ == "__main__":
    main()
