#!/usr/bin/env python3
"""
Set the Nest: target temperature, mode, eco. Prints one JSON line.

    python3 control.py --temp 70            # set the target, in Fahrenheit
    python3 control.py --mode heat|cool|heatcool|off
    python3 control.py --eco on|off
    python3 control.py --preset sleep|day   # the temperatures from config

Scheduled callers add the guards, and ALL of them must be re-listed whenever a
cron's argv is rewritten — `cron edit --command-argv` replaces the whole argv, so
a flag left out is a flag silently dropped:

    --skip-if-away                   no-op when the user is not home
    --expected-start HH:MM           no-op on a gateway catch-up run
    --late-grace-min N               how late a real run may still be

`--temp` does the fiddly part itself: clears eco (which otherwise rejects
setpoints outright), picks SetHeat/SetCool/SetRange to match the current mode, and
brackets the target in HEATCOOL because that mode needs both bounds with cool
above heat. See nest_client.set_temperature_f.

One deliberate asymmetry with the ChiliPad's control.py: this one spends a READ
before every write, because the right command depends on the current mode. That
is 2 of Google's 5-per-minute device budget, which is why nothing here loops.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(SCRIPTS_DIR.parent.parent / "shared"))

from current_location import is_away_from_home  # noqa: E402
from misfire_guard import near_slot  # noqa: E402
from nest_client import NestClient, NestError, load_config  # noqa: E402

MISFIRE_EARLY_GRACE_MIN = 20
# The house takes a while to move, so a late run still does something useful.
DEFAULT_LATE_GRACE_MIN = 90


def preset_temp(name: str, cfg: dict) -> float:
    key = {"sleep": "sleep_temp_f", "day": "day_temp_f"}.get(name)
    if key is None:
        raise NestError(f"unknown preset {name!r} — use 'sleep' or 'day'")
    if cfg.get(key) is None:
        raise NestError(f"{key} is not set in nest_config.json")
    return float(cfg[key])


def main() -> None:
    ap = argparse.ArgumentParser(description="Control the Nest thermostat.")
    ap.add_argument("--device", help="Nickname (default: the one in nest_config.json).")
    ap.add_argument("--temp", type=float, help="Target temperature in Fahrenheit.")
    ap.add_argument("--preset", choices=("sleep", "day"),
                    help="Use sleep_temp_f / day_temp_f from config instead of --temp. What "
                         "the scheduled jobs carry, so changing the number in config is enough.")
    ap.add_argument("--mode", choices=("heat", "cool", "heatcool", "off"))
    ap.add_argument("--eco", choices=("on", "off"))
    ap.add_argument("--skip-if-away", action="store_true",
                    help="No-op (exit 0) when is_away_from_home() is true.")
    ap.add_argument("--expected-start", metavar="HH:MM",
                    help="This job's own scheduled time. No-op on a gateway catch-up run.")
    ap.add_argument("--late-grace-min", type=int, default=DEFAULT_LATE_GRACE_MIN,
                    help=f"With --expected-start (default {DEFAULT_LATE_GRACE_MIN}).")
    args = ap.parse_args()

    if args.temp is not None and args.preset:
        print(json.dumps({"error": "--temp and --preset are contradictory"}))
        sys.exit(2)
    if args.temp is None and not (args.preset or args.mode or args.eco):
        print(json.dumps({"error": "nothing to do — pass --temp, --preset, --mode or --eco"}))
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

    try:
        cfg = load_config() if args.preset else {}
        from nest_client import resolve_device  # noqa: E402
        name, nickname = resolve_device(args.device)
        client = NestClient()
        done = {"device": nickname}

        # Mode first: a setpoint command has to match the mode, so changing both in
        # one call would otherwise race its own read.
        if args.mode:
            client.set_mode(name, args.mode)
            done["mode"] = args.mode.upper()
        if args.eco:
            client.set_eco(name, args.eco == "on")
            done["eco"] = "MANUAL_ECO" if args.eco == "on" else "OFF"

        target = preset_temp(args.preset, cfg) if args.preset else args.temp
        if target is not None:
            done.update(client.set_temperature_f(name, target))
            if args.preset:
                done["preset"] = args.preset
    except NestError as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)

    print(json.dumps(done))


if __name__ == "__main__":
    main()
