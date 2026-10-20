#!/usr/bin/env python3
"""
Create a recurring reminder: a persistent cron job that fires on a repeating
schedule and delivers a Telegram message each time. Unlike Todoist, there is
no task "instance" to mark done/overdue between firings — it just nudges
again next cycle regardless of whether the last one was acted on. That is
the deliberate fix for the pileup problem: the recurrence lives in the
scheduler, not in a list that can rot.

Two schedule modes (pick one):
  --every <duration>   Simple interval, e.g. "45d" — use for cadences that
                        don't land on a clean calendar boundary (every N days).
  --cron <expr> --tz    Calendar-precise schedule, e.g. "0 19 * * 4" for
                        every Thursday at 7pm — use when the cadence should
                        anchor to a specific weekday/day-of-month/month.

Usage:
    python3 create_recurring_reminder.py --text "Check mail" --cron "0 19 * * 4"
    python3 create_recurring_reminder.py --text "Change toothbrush" --every 45d
    python3 create_recurring_reminder.py --text "Replace water filter" --cron "0 9 9 2,8 *"
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
    parser = argparse.ArgumentParser(description="Create a recurring Telegram reminder")
    parser.add_argument("--text", required=True, help="What to remind the user about, each cycle")
    parser.add_argument("--every", help='Simple interval, e.g. "45d"')
    parser.add_argument("--cron", help='Cron expression, e.g. "0 19 * * 4"')
    parser.add_argument("--tz", default=None, help="IANA timezone; defaults to the user's current location (only used with --cron)")
    args = parser.parse_args()

    if bool(args.every) == bool(args.cron):
        print("ERROR: specify exactly one of --every or --cron")
        sys.exit(1)

    name = f"recurring-{slugify(args.text)}-{int(time.time())}"
    message = f"\U0001F501 {args.text} \U0001F3A9"

    # Command payload + --no-deliver: send_telegram.py posts the reminder itself.
    # An agentTurn + --announce job would prepend a "[cron:<id> <name>]"
    # provenance header to the message (and burn an LLM turn to echo static text).
    cmd = [
        OPENCLAW_BIN, "cron", "add",
        "--name", name,
        "--description", f"Recurring reminder: {args.text}",
        "--command-argv", json.dumps(["python3", SEND_TELEGRAM, "--text", message]),
        "--no-deliver",
        "--channel", "telegram",
        "--to", TG_CHAT_ID,
        "--best-effort-deliver",
    ]
    if args.every:
        cmd += ["--every", args.every]
    else:
        cmd += ["--cron", args.cron, "--tz", args.tz or get_tz_name()]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        print(f"ERROR: {result.stderr.strip()}")
        sys.exit(1)

    schedule = f"every {args.every}" if args.every else f"cron {args.cron}"
    print(f'Recurring reminder set ({schedule}): "{args.text}"')


if __name__ == "__main__":
    main()
