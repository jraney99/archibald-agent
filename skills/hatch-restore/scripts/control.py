#!/usr/bin/env python3
"""
Control a Hatch Restore device: light color/brightness, sound, volume,
clock display. Every invocation opens a fresh MQTT connection (login -> AWS
IoT shadow), applies flags in sequence, waits for the update to settle, then
disconnects — a one-shot model, no persistent daemon, same shape as
govee-lights' control.py.

Note on --sound: the original Restore (product "restoreIot") has no open
sound-content API — it only plays whatever's saved as a Favorite/routine in
the Hatch app, so --sound matches against those by name. Newer models
(Restore 2/3, product restoreV4/restoreV5) do expose a direct sound-content
lookup and use that instead automatically (see hatch_client.find_favorite's
docstring).

Usage:
  control.py --device restore --color "255,80,0" --brightness 40
  control.py --device restore --off                    # light + sound off
  control.py --device restore --sound "Ocean"
  control.py --device restore --sound none              # stop sound only
  control.py --device restore --volume 60
  control.py --device restore --clock on
  control.py --device restore --clock off
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hatch_client import HatchSession, HatchError, find_favorite, COMMAND_SETTLE_WAIT


def parse_color(value: str) -> tuple[int, int, int]:
    if value.startswith("#"):
        value = value.lstrip("#")
        return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))
    parts = [int(p.strip()) for p in value.split(",")]
    if len(parts) != 3:
        raise HatchError("--color must be 'R,G,B' or '#RRGGBB'")
    return parts[0], parts[1], parts[2]


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", required=True)
    parser.add_argument("--off", action="store_true", help="turn light and sound both off")
    parser.add_argument("--color", help="'R,G,B' or '#RRGGBB' — turns the light on")
    parser.add_argument("--brightness", type=int, default=0, help="0-100, used with --color")
    parser.add_argument("--white", type=int, default=0, help="0-100 white channel, used with --color")
    parser.add_argument("--sound", help="favorite/routine name (or sound name on newer models), or 'none' to stop")
    parser.add_argument("--duration", type=int, default=0, help="seconds, 0 = indefinite; only used on models with set_sound")
    parser.add_argument("--volume", type=int, help="0-100")
    parser.add_argument("--clock", choices=["on", "off"])
    parser.add_argument("--clock-brightness", type=int, default=50, help="0-100, only used by single-channel clocks (e.g. original Restore)")
    args = parser.parse_args()

    async with HatchSession() as session:
        device = session.device_by_nickname(args.device)

        if args.off:
            device.turn_off()
            device.turn_light_off()

        if args.color:
            r, g, b = parse_color(args.color)
            device.set_color(r, g, b, white=args.white, brightness=args.brightness)

        if args.sound:
            if args.sound.lower() == "none":
                device.turn_off()
            elif hasattr(device, "set_sound"):
                device.set_sound(args.sound, duration=args.duration)
            else:
                device.set_favorite(find_favorite(device, args.sound))

        if args.volume is not None:
            device.set_volume(args.volume)

        if args.clock == "on":
            if hasattr(device, "clock_daytime"):
                device.set_clock()
            else:
                device.set_clock(brightness=args.clock_brightness)
        elif args.clock == "off":
            device.turn_clock_off()

        await asyncio.sleep(COMMAND_SETTLE_WAIT)
        print(f"OK: {args.device} updated.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except HatchError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
