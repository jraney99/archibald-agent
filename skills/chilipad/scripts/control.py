#!/usr/bin/env python3
"""
Set the ChiliPad: on/off and temperature. Prints one JSON line.

    python3 control.py --temp 72 --on       # set 72F and start the pump
    python3 control.py --phase entry --on   # the warm get-into-bed temperature
    python3 control.py --phase base         # tonight's sleeping temperature
    python3 control.py --temp max-cool
    python3 control.py --off                # standby (pump off)

Scheduled callers add the two guards, and BOTH must be re-listed every time a
cron's argv is rewritten — `cron edit --command-argv` replaces the whole argv, so
a flag left out is a flag silently dropped (that is how the govee misfire guard
vanished for weeks before 2026-09-09):

    --skip-if-away                      no-op when the user is not home
    --expected-start HH:MM              no-op on a gateway catch-up run
    --late-grace-min N                  how late a real run may still be

Order matters: with a temperature and --on together the temperature is set first,
so the pump never spends a minute running at last night's setting.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(SCRIPTS_DIR.parent.parent / "shared"))

from current_location import is_away_from_home  # noqa: E402
from misfire_guard import near_slot  # noqa: E402
from night_plan import phase_temps  # noqa: E402
import manual_hold  # noqa: E402
from sleepme_client import (  # noqa: E402
    SleepMeClient, SleepMeError, clamp_f, describe_temp, load_config,
    parse_temp_arg, resolve_device,
)
from trial import tonight_base  # noqa: E402

MISFIRE_EARLY_GRACE_MIN = 20
# An hour late still beats a warm bed: the next scheduled step is hours away.
DEFAULT_LATE_GRACE_MIN = 60


def phase_temp(name: str, cfg: dict) -> tuple[str, int, str]:
    """A night phase -> (api_field, value, why).

    The phases exist so a cron's argv carries "--phase entry" rather than a
    number. The temperature is resolved at FIRE TIME from tonight's base (the
    trial's current rung, or base_temp_f once it has settled) — so stepping the
    trial, or changing a number in config, moves the night with nothing to
    re-schedule. `entry` is absolute; `rem` is the floor plus its lift.
    """
    base, why = tonight_base(cfg)
    temps = phase_temps(cfg, base)
    if name in ("rem", "prewake"):
        # Both are retired lifts (midpoint, then pre-wake); the night ends with the pump OFF at
        # early_off_time instead. A job not yet retimed holds the floor rather than failing.
        name = "base"
    if name not in temps:
        raise SleepMeError(f"unknown phase {name!r} — use {', '.join(temps)}")
    return "set_temperature_f", clamp_f(temps[name]), why


def main() -> None:
    ap = argparse.ArgumentParser(description="Control the ChiliPad Dock Pro.")
    ap.add_argument("--device", help="Nickname (default: the one in chilipad_config.json).")
    ap.add_argument("--temp", help="Temperature: 60, 21C, max-cool, max-heat.")
    ap.add_argument("--phase", choices=("entry", "base", "prewake", "rem"),  # the last two: retired, see phase_temp
                    help="Tonight's base temperature plus this phase's offset, resolved at fire "
                         "time. What the scheduled jobs use instead of a hardcoded number.")
    ap.add_argument("--on", action="store_true", help="thermal_control_status = active.")
    ap.add_argument("--off", action="store_true", help="thermal_control_status = standby.")
    ap.add_argument("--check-water", action="store_true",
                    help="Also read the water level and report it. Worth one extra request on "
                         "the run that starts the night: a dry reservoir means the pad does "
                         "nothing until morning, and nothing else would notice.")
    ap.add_argument("--skip-if-away", action="store_true",
                    help="No-op (exit 0) when is_away_from_home() is true. Put this on every "
                         "scheduled job — heating an empty bed at home while he is travelling.")
    ap.add_argument("--expected-start", metavar="HH:MM",
                    help="This job's own scheduled time. No-op when the clock is more than 20 "
                         "min early or --late-grace-min late (a gateway catch-up, not a real "
                         "run). Put it on every scheduled job; omit it when run by hand.")
    ap.add_argument("--late-grace-min", type=int, default=DEFAULT_LATE_GRACE_MIN,
                    help=f"With --expected-start (default {DEFAULT_LATE_GRACE_MIN}).")
    args = ap.parse_args()

    if args.on and args.off:
        print(json.dumps({"error": "--on and --off are contradictory"}))
        sys.exit(2)
    if args.temp and args.phase:
        print(json.dumps({"error": "--temp and --phase are contradictory"}))
        sys.exit(2)
    if not (args.on or args.off or args.temp or args.phase):
        print(json.dumps({"error": "nothing to do — pass --on, --off, --temp or --phase"}))
        sys.exit(2)

    if args.expected_start:
        ok, reason = near_slot(args.expected_start, grace_min=MISFIRE_EARLY_GRACE_MIN,
                               late_grace_min=args.late_grace_min)
        if not ok:
            print(json.dumps({"skipped": reason}))
            return

    if args.skip_if_away and is_away_from_home():
        print(json.dumps({"skipped": "away from home"}))
        return

    # The user setting the bed himself outranks tonight's plan until the morning (manual_hold.py).
    # A scheduled step that would move the TEMPERATURE stands down; the morning-off still runs,
    # because standby is where the night ends anyway, and it hands the next night back.
    scheduled = bool(args.expected_start)
    if scheduled:
        live = manual_hold.hold()
        if live and (args.temp or args.phase):
            print(json.dumps({"skipped": f"Master Wayne set the bed himself ({live.get('set')}) — "
                                         f"holding that until the morning"}))
            return
        if live and args.off:
            manual_hold.clear()

    try:
        cfg = load_config() if args.phase else {}
        device_id, nickname = resolve_device(args.device)
        field = value = None
        done = {"device": nickname}
        if args.phase:
            field, value, why = phase_temp(args.phase, cfg)
            done["phase"] = args.phase
            done["base"] = why
        elif args.temp:
            field, value = parse_temp_arg(args.temp)

        client = SleepMeClient()

        # Temperature before status, so --temp --on never runs the pump at the
        # previous setpoint.
        if field is not None:
            client.set_temperature(device_id, field, value)
            done["temperature"] = describe_temp(value, "C" if field.endswith("_c") else "F")
        if args.on:
            client.set_thermal_status(device_id, "active")
            done["status"] = "active"
        elif args.off:
            client.set_thermal_status(device_id, "standby")
            done["status"] = "standby"

        if args.check_water:
            status = (client.get_device(device_id) or {}).get("status") or {}
            done["water_level"] = status.get("water_level")
            done["water_low"] = status.get("is_water_low")
            done["connected"] = status.get("is_connected")
            if status.get("is_water_low"):
                # Said out loud in the cron's diagnostic rather than sent to
                # Telegram: a new automated message needs the user's go-ahead on the
                # exact wording first (CLAUDE.md's rollout gate).
                done["warning"] = "water is low — the pad will not hold temperature"
    except SleepMeError as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)

    if not scheduled:
        # Recorded only now, after the bed really changed: a hold that silenced tonight's
        # schedule on the back of a failed call would be the worst of both.
        what = done.get("temperature") or ("standby" if args.off else "on")
        rec = manual_hold.start(what, cfg=load_config())
        done["holding_until"] = datetime.fromisoformat(rec["until"]).strftime("%-I:%M %p")

    print(json.dumps(done))


if __name__ == "__main__":
    main()
