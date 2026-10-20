#!/usr/bin/env python3
"""
What the thermostat is doing right now, read from the device.

    python3 status.py            # plain lines
    python3 status.py --json

Everything is reported in Fahrenheit — the SDM API only ever speaks Celsius, and a
raw Celsius number in front of the user is a bug, not a detail.

One read, which is 1 of Google's 5-per-minute per-device budget. Don't loop this.
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
from nest_client import NestClient, NestError, load_config, resolve_device  # noqa: E402


def read_status(nickname: str | None = None) -> dict:
    name, nickname = resolve_device(nickname)
    client = NestClient()
    out = {"device": nickname}
    out.update(client.read(client.get_device(name)))
    out["away_from_home"] = is_away_from_home()
    try:
        cfg = load_config()
        out["presets"] = {"sleep_f": cfg.get("sleep_temp_f"), "day_f": cfg.get("day_temp_f")}
    except NestError:
        pass
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Report the Nest's live state.")
    ap.add_argument("--device", help="Nickname (default: the one in nest_config.json).")
    ap.add_argument("--json", action="store_true", help="Machine-readable output.")
    args = ap.parse_args()

    try:
        info = read_status(args.device)
    except NestError as e:
        if args.json:
            print(json.dumps({"error": str(e)}))
        else:
            print(f"error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(info))
        return

    target = info.get("cool_f") if info.get("mode") == "COOL" else info.get("heat_f")
    if info.get("mode") == "HEATCOOL":
        target = f"{info.get('heat_f')}–{info.get('cool_f')}"
    print(f"{info['device']} — {info.get('ambient_f')}°F now, {info.get('mode')} "
          f"to {target}°F, hvac {info.get('hvac')}")
    print(f"  humidity {info.get('humidity')}%")
    if info.get("eco") == "MANUAL_ECO":
        print(f"  eco is ON ({info.get('eco_heat_f')}–{info.get('eco_cool_f')}°F) "
              f"— setpoint changes are refused while it is")
    if not info.get("online"):
        print("  ⚠️ the thermostat is offline")
    if info.get("away_from_home"):
        print("  (away from home)")
    presets = info.get("presets")
    if presets:
        print(f"  presets: sleep {presets['sleep_f']}°F, day {presets['day_f']}°F")


if __name__ == "__main__":
    main()
