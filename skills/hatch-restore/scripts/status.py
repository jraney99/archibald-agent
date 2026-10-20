#!/usr/bin/env python3
"""
Read live state for one or all configured Hatch devices via their AWS IoT
shadow. Connects once, waits for the initial shadow push, prints state for
each requested device, disconnects.
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hatch_client import HatchSession, HatchError, load_device_map


def print_status(nickname: str, device) -> None:
    print(f"{nickname} ({device.device_name}):")
    print(f"  online:        {device.is_online}")
    print(
        f"  light:         {'on' if device.is_light_on else 'off'}  "
        f"(r={device.red} g={device.green} b={device.blue} w={device.white} brightness={device.brightness}%)"
    )
    favorite_names = [f.get("name") for f in (getattr(device, "favorites", []) or [])]
    print(
        f"  sound:         {'playing' if device.is_playing else 'off'}  "
        f"(sound_id={device.sound_id} volume={device.volume}%)"
    )
    if favorite_names:
        print(f"  favorites:     {', '.join(favorite_names)}")
    print(f"  clock:         {'on' if device.is_clock_on else 'off'}  (24h={bool(device.is_clock_24h)})")
    if getattr(device, "alarm_capable", False):
        enabled_count = sum(1 for a in device.alarms if a.get("enabled"))
        print(f"  alarms:        {len(device.alarms)} configured, {enabled_count} enabled — see alarm.py --device {nickname} --list")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", default="all")
    args = parser.parse_args()

    device_map = load_device_map()
    nicknames = list(device_map) if args.device == "all" else [args.device]

    async with HatchSession() as session:
        for nickname in nicknames:
            device = session.device_by_nickname(nickname)
            print_status(nickname, device)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except HatchError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
