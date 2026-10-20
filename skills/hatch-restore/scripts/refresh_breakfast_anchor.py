#!/usr/bin/env python3
"""
Pre-dawn re-check of the Breakfast Anchor send time against the LIVE Hatch
alarm.

The 21:00 nightly sync schedules the Breakfast Anchor one-shot at
(computed wake + N) and writes that wake time to the Hatch. But the user can
still change the alarm himself after 21:00 — at bedtime, in the Hatch app —
and nothing looked at the device again, so the reminder kept firing off the
old time ("7:30 unless I change it", 2026-09-19). This runs once while he's
asleep, reads what the Hatch will actually do this morning, and moves the
one-shot if that differs from what it was built from.

Silent by design: it never sends a Telegram message. Any failure (Hatch
unreachable, alarm switched off, cron CLI error) leaves the 21:00 schedule
or the 08:30 backstop to cover the morning.

Usage:
    python3 refresh_breakfast_anchor.py
    python3 refresh_breakfast_anchor.py --from-alarm-change   # alarm.py just changed the alarm
"""
import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import nightly_wake_and_workout_sync as nightly  # noqa: E402
import sync_alarm  # noqa: E402
from current_location import get_tz, is_away_from_home  # noqa: E402
from misfire_guard import within_slot  # noqa: E402

# Cron-scheduled for 03:00. A catch-up run later in the day would re-create
# the one-shot for a morning that's already happened.
MISFIRE_SLOT = (1, 6)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-alarm-change", action="store_true",
                    help="Run regardless of the hour: alarm.py calls this as a follower the "
                         "moment the user changes the alarm, which is usually in the evening.")
    args = ap.parse_args()

    # The guard is here for CATCH-UP runs of the 03:00 cron, which would re-create the
    # one-shot for a morning that has already happened. A follower run is not a catch-up —
    # The user just changed the alarm — and blocking it left the first meal reminder on the old
    # wake time until 3am (2026-10-06: still 08:00 for an 08:30 wake, one failed 3am job away
    # from a reminder before he was up).
    if not args.from_alarm_change:
        ok, reason = within_slot(None, MISFIRE_SLOT[0], MISFIRE_SLOT[1])
        if not ok:
            print(f"misfire-guard: {reason} — skipping breakfast anchor refresh", file=sys.stderr)
            return
    if is_away_from_home():
        print("away from home — nothing to refresh")
        return

    config = sync_alarm.load_config()
    tz = get_tz()
    try:
        wake_time = sync_alarm.read_current_wake_time(config)
    except Exception as e:
        print(f"couldn't read the Hatch alarm ({str(e)[:200]}) — leaving the schedule as is", file=sys.stderr)
        return
    if wake_time is None:
        print("Hatch alarm is off or unreadable — leaving the schedule as is")
        return

    now = datetime.now(tz)
    wake_dt = datetime.combine(now.date(), wake_time.replace(second=0, microsecond=0), tzinfo=tz)
    if wake_dt <= now:
        # Tomorrow morning — the normal case for an evening change. (Same fix as
        # refresh_morning_briefing.py got on 2026-10-01; this copy still had the bug.)
        wake_dt += timedelta(days=1)

    try:
        planned = datetime.fromisoformat(json.loads(nightly.BREAKFAST_PLAN_FILE.read_text())["wake_at"])
    except Exception:
        planned = None
    if planned == wake_dt:
        print(f"alarm still {wake_dt:%H:%M} — schedule unchanged")
        return

    nightly.schedule_breakfast_anchor_reminder(wake_dt, tz, config, send_now_on_failure=False)
    built_from = f"{planned:%a %H:%M}" if planned else "nothing on file"
    print(f"alarm is {wake_dt:%H:%M} (schedule was built from {built_from}) — breakfast anchor rescheduled")


if __name__ == "__main__":
    main()
