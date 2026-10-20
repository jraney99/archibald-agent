#!/usr/bin/env python3
"""
T-24h check-in reminder — fired by a cron job (daemon.py::_schedule_flight_reminders
section 2). The message text is precomposed by daemon.py at booking time (it's
low-confidence/static either way, same as travel_reminder.py's "rough" stage)
and just passed through here via --text.

The one thing that has to happen at run time rather than booking time: when
--ask-bag is set, this writes ~/.openclaw/pending_checked_bag.json so
RULES.md can route the user's yes/no reply straight to set_checked_bag.py. Whether
he's checking a bag usually isn't decided days out at booking time, so this is
the earliest point it's worth asking.
"""
import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PENDING       = Path("~/.openclaw/pending_checked_bag.json")
SEND_TELEGRAM = "~/.openclaw/workspace/Skills/shared/send_telegram.py"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--text", required=True)
    p.add_argument("--key", required=True)
    p.add_argument("--flight", required=True)
    p.add_argument("--confirmation", default="")
    p.add_argument("--ask-bag", action="store_true")
    args = p.parse_args()

    if args.ask_bag:
        PENDING.write_text(json.dumps({
            "key": args.key,
            "flight": args.flight,
            "confirmation": args.confirmation,
            "asked_at": datetime.now().isoformat(),
        }, indent=2))

    r = subprocess.run(
        ["python3", SEND_TELEGRAM, "--text", args.text],
        capture_output=True, text=True, timeout=30,
    )
    if r.returncode != 0:
        print(r.stderr.strip(), file=sys.stderr)
        sys.exit(r.returncode)
    print(f"check-in reminder sent for {args.key}" + (" (bag question pending)" if args.ask_bag else ""))


if __name__ == "__main__":
    main()
