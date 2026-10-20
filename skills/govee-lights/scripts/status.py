"""
CLI to read current state of one or more Govee lights by nickname.

Examples:
  python3 status.py --light office
  python3 status.py --light all
  python3 status.py --light all --cloud    # what Govee's servers believe, for comparison

Asks the strip itself over the LAN (govee_lan.py) and falls back to the cloud, because
/device/state echoes the last command we sent rather than the device: on 2026-10-01 it
reported amber at 12% for a strip it was simultaneously calling offline. `source` in each
row says which answered — "device" is the truth, "cloud" is a claim.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from govee_client import GoveeClient, GoveeError  # noqa: E402

CONFIG_PATH = Path(__file__).parent.parent / "config" / "devices.json"


def load_devices() -> dict:
    cfg = json.loads(CONFIG_PATH.read_text())
    return cfg.get("devices", {})


def summarize(payload: dict) -> dict:
    out = {}
    for cap in payload.get("capabilities", []):
        instance = cap.get("instance")
        value = cap.get("state", {}).get("value")
        if instance == "powerSwitch":
            out["power"] = "on" if value == 1 else "off"
        elif instance == "brightness":
            out["brightness"] = value
        elif instance == "colorRgb":
            out["color_rgb"] = value
        elif instance == "colorTemperatureK":
            out["color_temp_k"] = value
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--light", required=True, help="Nickname from config/devices.json, or 'all'")
    ap.add_argument("--cloud", action="store_true", help="Skip local control; report what Govee's servers say")
    args = ap.parse_args()

    devices = load_devices()
    targets = list(devices.items()) if args.light == "all" else (
        [(args.light, devices[args.light])] if args.light in devices else []
    )

    if not targets:
        print(json.dumps({"error": f"Unknown light {args.light!r}. Known: {', '.join(devices) or '(none configured)'}"}))
        sys.exit(1)

    client = GoveeClient()
    results = []
    for nickname, dev in targets:
        if not args.cloud:
            try:
                import govee_lan
                st = govee_lan.status(dev["device"])
                r, g, b = st["rgb"]
                results.append({"light": nickname, "source": "device", "power": "on" if st["on"] else "off",
                                "brightness": st["brightness"],
                                "color_rgb": (r << 16 | g << 8 | b) if None not in st["rgb"] else None,
                                "color_temp_k": st["kelvin"]})
                continue
            except Exception:  # noqa: BLE001 — no local control (or none configured): ask the cloud
                pass
        try:
            payload = client.get_state(dev["sku"], dev["device"])
            results.append({"light": nickname, "source": "cloud", **summarize(payload)})
        except GoveeError as e:
            results.append({"light": nickname, "error": str(e)})

    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
