#!/usr/bin/env python3
"""
Google Smart Device Management (SDM) client — the user's Nest thermostat.

Auth is the awkward part and the reason this looks nothing like the ChiliPad's
one-line bearer token. Google requires a Device Access project (one-time US$5),
a Google Cloud project with the Smart Device Management API enabled, a Web
Application OAuth client, and then a refresh token obtained through Nest's
Partner Connection Manager rather than the ordinary Google consent screen.
`setup_oauth.py` walks that last step; everything after it is plain REST.

ITS OWN OAUTH CLIENT, DELIBERATELY. This must never share the client that `gog`
and the email daemon use. On 2026-10-02 those two shared one client and revoking
it killed both at once (feedback-google-oauth-rotation-lessons). A thermostat
integration is not worth putting calendar and mail at risk, so it carries its own
NEST_CLIENT_ID / NEST_CLIENT_SECRET and can be revoked on its own.

Four secrets, in openclaw.json's env.vars (same lookup as GOVEE_API_KEY):
    NEST_PROJECT_ID       the Device Access project UUID
    NEST_CLIENT_ID        the Web Application OAuth client
    NEST_CLIENT_SECRET
    NEST_REFRESH_TOKEN    from setup_oauth.py

THE DEVICE LIMIT IS THE TIGHT ONE. Google publishes several ceilings, and the one
that bites is per-device, enforced across all projects to protect the thermostat:
**5 queries per minute AND 100 per hour**. The hourly cap is what rules out any
kind of polling — at one read a minute it would be exhausted in under two hours.
This client holds both windows and refuses rather than burning the budget, and
nothing in this skill polls.

CELSIUS ONLY. Every SDM temperature is Celsius regardless of what the thermostat
displays. The user thinks in Fahrenheit, so conversion happens at the edges here and
nowhere else.

THREE RULES THE API ENFORCES, all of which produce confusing 400s if ignored:
  1. A setpoint command must match the current mode — SetHeat needs HEAT, SetCool
     needs COOL, SetRange needs HEATCOOL. `set_temperature_f()` reads the mode and
     picks the right command rather than guessing.
  2. **Eco mode rejects setpoint commands outright.** Eco has to be turned off
     first, which `set_temperature_f()` does and reports.
  3. In HEATCOOL, coolCelsius must be strictly above heatCelsius.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

import requests

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR.parent / "shared"))
from data_paths import path as data_path  # noqa: E402

API_BASE = "https://smartdevicemanagement.googleapis.com/v1"
TOKEN_URL = "https://oauth2.googleapis.com/token"
PCM_BASE = "https://nestservices.google.com/partnerconnections"
SCOPE = "https://www.googleapis.com/auth/sdm.service"
REDIRECT_URI = "https://www.google.com"
# Matches RUNTIME in shared/test_data_layout.py: the deployment's own credential
# file is the one path a script may name.
OPENCLAW_JSON = Path(os.path.expanduser("~/.openclaw/openclaw.json")).expanduser()

SECRETS = ("NEST_PROJECT_ID", "NEST_CLIENT_ID", "NEST_CLIENT_SECRET", "NEST_REFRESH_TOKEN")

# Google's per-device ceiling is 5 QPM / 100 QPH across all projects. Sit under
# both: the minute cap stops a burst, the hour cap is the one that makes polling
# impossible and must never be spent by accident.
MAX_PER_MINUTE = 4
MAX_PER_HOUR = 90

RETRY_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 15

# A HARD SAFETY BOUND on what can be asked of the HVAC, in code and not
# configurable.
#
# WHY (2026-10-07): the deployment can now move physical things, and the agent reads
# untrusted inbound email. outbound-guard stops data leaving; nothing stopped the
# agent being TALKED INTO setting the house to 90°F in August or 50°F in a January
# freeze. A thermostat is the one device here that can damage the building.
#
# 60°F floor: below that risks pipes in a hard freeze, which is the expensive
# failure. 80°F ceiling: above that is a health risk in a hot summer and serves no
# purpose. The user's own 67/70 sit comfortably inside.
#
# This does NOT touch eco. Eco is Nest's own away mode with its own safe bounds, set
# by Nest rather than by anything here, so away.py is unaffected.
SAFE_MIN_F, SAFE_MAX_F = 60, 80

MODES = ("HEAT", "COOL", "HEATCOOL", "OFF")
SETPOINT_COMMAND = {
    "HEAT": ("sdm.devices.commands.ThermostatTemperatureSetpoint.SetHeat", "heatCelsius"),
    "COOL": ("sdm.devices.commands.ThermostatTemperatureSetpoint.SetCool", "coolCelsius"),
}


class NestError(Exception):
    pass


def f_to_c(f: float) -> float:
    """Fahrenheit in, Celsius out, rounded to the half degree the device accepts."""
    return round((float(f) - 32) * 5 / 9 * 2) / 2


def c_to_f(c: Any) -> Optional[float]:
    if c is None:
        return None
    try:
        return round(float(c) * 9 / 5 + 32, 1)
    except (TypeError, ValueError):
        return None


def describe_f(c: Any) -> str:
    """A Celsius reading as the user reads it. Never print a raw Celsius number at him."""
    f = c_to_f(c)
    return "unknown" if f is None else f"{f:.0f}°F"


def _secret(name: str) -> str:
    val = os.environ.get(name, "")
    if val:
        return val
    try:
        cfg = json.loads(OPENCLAW_JSON.read_text())
        return cfg.get("env", {}).get("vars", {}).get(name, "")
    except Exception:  # noqa: BLE001 — a missing/garbled config reads as "not set"
        return ""


def missing_secrets() -> list:
    return [name for name in SECRETS if not _secret(name)]


def load_devices() -> dict:
    try:
        return json.loads(data_path("nest_devices.json").read_text()).get("devices", {})
    except FileNotFoundError:
        return {}
    except ValueError as e:
        raise NestError(f"nest_devices.json is not valid JSON: {e}") from None


def load_config() -> dict:
    try:
        return json.loads(data_path("nest_config.json").read_text())
    except FileNotFoundError:
        raise NestError("nest_config.json is missing — see nest-thermostat/SKILL.md") from None


def resolve_device(nickname: Optional[str] = None) -> tuple[str, str]:
    """(full SDM device name, nickname). The device name is the long
    enterprises/<project>/devices/<id> string the API wants."""
    devices = load_devices()
    if not devices:
        raise NestError("No thermostat configured yet — run `python3 scripts/discover_devices.py`.")
    if nickname is None:
        try:
            nickname = load_config().get("device") or next(iter(devices))
        except NestError:
            nickname = next(iter(devices))
    entry = devices.get(nickname)
    if entry is None:
        raise NestError(f"No thermostat nicknamed {nickname!r}. Configured: "
                        f"{', '.join(sorted(devices)) or 'none'}.")
    return entry["name"], nickname


def auth_url(project_id: str, client_id: str) -> str:
    """The Partner Connection Manager consent URL.

    Not the ordinary Google OAuth endpoint: Nest requires the PCM flow, which also
    asks which devices to share. `prompt=consent` and `access_type=offline` are
    both required or no refresh token comes back — the single most common way this
    setup fails.
    """
    return (f"{PCM_BASE}/{project_id}/auth"
            f"?redirect_uri={REDIRECT_URI}"
            f"&access_type=offline&prompt=consent"
            f"&client_id={client_id}"
            f"&response_type=code&scope={SCOPE}")


def exchange_code(code: str, client_id: str, client_secret: str) -> dict:
    """Auth code -> tokens. Used once, by setup_oauth.py."""
    resp = requests.post(TOKEN_URL, data={
        "client_id": client_id, "client_secret": client_secret,
        "code": code, "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI,
    }, timeout=30)
    if not resp.ok:
        raise NestError(f"token exchange failed ({resp.status_code}): {resp.text[:300]}")
    data = resp.json()
    if not data.get("refresh_token"):
        raise NestError(
            "Google returned no refresh_token. The auth URL must carry both "
            "access_type=offline and prompt=consent, and the code can only be used "
            "once — start again from a fresh URL.")
    return data


class NestClient:
    def __init__(self) -> None:
        missing = missing_secrets()
        if missing:
            raise NestError(
                f"Missing {', '.join(missing)} in openclaw.json's env.vars. "
                f"Run `python3 scripts/setup_oauth.py` — see SKILL.md's Setup.")
        self.project_id = _secret("NEST_PROJECT_ID")
        self._client_id = _secret("NEST_CLIENT_ID")
        self._client_secret = _secret("NEST_CLIENT_SECRET")
        self._refresh_token = _secret("NEST_REFRESH_TOKEN")
        self._access_token: Optional[str] = None
        self._access_expires = 0.0
        self._minute: list = []
        self._hour: list = []

    # -- auth --------------------------------------------------------------

    def _token(self) -> str:
        """A live access token, refreshed when within a minute of expiry."""
        if self._access_token and time.monotonic() < self._access_expires - 60:
            return self._access_token
        resp = requests.post(TOKEN_URL, data={
            "client_id": self._client_id, "client_secret": self._client_secret,
            "refresh_token": self._refresh_token, "grant_type": "refresh_token",
        }, timeout=30)
        if not resp.ok:
            raise NestError(
                f"Could not refresh the Nest token ({resp.status_code}): {resp.text[:200]}. "
                f"If this says invalid_grant the refresh token was revoked — re-run "
                f"setup_oauth.py. It does NOT affect gog or the email daemon; this skill "
                f"has its own OAuth client on purpose.")
        data = resp.json()
        self._access_token = data["access_token"]
        self._access_expires = time.monotonic() + float(data.get("expires_in", 3600))
        return self._access_token

    # -- transport ---------------------------------------------------------

    def _spend_quota(self) -> None:
        """Hold both of Google's per-device windows.

        The hourly cap is refused rather than waited out: sleeping up to an hour
        inside a cron job would hold the job open and fire its timeout, and
        whatever wanted the thermostat has a next scheduled run anyway.
        """
        now = time.monotonic()
        self._minute = [t for t in self._minute if now - t < 60]
        self._hour = [t for t in self._hour if now - t < 3600]
        if len(self._hour) >= MAX_PER_HOUR:
            raise NestError(
                f"Nest hourly quota spent ({MAX_PER_HOUR} of Google's 100/hour per device) "
                f"— refusing rather than getting the device throttled.")
        if len(self._minute) >= MAX_PER_MINUTE:
            wait = 60 - (now - self._minute[0]) + 0.5
            if wait > 0:
                time.sleep(wait)
            now = time.monotonic()
            self._minute = [t for t in self._minute if now - t < 60]
        stamp = time.monotonic()
        self._minute.append(stamp)
        self._hour.append(stamp)

    def _request(self, method: str, endpoint: str, payload: Optional[dict] = None) -> Any:
        last = "no attempt made"
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            self._spend_quota()
            try:
                resp = requests.request(
                    method, f"{API_BASE}/{endpoint.lstrip('/')}",
                    headers={"Authorization": f"Bearer {self._token()}",
                             "Content-Type": "application/json"},
                    json=payload, timeout=30,
                )
            except requests.RequestException as e:
                last = f"{method} {endpoint}: {e}"
                if attempt < RETRY_ATTEMPTS:
                    time.sleep(RETRY_BACKOFF_SECONDS * attempt)
                continue

            if resp.status_code == 401:
                raise NestError("Nest rejected the access token (401) — re-run setup_oauth.py.")
            if resp.status_code == 403:
                raise NestError(
                    "Nest refused the request (403). Usually the Device Access project has "
                    "not been linked to this thermostat, or the Smart Device Management API "
                    "is not enabled on the Cloud project.")
            if resp.status_code == 429 or "RESOURCE_EXHAUSTED" in resp.text:
                raise NestError(
                    "Nest throttled the request (RESOURCE_EXHAUSTED). The per-device ceiling "
                    "is 5/min and 100/hour across all projects — something is calling too often.")
            if resp.status_code >= 500:
                last = f"{method} {endpoint} -> {resp.status_code}"
                if attempt < RETRY_ATTEMPTS:
                    time.sleep(RETRY_BACKOFF_SECONDS * attempt)
                continue
            if not resp.ok:
                raise NestError(f"{method} {endpoint} -> {resp.status_code}: {resp.text[:300]}")
            return resp.json() if resp.content else {}

        raise NestError(f"Nest unreachable after {RETRY_ATTEMPTS} tries — {last}")

    # -- api ---------------------------------------------------------------

    def list_devices(self) -> list:
        data = self._request("GET", f"enterprises/{self.project_id}/devices")
        return data.get("devices", []) if isinstance(data, dict) else []

    def get_device(self, name: str) -> dict:
        """`name` is the full enterprises/.../devices/... string."""
        return self._request("GET", name)

    def command(self, name: str, command: str, params: dict) -> dict:
        return self._request("POST", f"{name}:executeCommand",
                             {"command": command, "params": params})

    # -- traits ------------------------------------------------------------

    @staticmethod
    def traits(device: dict) -> dict:
        return device.get("traits") or {}

    @classmethod
    def read(cls, device: dict) -> dict:
        """The thermostat as the user would describe it, in Fahrenheit."""
        t = cls.traits(device)
        setpoint = t.get("sdm.devices.traits.ThermostatTemperatureSetpoint") or {}
        eco = t.get("sdm.devices.traits.ThermostatEco") or {}
        return {
            "online": (t.get("sdm.devices.traits.Connectivity") or {}).get("status") == "ONLINE",
            "mode": (t.get("sdm.devices.traits.ThermostatMode") or {}).get("mode"),
            "available_modes": (t.get("sdm.devices.traits.ThermostatMode") or {}).get("availableModes", []),
            "hvac": (t.get("sdm.devices.traits.ThermostatHvac") or {}).get("status"),
            "eco": eco.get("mode"),
            "ambient_f": c_to_f((t.get("sdm.devices.traits.Temperature") or {}).get("ambientTemperatureCelsius")),
            "humidity": (t.get("sdm.devices.traits.Humidity") or {}).get("ambientHumidityPercent"),
            "heat_f": c_to_f(setpoint.get("heatCelsius")),
            "cool_f": c_to_f(setpoint.get("coolCelsius")),
            "eco_heat_f": c_to_f(eco.get("heatCelsius")),
            "eco_cool_f": c_to_f(eco.get("coolCelsius")),
            "scale": (t.get("sdm.devices.traits.Settings") or {}).get("temperatureScale"),
        }

    # -- control -----------------------------------------------------------

    def set_mode(self, name: str, mode: str) -> dict:
        mode = mode.upper()
        if mode not in MODES:
            raise NestError(f"mode must be one of {', '.join(MODES)}, not {mode!r}")
        return self.command(name, "sdm.devices.commands.ThermostatMode.SetMode", {"mode": mode})

    def set_eco(self, name: str, on: bool) -> dict:
        return self.command(name, "sdm.devices.commands.ThermostatEco.SetMode",
                            {"mode": "MANUAL_ECO" if on else "OFF"})

    def set_temperature_f(self, name: str, temp_f: float, device: Optional[dict] = None,
                          spread_f: float = 4.0) -> dict:
        """Set the target to `temp_f`, picking the command the current mode requires.

        Handles the three things the API rejects outright, each of which otherwise
        surfaces as an unexplained 400:
          - eco mode blocks setpoints, so eco is cleared first;
          - the command must match the mode (SetHeat/SetCool/SetRange);
          - in HEATCOOL both bounds are required and cool must exceed heat, so the
            target is bracketed by `spread_f`.
        Refuses when the thermostat is OFF rather than silently doing nothing.
        """
        # The safety bound, checked before anything is sent and before eco is
        # touched, so a refused request changes nothing at all. Every path reaches
        # the HVAC through this method — control.py, a cron, the agent — which is why
        # it lives here and not in an argument parser or a plugin hook. A hook only
        # sees the agent's tool calls; a cron would walk straight past it.
        if not (SAFE_MIN_F <= float(temp_f) <= SAFE_MAX_F):
            raise NestError(
                f"Refusing {round(float(temp_f))}°F: outside the safe band "
                f"{SAFE_MIN_F}-{SAFE_MAX_F}°F for the house. Below {SAFE_MIN_F} risks "
                f"pipes in a freeze. This is a hard limit in nest_client.py, not a setting.")

        state = self.read(device if device is not None else self.get_device(name))
        done: dict = {"target_f": round(float(temp_f))}

        if state.get("eco") == "MANUAL_ECO":
            self.set_eco(name, False)
            done["eco_cleared"] = True

        mode = (state.get("mode") or "").upper()
        if mode == "OFF":
            raise NestError(
                "The thermostat is OFF, so a target temperature would do nothing. "
                "Set a mode first: control.py --mode heat|cool|heatcool")

        if mode == "HEATCOOL":
            half = float(spread_f) / 2
            heat_c, cool_c = f_to_c(temp_f - half), f_to_c(temp_f + half)
            if cool_c <= heat_c:                      # the API requires cool > heat
                cool_c = heat_c + 0.5
            self.command(name, "sdm.devices.commands.ThermostatTemperatureSetpoint.SetRange",
                         {"heatCelsius": heat_c, "coolCelsius": cool_c})
            done.update({"mode": mode, "heat_f": c_to_f(heat_c), "cool_f": c_to_f(cool_c)})
            return done

        command, field = SETPOINT_COMMAND[mode]
        self.command(name, command, {field: f_to_c(temp_f)})
        done["mode"] = mode
        return done
