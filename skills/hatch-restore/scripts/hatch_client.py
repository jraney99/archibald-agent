"""
Hatch Restore cloud client, built on the `hatch_rest_api` library
(https://github.com/dahlb/hatch_rest_api). Unlike Govee, Hatch has no public
API key — auth is the actual Hatch account email/password, federated through
AWS Cognito into a live MQTT connection (AWS IoT device shadow). There is no
simple request/response control call: every invocation here logs in, opens
the MQTT connection, applies commands by publishing shadow deltas, waits
briefly for them to settle, then tears the connection down. No persistent
daemon.

Credentials: HATCH_EMAIL / HATCH_PASSWORD env vars, falling back to
openclaw.json's env.vars block — same lookup pattern as govee_client.py's
GOVEE_API_KEY.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from hatch_rest_api import get_rest_devices

OPENCLAW_JSON = Path(os.path.expanduser("~/.openclaw/openclaw.json")).expanduser()
DEVICES_CONFIG = Path(__file__).resolve().parent.parent / "config" / "devices.json"

# Seconds to wait after connecting for the device to publish its initial
# shadow state, so status reads (and commands that branch on current state,
# e.g. set_color's "is anything already playing") aren't racing a device
# that hasn't reported in yet.
INITIAL_STATE_WAIT = 3
# Seconds to wait after sending shadow-delta commands before disconnecting,
# so the update has time to reach the device and reflect back.
COMMAND_SETTLE_WAIT = 2


class HatchError(Exception):
    pass


def _get_credential(name: str) -> str:
    val = os.environ.get(name, "")
    if val:
        return val
    try:
        cfg = json.loads(OPENCLAW_JSON.read_text())
        return cfg.get("env", {}).get("vars", {}).get(name, "")
    except Exception:
        return ""


def _credentials() -> tuple[str, str]:
    email = _get_credential("HATCH_EMAIL")
    password = _get_credential("HATCH_PASSWORD")
    if not email or not password:
        raise HatchError(
            "No Hatch credentials found. Set HATCH_EMAIL / HATCH_PASSWORD in "
            "openclaw.json's env.vars block (or as env vars)."
        )
    return email, password


def load_device_map() -> dict:
    if not DEVICES_CONFIG.exists():
        return {}
    return json.loads(DEVICES_CONFIG.read_text()).get("devices", {})


class HatchSession:
    """Async context manager: login -> MQTT connect -> [use .devices] -> disconnect."""

    def __init__(self):
        self.api = None
        self.mqtt_connection = None
        self.devices: list = []

    async def __aenter__(self) -> "HatchSession":
        email, password = _credentials()
        try:
            (
                self.api,
                self.mqtt_connection,
                self.devices,
                _expiration,
            ) = await get_rest_devices(email=email, password=password)
        except Exception as e:
            status = getattr(e, "status", None)
            if status == 429:
                raise HatchError(
                    "Hatch API rate limit hit (429) — wait a minute before retrying."
                ) from e
            raise HatchError(f"Failed to connect to Hatch cloud: {e}") from e
        await asyncio.sleep(INITIAL_STATE_WAIT)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        loop = asyncio.get_running_loop()
        if self.mqtt_connection is not None:
            disconnect_future = await loop.run_in_executor(
                None, self.mqtt_connection.disconnect
            )
            await loop.run_in_executor(None, disconnect_future.result)
        if self.api is not None:
            await self.api.cleanup_client_session()

    def device_by_nickname(self, nickname: str):
        device_map = load_device_map()
        entry = device_map.get(nickname)
        if entry is None:
            raise HatchError(
                f"Unknown device nickname '{nickname}'. Known: {list(device_map)}. "
                f"Run discover_devices.py and add it to config/devices.json."
            )
        mac = entry["mac"]
        for device in self.devices:
            if device.mac == mac:
                return device
        raise HatchError(
            f"Device '{nickname}' (mac {mac}) not found on this Hatch account — "
            f"re-run discover_devices.py, the mac may have changed."
        )


def find_favorite(device, name_or_id: str) -> str:
    """
    Resolve a sound/routine name or id to the 'name-id' string set_favorite()
    expects. Only used for devices without a direct set_sound() (e.g. the
    original Restore/restoreIot) — there, "sounds" are whatever's saved as a
    Favorite/routine in the Hatch app, not an open content library.
    """
    favorites = getattr(device, "favorites", []) or []
    target = name_or_id.strip().lower()
    for fav in favorites:
        if str(fav.get("id")) == name_or_id or str(fav.get("name", "")).strip().lower() == target:
            return f"{fav['name']}-{fav['id']}"
    available = ", ".join(str(f.get("name")) for f in favorites) or "(none — check the Hatch app's Favorites screen)"
    raise HatchError(f"No favorite/routine named '{name_or_id}' found. Available: {available}")
