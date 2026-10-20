#!/usr/bin/env python3
"""
Post-flight capture prompt — fired ~45 min after departure by a cron job
(daemon.py::_schedule_flight_reminders section 4).

Writes ~/.openclaw/pending_flight_log.json and sends one Telegram
message asking for exactly two numbers: actual security wait, and how long
The user sat at the gate. RULES.md routes his reply to log_flight.py.

Two numbers, nothing else — per the spec, any more friction and it won't get
filled in.
"""
import argparse
import json
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

PENDING = Path("~/.openclaw/pending_flight_log.json")
STATE_FILE = Path("~/.openclaw/flight_timing_state.json")
TG_TOKEN   = __import__("runpy").run_path("~/.openclaw/workspace/Skills/shared/runtime_secrets.py")["require_secret"]("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID = "1000000001"


def send_tg(text: str) -> None:
    payload = json.dumps({"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML"}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        data=payload, headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        json.loads(r.read())


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--flight", required=True)
    p.add_argument("--origin", required=True)
    p.add_argument("--dest", required=True)
    p.add_argument("--departure", required=True)
    p.add_argument("--terminal", default="")
    # Accepted so daemon.py can embed it in the cron command string and
    # _cancel_stale_flight_reminders can match this job by confirmation on a
    # reschedule. Not otherwise used here.
    p.add_argument("--confirmation", default="")
    args = p.parse_args()

    dep_dt = datetime.fromisoformat(args.departure)
    key = f"{args.flight.replace(' ', '')}-{dep_dt.strftime('%Y%m%d')}"

    predicted = {}
    try:
        predicted = json.loads(STATE_FILE.read_text()).get(key, {})
    except Exception:
        pass

    PENDING.write_text(json.dumps({
        "key": key,
        "flight": args.flight,
        "origin": args.origin,
        "dest": args.dest,
        "terminal": args.terminal,
        "departure": args.departure,
        "departure_hhmm": dep_dt.strftime("%H:%M"),
        "predicted_leave": predicted.get("computed_leave"),
        "predicted_leave_display": predicted.get("leave_display"),
        "asked_at": datetime.now().isoformat(),
    }, indent=2))

    send_tg(
        f"<b>{args.flight} {args.origin} → {args.dest}</b> just landed. Two numbers to sharpen your airport-timing estimates:\n\n"
        f"<b>1. Security</b> — minutes in the TSA line (joined line → cleared the scanner)\n"
        f"<b>2. Gate wait</b> — minutes sitting at the gate with nothing to do (reached gate → boarding started)\n\n"
        f"Reply with both, security first — e.g. <code>8 22</code> means 8 min in security, 22 min waiting at the gate.\n"
        f"Reply <code>skip</code> to drop it.\n\n"
        f"🎩"
    )
    print(f"flight-log prompt sent for {key}")


if __name__ == "__main__":
    main()
