"""
One-time (or re-run-when-lights-change) discovery script.

Fetches every Govee device on the account and writes the raw list to
config/raw_devices.json. Run this once GOVEE_API_KEY is set, then hand-assign
nicknames in config/devices.json (nickname -> sku/device) so control.py and
status.py can be called with a friendly name instead of raw IDs.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from govee_client import GoveeClient, GoveeError  # noqa: E402

CONFIG_DIR = Path(__file__).parent.parent / "config"
RAW_PATH = CONFIG_DIR / "raw_devices.json"


def main() -> None:
    try:
        client = GoveeClient()
        devices = client.list_devices()
    except GoveeError as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    RAW_PATH.write_text(json.dumps(devices, indent=2))

    print(f"Found {len(devices)} device(s). Saved to {RAW_PATH}\n")
    for d in devices:
        caps = sorted({c.get("type", "").rsplit(".", 1)[-1] for c in d.get("capabilities", [])})
        print(f"- {d.get('deviceName')!r}")
        print(f"    sku:    {d.get('sku')}")
        print(f"    device: {d.get('device')}")
        print(f"    caps:   {', '.join(caps)}")
    print(
        "\nNext step: add nicknames for these in "
        f"{CONFIG_DIR / 'devices.json'} (nickname -> sku/device/room)."
    )


if __name__ == "__main__":
    main()
