#!/usr/bin/env python3
"""
Single source of truth for what `console_light` should be showing at any
given time of day, plus a way to apply it.

Two callers:
  1. The daytime cron steps that don't need their own hardcoded values —
     currently `govee-console-afternoon` (1pm neutral hold) and
     `govee-console-warm` (4:30pm warming step). They just run this and it
     sets whatever the curve says for the current local time.
  2. `sync_zoom_lighting.py`'s `zoom-light-off-*` jobs. When a Zoom call
     ends the console must NOT hard-power-off (it used to — bug: a call
     ending at 2pm left the desk dark until the next scheduled step). It
     should return to wherever the daytime schedule would have it. This
     computes and applies that.

The CURVE below MUST stay consistent with the `console_light` cron jobs
that still carry explicit argv:
  - govee-morning-energize        07:55  on / 100% / 6500K
  - govee-focus-start             11:00  on / scene "Study"
  - govee-winddown-console        20:00  on / 50% / RGB 255,170,90
  - govee-winddown-fade-off-console 22:00 fade to off
If you retime or revalue one of those, update the matching CURVE entry here
or the post-Zoom-call restore will snap the light to a stale value.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "shared"))
from current_location import get_tz, is_away_from_home  # noqa: E402
from data_paths import path as data_path  # noqa: E402
from evening_hold import held  # noqa: E402

CONTROL_PY = str(Path(__file__).parent / "control.py")

# (start "HH:MM" local, state). The active entry is the last one whose start
# is <= now; before the first entry we're still in the previous day's final
# entry (lights off overnight).
# The console's morning step follows the alarm. 07:55 is wake + 25 min for the
# 7:30 start it was written against, so the interval was already right and only
# the anchor was missing: the BEDROOM light has followed the alarm since 2026-09,
# while the desk kept blazing 6500K at an empty chair on any late wake
# (2026-10-13). Everything later in the day is work- or evening-anchored and
# stays on the clock.
MORNING_AFTER_WAKE_MIN = 25
DEFAULT_MORNING_HHMM = "07:55"
PLAN_FILE = data_path("breakfast_anchor_plan.json")


def morning_hhmm() -> str:
    """When the desk should come up today. Falls back to the old fixed time when
    no plan is readable — a console that lights late beats one that never does."""
    try:
        wake = datetime.fromisoformat(json.loads(PLAN_FILE.read_text())["wake_at"])
    except (OSError, ValueError, KeyError, TypeError):
        return DEFAULT_MORNING_HHMM
    return (wake + timedelta(minutes=MORNING_AFTER_WAKE_MIN)).strftime("%H:%M")


CURVE: list[tuple[str, dict]] = [
    ("07:55", {"on": True, "brightness": 100, "temp": 6500}),           # morning energize
    ("11:00", {"on": True, "scene": "Study"}),                          # focus block
    ("13:00", {"on": True, "brightness": 65, "temp": 4300}),            # afternoon neutral hold — fights the post-lunch dip
    ("16:30", {"on": True, "brightness": 55, "temp": 3800}),            # late-afternoon warming, bridge to the evening gold
    ("20:00", {"on": True, "brightness": 50, "color": "255,170,90"}),   # Sunset Gold winddown
    ("22:00", {"on": False}),                                           # night / overnight — off
]


def curve() -> list:
    """Today's curve: the morning step moved to wake + 25, the rest fixed.

    Re-sorted because a late wake can push the morning step past the 11:00 focus
    block, and state_at() relies on the list being in time order."""
    rest = [entry for entry in CURVE if entry[0] != DEFAULT_MORNING_HHMM]
    return sorted([(morning_hhmm(), CURVE[0][1])] + rest, key=lambda e: e[0])


def state_at(now: datetime) -> dict:
    """The console_light state the daily curve calls for at `now`."""
    hm = now.strftime("%H:%M")
    today = curve()
    active = today[-1][1]  # overnight default, wraps from yesterday's last entry
    for start, st in today:
        if start <= hm:
            active = st
    return active


def _argv_for(st: dict) -> list[str]:
    argv = ["python3", CONTROL_PY, "--light", "console_light"]
    if not st.get("on"):
        return argv + ["--off"]
    argv.append("--on")
    if "scene" in st:
        argv += ["--scene", st["scene"]]
    if "brightness" in st:
        argv += ["--brightness", str(st["brightness"])]
    if "temp" in st:
        argv += ["--temp", str(st["temp"])]
    if "color" in st:
        argv += ["--color", st["color"]]
    return argv


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--at", help="HH:MM local override (default: now)")
    ap.add_argument("--print", action="store_true", dest="print_only",
                    help="print the computed state, don't apply it")
    ap.add_argument("--skip-if-away", action="store_true",
                    help="no-op (exit 0) when is_away_from_home() — for cron use")
    args = ap.parse_args()

    if args.skip_if_away and is_away_from_home():
        print(json.dumps({"skipped": "away from home"}))
        return

    now = datetime.now(get_tz())
    if args.at:
        h, m = map(int, args.at.split(":"))
        now = now.replace(hour=h, minute=m, second=0, microsecond=0)

    st = state_at(now)

    if args.print_only:
        print(json.dumps({"at": now.strftime("%H:%M"), "state": st}))
        return

    # No flag: while the night light hold is live there IS no scheduled state to
    # apply — that includes the post-Zoom-call restore, which would otherwise snap
    # the console back to 65%/4300K in the middle of the user's evening.
    if held("console_light"):
        print(json.dumps({"at": now.strftime("%H:%M"), "skipped": "night light hold is on"}))
        return

    argv = _argv_for(st)
    result = subprocess.run(argv, capture_output=True, text=True)
    print(json.dumps({
        "at": now.strftime("%H:%M"),
        "state": st,
        "applied": argv[3:],
        "control_exit": result.returncode,
    }))
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
