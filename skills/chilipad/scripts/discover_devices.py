#!/usr/bin/env python3
"""
One-time setup: list every Dock Pro on the sleep.me account, save the raw dump to
config/chilipad_raw_devices.json, and write config/chilipad_devices.json as a
{nickname: {id, name}} map the other scripts resolve against.

    python3 discover_devices.py                 # list + write, nickname "bed"
    python3 discover_devices.py --nickname pad   # a different nickname
    python3 discover_devices.py --dry-run        # list only, write nothing

Re-run it if the unit is ever re-registered in the app: the device id changes and
every script here resolves through the map rather than a typed id, so this is the
only file that needs to catch up (same reasoning as hatch-restore's stale alarm
id, which cost three failed tries on 2026-09-20).

With more than one unit on the account the first is nicknamed and the rest are
printed — nickname them by hand in the file, like govee-lights/config/devices.json.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(SCRIPTS_DIR.parent.parent / "shared"))

from data_paths import path as data_path  # noqa: E402
from sleepme_client import SleepMeClient, SleepMeError, describe_temp  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="Discover ChiliPad Dock Pro units on the sleep.me account.")
    ap.add_argument("--nickname", default="bed",
                    help="Nickname for the first unit found (default: bed).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print what was found and write nothing.")
    args = ap.parse_args()

    try:
        client = SleepMeClient()
        devices = client.list_devices()
    except SleepMeError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)

    if not devices:
        print("No devices on this sleep.me account. Is the Dock Pro registered in the "
              "Sleepme app under the same account the token came from?", file=sys.stderr)
        sys.exit(1)

    raw = {"devices": devices, "details": {}}
    for dev in devices:
        dev_id = dev.get("id")
        label = dev.get("name") or dev_id
        try:
            detail = client.get_device(dev_id)
        except SleepMeError as e:
            print(f"{label} ({dev_id}): could not read details — {e}")
            continue
        raw["details"][dev_id] = detail
        about = detail.get("about") or {}
        control = detail.get("control") or {}
        status = detail.get("status") or {}
        unit = "C" if str(control.get("display_temperature_unit", "f")).lower().startswith("c") else "F"
        print(f"{label}  id={dev_id}")
        print(f"  model {about.get('model', '?')}  firmware {about.get('firmware_version', '?')}")
        print(f"  {control.get('thermal_control_status', '?')}, "
              f"set to {describe_temp(control.get(f'set_temperature_{unit.lower()}'), unit)}, "
              f"water {describe_temp(status.get(f'water_temperature_{unit.lower()}'), unit)}")
        print(f"  connected={status.get('is_connected')}  water_level={status.get('water_level')}  "
              f"water_low={status.get('is_water_low')}")

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return

    first = devices[0]
    mapping = {"devices": {args.nickname: {"id": first.get("id"), "name": first.get("name")}}}
    if len(devices) > 1:
        print(f"\n{len(devices)} units found. Only {first.get('name')!r} was nicknamed "
              f"{args.nickname!r}; add the others to the file by hand.")

    devices_file = data_path("chilipad_devices.json")
    raw_file = data_path("chilipad_raw_devices.json")
    devices_file.parent.mkdir(parents=True, exist_ok=True)
    devices_file.write_text(json.dumps(mapping, indent=2) + "\n")
    raw_file.write_text(json.dumps(raw, indent=2) + "\n")
    print(f"\nwrote {devices_file}")
    print(f"wrote {raw_file}")


if __name__ == "__main__":
    main()
