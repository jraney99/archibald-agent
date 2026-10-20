#!/usr/bin/env python3
"""Find Zengge Bluetooth lights near the Mac — run once when a light is added.

  python3 discover_devices.py            # 8 s scan
  python3 discover_devices.py --seconds 15

Prints each Zengge advertiser with the name to put in config/zengge_devices.json. The NAME
is the stable identifier (it ends in the light's MAC); macOS gives Bluetooth devices a
per-Mac UUID address that can change, so it is not stored.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from zengge_ble import DEVICES, BleUnavailable, scan  # noqa: E402

PRODUCTS = {0x27: "Ctrl_Mini_RGBW (RGB + one white channel)", 0x33: "Mini RGB", 0x06: "Mini RGBW",
            0x07: "Mini RGBCW", 0x08: "Mini RGB + mic", 0x44: "RGBW bulb", 0x52: "CCT bulb"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=8.0)
    args = ap.parse_args()
    try:
        found = scan(args.seconds)
    except Exception as exc:  # noqa: BLE001 — bleak raises its own types for a dead radio
        print(json.dumps({"error": f"Bluetooth scan failed: {exc}"}))
        sys.exit(1)
    print(f"Found {len(found)} Zengge light(s) in {args.seconds:.0f}s\n")
    for d in found:
        print(f"- {d['name']}")
        print(f"    product: 0x{d['product_id']:02X} {PRODUCTS.get(d['product_id'], '(not in the known list)')}")
        print(f"    ble v{d['ble_version']}  firmware {d['firmware']}  mac {d['mac']}  rssi {d['rssi']}"
              + (f"  power {'on' if d['on'] else 'off'}" if "on" in d else ""))
    print(f"\nNext: add a nickname for it in {DEVICES} as "
          '{"nickname": {"name": "<name above>", "product_id": <n>, "room": "<room>"}}')


if __name__ == "__main__":
    main()
