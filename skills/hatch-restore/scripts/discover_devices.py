#!/usr/bin/env python3
"""
Log into the Hatch account and list every device on it (name, product code,
mac address), without opening the MQTT connection — this is pure REST, same
shape as govee-lights' discover_devices.py. Saves the raw dump to
config/raw_devices.json; nicknames are then hand-assigned in
config/devices.json.
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hatch_client import _credentials, HatchError

from hatch_rest_api.hatch import Hatch

RAW_DEVICES_PATH = Path(__file__).resolve().parent.parent / "config" / "raw_devices.json"


async def main() -> None:
    email, password = _credentials()
    api = Hatch()
    try:
        token = await api.login(email=email, password=password)
        devices = await api.iot_devices(auth_token=token)
    finally:
        await api.cleanup_client_session()

    RAW_DEVICES_PATH.write_text(json.dumps(devices, indent=2))
    print(f"Found {len(devices)} device(s). Saved raw dump to {RAW_DEVICES_PATH}\n")
    for d in devices:
        print(f"  name={d.get('name')!r} product={d.get('product')} mac={d.get('macAddress')}")
    print("\nAssign a nickname for each device you want to control in config/devices.json, e.g.:")
    print('  {"devices": {"bedroom": {"mac": "<macAddress>", "product": "<product>", "name": "<name>"}}}')


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except HatchError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
