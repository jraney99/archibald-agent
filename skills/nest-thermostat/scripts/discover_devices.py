#!/usr/bin/env python3
"""
One-time setup: list the thermostats on the Device Access project, save the raw
dump, and write config/nest_devices.json as a {nickname: {name, ...}} map.

    python3 discover_devices.py                  # list + write, nickname "house"
    python3 discover_devices.py --nickname up     # a different nickname
    python3 discover_devices.py --dry-run         # list only, write nothing

The SDM "name" is the long enterprises/<project>/devices/<id> string, and every
script resolves through this map rather than typing one — so if the Device Access
project is ever recreated, this file is the only thing that has to catch up.
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
from nest_client import NestClient, NestError, describe_f  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="Discover Nest thermostats on the project.")
    ap.add_argument("--nickname", default="house", help="Nickname for the first thermostat (default: house).")
    ap.add_argument("--dry-run", action="store_true", help="Print what was found and write nothing.")
    args = ap.parse_args()

    try:
        client = NestClient()
        devices = client.list_devices()
    except NestError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)

    thermostats = [d for d in devices if str(d.get("type", "")).endswith("THERMOSTAT")]
    if not thermostats:
        print(f"No thermostat on the project ({len(devices)} device(s) total). Re-run the "
              f"consent URL from setup_oauth.py and tick the thermostat.", file=sys.stderr)
        sys.exit(1)

    for dev in thermostats:
        state = NestClient.read(dev)
        label = ((dev.get("parentRelations") or [{}])[0].get("displayName")) or dev.get("name", "?")
        print(f"{label}")
        print(f"  {dev.get('name')}")
        print(f"  mode {state['mode']}  hvac {state['hvac']}  eco {state['eco']}  online={state['online']}")
        print(f"  now {state['ambient_f']}°F, humidity {state['humidity']}%  "
              f"heat {state['heat_f']} cool {state['cool_f']}  (device shows {state['scale']})")

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return

    first = thermostats[0]
    label = ((first.get("parentRelations") or [{}])[0].get("displayName")) or args.nickname
    mapping = {"devices": {args.nickname: {"name": first.get("name"), "label": label}}}
    if len(thermostats) > 1:
        print(f"\n{len(thermostats)} thermostats found. Only {label!r} was nicknamed "
              f"{args.nickname!r}; add the others to the file by hand.")

    devices_file = data_path("nest_devices.json")
    raw_file = data_path("nest_raw_devices.json")
    devices_file.parent.mkdir(parents=True, exist_ok=True)
    devices_file.write_text(json.dumps(mapping, indent=2) + "\n")
    raw_file.write_text(json.dumps({"devices": devices}, indent=2) + "\n")
    print(f"\nwrote {devices_file}")
    print(f"wrote {raw_file}")


if __name__ == "__main__":
    main()
