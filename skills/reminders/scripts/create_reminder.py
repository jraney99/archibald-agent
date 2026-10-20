#!/usr/bin/env python3
"""
Create a one-shot reminder: a cron job that fires once at a given local
time, delivers a Telegram message, then deletes itself. The cron job is
the only record of the reminder — no separate JSON store to maintain or
let go stale.

Usage:
    python3 create_reminder.py --text "Take pants to tailor" --at "2026-07-07T09:00:00"
    python3 create_reminder.py --text "Call the dentist" --at "2026-07-10T14:30:00" --tz America/Los_Angeles
"""
import argparse
import json
import re
import subprocess
import sys
import time

sys.path.insert(0, "~/.openclaw/workspace/Skills/shared")
from current_location import get_tz_name  # noqa: E402

OPENCLAW_BIN = "/opt/homebrew/bin/openclaw"
TG_CHAT_ID = "1000000001"
SEND_TELEGRAM = "~/.openclaw/workspace/Skills/shared/send_telegram.py"


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:40] or "reminder"


def main():
    parser = argparse.ArgumentParser(description="Create a one-shot Telegram reminder")
    parser.add_argument("--text", required=True, help="What to remind the user about")
    parser.add_argument("--at", required=True, help="Local datetime to fire, ISO format e.g. 2026-07-07T09:00:00")
    parser.add_argument("--tz", default=None, help="IANA timezone; defaults to the user's current location")
    args = parser.parse_args()

    tz = args.tz or get_tz_name()
    name = f"reminder-{slugify(args.text)}-{int(time.time())}"
    message = f"\U0001F514 {args.text} \U0001F3A9"

    # Command payload + --no-deliver: send_telegram.py posts the reminder itself.
    # An agentTurn + --announce job would prepend a "[cron:<id> <name>]"
    # provenance header to the message (and burn an LLM turn to echo static text).
    cmd = [
        OPENCLAW_BIN, "cron", "add",
        "--at", args.at,
        "--tz", tz,
        "--name", name,
        "--description", f"Reminder: {args.text}",
        "--command-argv", json.dumps(["python3", SEND_TELEGRAM, "--text", message]),
        "--no-deliver",
        "--channel", "telegram",
        "--to", TG_CHAT_ID,
        "--best-effort-deliver",
        "--delete-after-run",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        print(f"ERROR: {result.stderr.strip()}")
        sys.exit(1)

    print(f'Reminder set for {args.at} ({tz}): "{args.text}"')


if __name__ == "__main__":
    main()
