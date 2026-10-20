#!/usr/bin/env python3
"""Control a Zengge Bluetooth light by nickname — same shape as govee-lights/control.py.

  python3 control.py --light living_room --on
  python3 control.py --light living_room --off
  python3 control.py --light living_room --brightness 40
  python3 control.py --light living_room --color "255,170,90" --brightness 30
  python3 control.py --light living_room --white --brightness 60
  python3 control.py --light all --off

Every flat command is sent over Bluetooth and then CONFIRMED against the light itself
(zengge_ble.apply). A light that disagrees comes back as "status": "unverified", never "ok".
There is no --temp: this light has a single white channel, so --white at a brightness is the
only white it can do. Scenes/effects are not wired up (nothing asked for them yet).

Schedule-only flags (--skip-if-away, --skip-if-held, --expected-start, --late-grace-min) behave
exactly as in govee-lights; the 8pm winddown job uses them, manual/chat calls omit them.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "shared"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "govee-lights" / "scripts"))
from zengge_ble import BleUnavailable, apply, load_devices, render  # noqa: E402
from current_location import is_away_from_home  # noqa: E402
from misfire_guard import near_slot  # noqa: E402
from evening_hold import held  # noqa: E402  (the Govee holds cover this light too)

MISFIRE_EARLY_GRACE_MIN = 20
DEFAULT_LATE_GRACE_MIN = 60


def resolve_targets(name: str, devices: dict) -> list[tuple[str, dict]]:
    if name == "all":
        return list(devices.items())
    if name not in devices:
        raise KeyError(f"Unknown light nickname {name!r}. Known: {', '.join(devices) or '(none configured yet)'}")
    return [(name, devices[name])]


def parse_color(spec: str) -> tuple[int, int, int]:
    spec = spec.strip()
    if spec.startswith("#") or (len(spec) == 6 and all(c in "0123456789abcdefABCDEF" for c in spec)):
        val = int(spec.lstrip("#"), 16)
        return (val >> 16) & 0xFF, (val >> 8) & 0xFF, val & 0xFF
    parts = [int(p.strip()) for p in spec.split(",")]
    if len(parts) != 3 or not all(0 <= p <= 255 for p in parts):
        raise ValueError(f"Color must be 'R,G,B' (0-255) or hex like #FFB43C, got {spec!r}")
    return parts[0], parts[1], parts[2]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--light", required=True, help="Nickname from config/zengge_devices.json, or 'all'")
    ap.add_argument("--on", action="store_true")
    ap.add_argument("--off", action="store_true")
    ap.add_argument("--brightness", type=int, help="1-100")
    ap.add_argument("--color", help="'R,G,B' or hex like #FFB43C")
    ap.add_argument("--white", action="store_true", help="Plain white (the light's single white channel)")
    ap.add_argument("--skip-if-away", action="store_true",
                    help="No-op (exit 0) when is_away_from_home() is true. For scheduled on-jobs only.")
    ap.add_argument("--skip-if-held", action="store_true",
                    help="No-op (exit 0) while a night-light / movie hold is live (govee-lights/evening_hold.py). "
                         "For the scheduled evening steps only.")
    ap.add_argument("--expected-start", metavar="HH:MM",
                    help="The cron job's own scheduled time; skips gateway catch-up runs (shared/misfire_guard.py).")
    ap.add_argument("--late-grace-min", type=int, default=DEFAULT_LATE_GRACE_MIN)
    args = ap.parse_args()

    if args.color and args.white:
        print(json.dumps({"error": "--color and --white are exclusive"}))
        sys.exit(2)
    if not (args.on or args.off or args.brightness is not None or args.color or args.white):
        print(json.dumps({"error": "nothing to do: give --on/--off/--brightness/--color/--white"}))
        sys.exit(2)
    if args.brightness is not None and not 1 <= args.brightness <= 100:
        print(json.dumps({"error": "--brightness must be 1-100"}))
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
    if args.skip_if_held and held(args.light):
        print(json.dumps({"skipped": "night light hold is on"}))
        return

    try:
        targets = resolve_targets(args.light, load_devices())
        rgb = parse_color(args.color) if args.color else None
    except (KeyError, ValueError) as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)

    power = True if args.on else (False if args.off else None)
    brightness = args.brightness
    if rgb is not None:
        rgb, brightness = render(rgb, brightness)   # the Govee colour, as these lamps show it
    results = []
    for nickname, dev in targets:
        try:
            done = apply(dev["name"], power=power, brightness=brightness, rgb=rgb,
                         white=True if args.white else None)
            results.append({"light": nickname, "status": "ok" if done["verified"] else "unverified",
                            "source": "device", "state": done["state"], "attempts": done["attempts"]})
        except BleUnavailable as e:
            results.append({"light": nickname, "status": "error", "error": str(e)})

    print(json.dumps(results, indent=2))
    if any(r["status"] != "ok" for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
