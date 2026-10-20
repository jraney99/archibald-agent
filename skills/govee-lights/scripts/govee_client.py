"""
Govee Open API client (https://openapi.api.govee.com).

Auth: single API key in the `Govee-API-Key` header (from Govee Home app →
Profile → Settings → Apply for API Key). Read from the GOVEE_API_KEY env var
first, falling back to openclaw.json's env.vars block — same lookup pattern
morning_briefing.py uses for ALPHA_VANTAGE_API_KEY.

Rate limits (per Govee docs, account-wide unless noted):
  - GET  /user/devices     30 req/min
  - POST /device/control   12 req/sec (2 req/sec per device)
  - POST /device/state     30 req/min per device
  - POST /device/scenes    not separately documented; treated as light use
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Optional

import requests

API_BASE = "https://openapi.api.govee.com/router/api/v1"
OPENCLAW_JSON = Path("~/.openclaw/openclaw.json")

# Govee's cloud/device link drops transient blips ("Device is offline")
# even when the physical light is on and reachable seconds later. Without a
# retry here, one blip kills whatever script is running (sunrise ramp,
# fade-off, etc.) and strands the light mid-sequence — seen 2026-08-17 when
# the bedroom sunrise ramp died at step 2 and left the light dim amber for
# 50+ minutes. Retry before raising.
RETRY_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 3


class GoveeError(Exception):
    pass


def _get_key() -> str:
    val = os.environ.get("GOVEE_API_KEY", "")
    if val:
        return val
    try:
        cfg = json.loads(OPENCLAW_JSON.read_text())
        return cfg.get("env", {}).get("vars", {}).get("GOVEE_API_KEY", "")
    except Exception:
        return ""


def rgb_to_int(r: int, g: int, b: int) -> int:
    """Govee's colorRgb value is a 24-bit int, not a hex string."""
    return (r << 16) + (g << 8) + b


def hex_to_int(hex_str: str) -> int:
    return int(hex_str.lstrip("#"), 16)


class GoveeClient:
    def __init__(self, api_key: Optional[str] = None) -> None:
        key = api_key or _get_key()
        if not key:
            raise GoveeError(
                "No Govee API key found. Set GOVEE_API_KEY in openclaw.json's "
                "env.vars block (or the GOVEE_API_KEY env var)."
            )
        self._session = requests.Session()
        self._session.headers.update({
            "Content-Type": "application/json",
            "Govee-API-Key": key,
        })

    def _post(self, path: str, payload: dict) -> dict:
        body = {"requestId": str(uuid.uuid4()), "payload": payload}
        last_error: Optional[str] = None
        for attempt in range(RETRY_ATTEMPTS):
            try:
                resp = self._session.post(f"{API_BASE}{path}", json=body, timeout=15)
                if not resp.ok:
                    last_error = f"POST {path} failed ({resp.status_code}): {resp.text[:300]}"
                else:
                    data = resp.json()
                    if data.get("code") != 200:
                        last_error = f"POST {path} error: {json.dumps(data)[:300]}"
                    else:
                        return data
            except requests.RequestException as e:
                last_error = f"POST {path} request failed: {e}"
            if attempt < RETRY_ATTEMPTS - 1:
                time.sleep(RETRY_BACKOFF_SECONDS)
        raise GoveeError(last_error or f"POST {path} failed after {RETRY_ATTEMPTS} attempts")

    def list_devices(self) -> list[dict]:
        resp = self._session.get(f"{API_BASE}/user/devices", timeout=15)
        if not resp.ok:
            raise GoveeError(f"GET /user/devices failed ({resp.status_code}): {resp.text[:300]}")
        data = resp.json()
        if data.get("code") != 200:
            raise GoveeError(f"GET /user/devices error: {json.dumps(data)[:300]}")
        return data.get("data", [])

    def get_state(self, sku: str, device: str) -> dict:
        data = self._post("/device/state", {"sku": sku, "device": device})
        return data.get("payload", {})

    def list_scenes(self, sku: str, device: str) -> list[dict]:
        data = self._post("/device/scenes", {"sku": sku, "device": device})
        capabilities = data.get("payload", {}).get("capabilities", [])
        for cap in capabilities:
            if cap.get("instance") == "lightScene":
                return cap.get("parameters", {}).get("options", [])
        return []

    def control(self, sku: str, device: str, cap_type: str, instance: str, value: Any) -> dict:
        return self._post("/device/control", {
            "sku": sku,
            "device": device,
            "capability": {
                "type": cap_type,
                "instance": instance,
                "value": value,
            },
        })

    # -- friendly wrappers -------------------------------------------------
    #
    # The four FLAT commands go over the LAN first (govee_lan.py), where the strip can be
    # asked afterwards whether it actually changed — the cloud reports "ok" either way, and
    # on 2026-10-01 it called a strip offline while that strip was answering us locally.
    # Anything the LAN API has no command for (scenes, gradient, DIY) stays on the cloud.
    # GOVEE_NO_LAN=1 forces the cloud path.

    def _lan(self, device: str, **wanted) -> Optional[dict]:
        """An applied-and-confirmed LAN result, or None to fall through to the cloud."""
        if os.environ.get("GOVEE_NO_LAN"):
            return None
        try:
            import govee_lan
        except ImportError:
            return None
        try:
            done = govee_lan.apply(device, **wanted)
        except govee_lan.LanUnavailable:
            return None
        except OSError:  # no route, firewall, socket trouble — the cloud may still work
            return None
        if not done.get("verified"):
            # The strip answered and disagreed: say so instead of a false success. The cloud
            # path can't do better (it would just report "ok"), so don't paper over it.
            raise GoveeError(f"the light did not take the change (it reports {done.get('state')}) "
                             f"— local control reached it at {done.get('ip')}")
        return done

    def power(self, sku: str, device: str, on: bool) -> dict:
        return self._lan(device, power=on) or \
            self.control(sku, device, "devices.capabilities.on_off", "powerSwitch", 1 if on else 0)

    def brightness(self, sku: str, device: str, pct: int) -> dict:
        pct = max(1, min(100, pct))
        return self._lan(device, brightness=pct) or \
            self.control(sku, device, "devices.capabilities.range", "brightness", pct)

    def color_rgb(self, sku: str, device: str, r: int, g: int, b: int) -> dict:
        return self._lan(device, rgb=(r, g, b)) or \
            self.control(sku, device, "devices.capabilities.color_setting", "colorRgb", rgb_to_int(r, g, b))

    def color_temp(self, sku: str, device: str, kelvin: int) -> dict:
        return self._lan(device, kelvin=kelvin) or \
            self.control(sku, device, "devices.capabilities.color_setting", "colorTemperatureK", kelvin)

    def scene(self, sku: str, device: str, scene_value: dict) -> dict:
        """scene_value is the {"paramId": ..., "id": ...} object from list_scenes()."""
        return self.control(sku, device, "devices.capabilities.dynamic_scene", "lightScene", scene_value)

    def gradient_toggle(self, sku: str, device: str, on: bool) -> dict:
        return self.control(sku, device, "devices.capabilities.toggle", "gradientToggle", 1 if on else 0)

    def clear_effect_modes(self, sku: str, device: str) -> None:
        """Force this H61E6 out of gradient (per-segment cycling) mode
        before applying a flat color/brightness. Confirmed live 2026-08-17:
        gradientToggle is a real, controllable capability on this device
        (dreamViewToggle is listed in the device schema but the API
        rejects it with "device does not has DreamView" — not actually
        supported here, don't call it). If gradientToggle was left on from
        a past scene, a plain colorRgb call sets the base color but
        segments can keep cycling through other hues on top of it — a
        plausible explanation for the 2026-08-17 bedroom-sunrise incident
        (script logged/confirmed amber, light visually showed blue)."""
        self.gradient_toggle(sku, device, False)
