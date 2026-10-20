#!/usr/bin/env python3
"""A temperature the user set by hand holds until the morning; the scheduled steps stand down.

    python3 manual_hold.py            # is a hold live, and until when
    python3 manual_hold.py --clear    # hand the night back to the schedule

WHY (2026-10-06, the skill's first live night): at 23:19 the user said "I'm too cold rn". ramp.py
was mid-descent — 76°F on its way to 72°F — so the warmer setting he asked for would have been
walked back down within four minutes, and chilipad-rem would have cooled the bed again at 03:07.
The ramp had to be killed by hand. That is the same bug the Govee sunrise had, where saying
"stop" was undone by the ramp's next step (govee-lights/SKILL.md § Stopping a sunrise).

So: every MANUAL control.py call (one without --expected-start, i.e. not a cron job) starts a
hold, and while it is live:
  - ramp.py stops where it is, rather than continuing to walk the temperature,
  - the entry and descend jobs skip instead of overriding him,
  - the 04:30 early-off and the morning-off still run and clear the hold: stopping the pump is
    the WARM option (the bed settles at his own body heat), it is what he asked for on
    2026-10-07 in place of any lift, and it means tomorrow night is normal with nothing to
    re-enable by hand.

The hold carries its own expiry, so a missed morning-off can't silence tomorrow's schedule
either: an expired hold is no hold.
"""
import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent / "shared"))
from data_paths import path  # noqa: E402

HOLD = path("chilipad_hold.json")
FALLBACK_OFF_TIME = "10:00"  # only if neither the cron store nor the config can say


def _off_time(cfg):
    """(hour, minute) of the morning-off, from the cron job that actually fires it."""
    try:
        import night_plan
        job = night_plan._cron_job((cfg.get("jobs") or {}).get("off") or "")
        minute, hour = (job.get("schedule") or {}).get("expr", "").split()[:2]
        return int(hour), int(minute)
    except Exception:  # noqa: BLE001 — no cron store, a reworded schedule, no job
        hhmm = (cfg or {}).get("no_alarm_off_time") or FALLBACK_OFF_TIME
        try:
            hour, minute = (int(p) for p in hhmm.split(":"))
            return hour, minute
        except ValueError:
            return 10, 0


def until_morning(now, cfg=None):
    """When a hold started now should expire: the next morning-off."""
    hour, minute = _off_time(cfg or {})
    off = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return off if off > now else off + timedelta(days=1)


def _record():
    try:
        data = json.loads(HOLD.read_text())
    except (OSError, ValueError):
        return None
    try:
        datetime.fromisoformat(data["until"])
    except (KeyError, TypeError, ValueError):
        return None
    return data


def hold(now=None):
    """The LIVE hold, or None. An expired hold is no hold at all."""
    rec = _record()
    if not rec:
        return None
    now = now or datetime.now().astimezone()
    return rec if now < datetime.fromisoformat(rec["until"]) else None


def held(now=None):
    return hold(now) is not None


def start(what, now=None, cfg=None):
    """Record that the user set the bed himself. `what` is what he set, in words."""
    now = now or datetime.now().astimezone()
    until = until_morning(now, cfg)
    HOLD.parent.mkdir(parents=True, exist_ok=True)
    rec = {"at": now.isoformat(), "until": until.isoformat(), "set": what}
    HOLD.write_text(json.dumps(rec, indent=1))
    return rec


def clear():
    HOLD.unlink(missing_ok=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clear", action="store_true", help="End the hold; the schedule takes the night back.")
    args = ap.parse_args()
    if args.clear:
        clear()
        print(json.dumps({"hold": "cleared"}))
        return
    live = hold()
    if live:
        until = datetime.fromisoformat(live["until"])
        print(json.dumps({"hold": "on", "set": live.get("set"), "until": until.strftime("%-I:%M %p")}))
    else:
        print(json.dumps({"hold": "off"}))


if __name__ == "__main__":
    main()
