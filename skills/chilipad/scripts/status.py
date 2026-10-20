#!/usr/bin/env python3
"""
What the ChiliPad is doing right now, read from the device.

    python3 status.py            # plain lines
    python3 status.py --json     # one JSON object
    python3 status.py --quiet    # device state only, skip the cron check

Reads live state rather than anything cached, and reports the water level and
whether tonight's jobs are actually enabled — the two ways the bed silently does
nothing. Set temperatures go through describe_temp(), so a sentinel reads as
"max cool" / "max heat" and never as -1 or 999 degrees.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(SCRIPTS_DIR.parent.parent / "shared"))

from chilipad_jobs import will_not_run  # noqa: E402
from current_location import is_away_from_home  # noqa: E402
from night_plan import lights_dark_time, phase_temps  # noqa: E402
from sleepme_client import (  # noqa: E402
    SleepMeClient, SleepMeError, describe_temp, load_config, resolve_device,
)
from trial import tonight_base  # noqa: E402


def read_status(nickname: str | None, check_jobs: bool = True) -> dict:
    device_id, nickname = resolve_device(nickname)
    detail = SleepMeClient().get_device(device_id) or {}
    about = detail.get("about") or {}
    control = detail.get("control") or {}
    status = detail.get("status") or {}

    unit = "C" if str(control.get("display_temperature_unit", "f")).lower().startswith("c") else "F"
    suffix = unit.lower()

    out = {
        "device": nickname,
        "model": about.get("model"),
        "firmware": about.get("firmware_version"),
        "connected": status.get("is_connected"),
        "thermal_control_status": control.get("thermal_control_status"),
        "set_temperature": describe_temp(control.get(f"set_temperature_{suffix}"), unit),
        "water_temperature": describe_temp(status.get(f"water_temperature_{suffix}"), unit),
        "water_level": status.get("water_level"),
        "water_low": status.get("is_water_low"),
        "away_from_home": is_away_from_home(),
    }

    try:
        cfg = load_config()
    except SleepMeError:
        cfg = {}
    if cfg:
        # Temperatures only — the TIMES are recomputed nightly from the Hatch, and
        # a Hatch login is far too slow to put behind a status read. `night_schedule
        # --dry-run` is the thing that shows tonight's clock times.
        base, base_why = tonight_base(cfg)
        dark, dark_why = lights_dark_time()
        out["tonight"] = {
            "base": describe_temp(base, "F"),
            "base_from": base_why,
            "phases": {name: describe_temp(t, "F") for name, t in phase_temps(cfg, base).items()},
            "lights_dark": dark.strftime("%H:%M"),
            "lights_dark_from": dark_why,
            "early_off": cfg.get("early_off_time"),
        }
        if check_jobs:
            out["problems"] = will_not_run((cfg.get("jobs") or {}).values())
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Report the ChiliPad's live state.")
    ap.add_argument("--device", help="Nickname (default: the one in chilipad_config.json).")
    ap.add_argument("--json", action="store_true", help="Machine-readable output.")
    ap.add_argument("--quiet", action="store_true",
                    help="Skip the cron-enable check (saves an `openclaw cron list` call).")
    args = ap.parse_args()

    try:
        info = read_status(args.device, check_jobs=not args.quiet)
    except SleepMeError as e:
        if args.json:
            print(json.dumps({"error": str(e)}))
        else:
            print(f"error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(info))
        return

    running = info["thermal_control_status"] == "active"
    print(f"{info['device']} — {'running' if running else 'standby'}, set to {info['set_temperature']}")
    print(f"  water {info['water_temperature']}, level {info['water_level']}"
          + ("  ⚠️ LOW — it will not hold temperature" if info.get("water_low") else ""))
    if info.get("connected") is False:
        print("  ⚠️ the dock is offline — nothing can be set until it is back on Wi-Fi")
    if info.get("away_from_home"):
        print("  (away from home — the scheduled runs will skip themselves)")
    tonight = info.get("tonight")
    if tonight:
        phases = tonight["phases"]
        print(f"  tonight: {phases['entry']} to get into, down to {phases['base']} for sleep, "
              f"pump off at {tonight.get('early_off') or 'wake + 30'}  ({tonight['base_from']})")
        print(f"  lights dark {tonight['lights_dark']} — {tonight['lights_dark_from']}")
    for problem in info.get("problems") or []:
        print(f"  ⚠️ {problem}")


if __name__ == "__main__":
    main()
