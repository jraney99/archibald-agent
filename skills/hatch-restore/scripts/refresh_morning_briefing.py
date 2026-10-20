#!/usr/bin/env python3
"""
Re-point the morning briefing at the LIVE Hatch alarm.

The 21:00 sync schedules the briefing one-shot at (computed wake + 15). The user can
still move the alarm afterwards — in the Hatch app at bedtime, or by telling
Archibald — and nothing would look at the device again, so the briefing would keep
firing off the old time. Same problem, and same fix, as refresh_breakfast_anchor.py
(2026-09-19); this is its twin for the briefing.

Run by alarm.py whenever it changes the wake time, and pre-dawn alongside the
breakfast refresh.

Silent by design: it never sends a Telegram message. Any failure (Hatch unreachable,
alarm switched off, cron CLI error) leaves the 21:00 schedule standing.

Usage:
    python3 refresh_morning_briefing.py
"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import nightly_wake_and_workout_sync as nightly  # noqa: E402
import sync_alarm  # noqa: E402
from current_location import get_tz, is_away_from_home  # noqa: E402


def main() -> None:
    # No Hatch to read while travelling — the 21:00 computed time stands, and it is
    # the phone alarm he is actually waking to.
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
        # An alarm time that has passed today means tomorrow morning — which is the normal case,
        # because the user sets tomorrow's alarm in the evening. This used to bail here, so a change
        # made after the 9pm sync left tomorrow's briefing on the OLD wake time (2026-10-01: an
        # 8:30pm change to an 8:30am alarm, briefing still built from 7:30).
        wake_dt += timedelta(days=1)

    try:
        planned = datetime.fromisoformat(json.loads(nightly.BRIEFING_PLAN_FILE.read_text())["wake_at"])
    except Exception:
        planned = None
    if planned == wake_dt:
        print(f"alarm still {wake_dt:%H:%M} — briefing unchanged")
        return

    nightly.schedule_morning_briefing(wake_dt, tz, config, send_now_on_failure=False)
    built_from = f"{planned:%a %H:%M}" if planned else "nothing on file"
    print(f"alarm is {wake_dt:%H:%M} (briefing was built from {built_from}) — briefing rescheduled")


if __name__ == "__main__":
    main()
