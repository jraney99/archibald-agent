#!/usr/bin/env python3
"""What a Zengge Bluetooth light is actually showing, by nickname.

  python3 status.py --light living_room
  python3 status.py --light all

Asks the light itself (there is no cloud to ask), so every row is `source: device`. The rgb
is the colour the light STORES — already scaled by its brightness, so a dim amber reads as a
small number in each channel, not as the bright colour that was asked for.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from zengge_ble import BleUnavailable, load_devices, status  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--light", required=True, help="Nickname from config/zengge_devices.json, or 'all'")
    args = ap.parse_args()

    devices = load_devices()
    targets = list(devices.items()) if args.light == "all" else (
        [(args.light, devices[args.light])] if args.light in devices else [])
    if not targets:
        print(json.dumps({"error": f"Unknown light {args.light!r}. Known: {', '.join(devices) or '(none configured)'}"}))
        sys.exit(1)

    results = []
    for nickname, dev in targets:
        try:
            st = status(dev["name"])
            r, g, b = st["rgb"]
            results.append({"light": nickname, "source": "device", "power": "on" if st["on"] else "off",
                            "mode": st["mode"], "brightness": st["brightness"],
                            "color_rgb": (r << 16) | (g << 8) | b, "rgb": [r, g, b], "white": st["white"]})
        except BleUnavailable as e:
            results.append({"light": nickname, "error": str(e)})
    print(json.dumps(results, indent=2))
    if any("error" in r for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
