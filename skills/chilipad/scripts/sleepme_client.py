#!/usr/bin/env python3
"""
sleep.me developer API client (https://api.developer.sleep.me/v1) — the ChiliPad
Dock Pro under the user's mattress.

Why this is the easy case: unlike the Hatch (no public API, AWS IoT device
shadows over live MQTT — see hatch-restore/SKILL.md), sleep.me publishes a real
REST API for the Wi-Fi units and hands out a token from the account portal. One
request in, one response out, plain `requests`, no SDK.

Auth: a bearer token from the sleep.me account portal → Developer API (free, per
account, no OAuth). Read from SLEEPME_API_TOKEN first, falling back to
openclaw.json's env.vars block — the same lookup govee_client.py uses for
GOVEE_API_KEY, so the gateway's launchd env picks it up too.

Endpoints (confirmed 2026-10-06 against docs.developer.sleep.me plus two
independent clients: nicolai86/sleepme and the Home Assistant integration
rsampayo/sleepme_thermostat):
  GET   /devices          every Dock Pro on the account -> [{id, name, attachments}]
  GET   /devices/{id}     -> {about, control, status}
  PATCH /devices/{id}     {"set_temperature_f": 60} | {"thermal_control_status": "active"}

Rate limit: the docs state 10 requests per discrete minute window per ACCOUNT
(not per device), and the API answers 429 freely. The ceiling here is 9/min to
leave one slot spare, enforced as a sliding window in-process, and a 429's
Retry-After is honoured. This skill makes 1-3 calls per run a handful of times a
day, so the limit only bites if several scripts run at once.

SENTINEL TEMPERATURES. The device takes 55-115 F (13.0-48.0 C in half-degree
steps), but the API also uses two values that are not temperatures at all: -1
means "max cool" and 999 means "max heat". They must never be printed or
arithmetic'd as degrees — the Home Assistant integration's issue #50 was exactly
that, surfacing 999 C to users as 1830 F. describe_temp() turns them back into
words and clamp_f() keeps real setpoints inside the physical range.
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

API_BASE = "https://api.developer.sleep.me/v1"
# Matches RUNTIME in shared/test_data_layout.py: openclaw.json is the deployment's
# own credential file, not skill data, so it is the one path a script may name.
OPENCLAW_JSON = Path(os.path.expanduser("~/.openclaw/openclaw.json")).expanduser()

TEMP_MIN_F, TEMP_MAX_F = 55, 115
TEMP_MIN_C, TEMP_MAX_C = 13.0, 48.0
MAX_COOL, MAX_HEAT = -1, 999

# A HARD SAFETY BOUND, narrower than what the hardware will accept, enforced at the
# moment of sending and not configurable.
#
# WHY (2026-10-07): the deployment can now move physical things, and the agent reads
# untrusted inbound email. outbound-guard stops data leaving; nothing stopped the
# agent being TALKED INTO setting the bed the user is asleep on to 115°F. The hardware
# ceiling is not a safety limit — it is just what the pump can do.
#
# 100°F leaves plenty of headroom over the 92°F warm entry the user actually uses. The
# floor is the hardware floor, because cold is uncomfortable rather than dangerous.
# The sentinels are refused outright: "max heat" is exactly the instruction an
# injection would reach for, and it bypasses any numeric check.
#
# In code, not config, on purpose: a bound that a config edit could widen is not a
# bound. Config may narrow this (see chilipad_config.json) but never exceed it.
SAFE_MIN_F, SAFE_MAX_F = 55, 100

# 10/min per account is the documented ceiling; 9 leaves a slot for a manual
# status check landing in the same minute as a cron run.
MAX_REQUESTS_PER_MINUTE = 9
RATE_LIMIT_WINDOW = 60

# A 429 or 5xx on the way to the bed is worth retrying — the alternative is a
# night at the wrong temperature. Capped low because the cron jobs that call this
# have their own timeouts.
RETRY_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 20
RETRY_AFTER_CAP_SECONDS = 120


class SleepMeError(Exception):
    pass


def _get_token() -> str:
    val = os.environ.get("SLEEPME_API_TOKEN", "")
    if val:
        return val
    try:
        cfg = json.loads(OPENCLAW_JSON.read_text())
        return cfg.get("env", {}).get("vars", {}).get("SLEEPME_API_TOKEN", "")
    except Exception:  # noqa: BLE001 — a missing/garbled config reads as "no token"
        return ""


def clamp_f(value: float) -> int:
    """A real Fahrenheit setpoint, inside what the hardware will accept.

    Clamps rather than raising: a config typo should cost the user a degree, not the
    night's cooling. The sentinels are not routed through here — use
    MAX_COOL/MAX_HEAT directly.
    """
    return int(round(min(max(float(value), TEMP_MIN_F), TEMP_MAX_F)))


def floor_f(cfg: dict | None) -> int | None:
    """The user's own lower limit for anything the SCHEDULE sets, or None if he hasn't set one.

    2026-10-08, after three cold nights walked the floor up from 72°F: "The absolute coldest it
    should go right now is 85 degrees." `min_temp_f` is that line. It is enforced where the
    temperatures are DECIDED — the trial ladder, the night's phases, the descent's steps — so no
    config edit, stale ladder or rounding can put the bed below it. A temperature he types
    himself is not clamped: he is allowed to overrule his own floor, he just has to say so.
    """
    raw = (cfg or {}).get("min_temp_f")
    try:
        return clamp_f(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def at_least_floor(value: float, cfg: dict | None) -> int:
    """`value` as a real setpoint, never below the user's floor."""
    lowest = floor_f(cfg)
    kept = clamp_f(value)
    return max(kept, lowest) if lowest is not None else kept


def clamp_c(value: float) -> float:
    """Celsius setpoint, rounded to the half degree the API documents."""
    snapped = round(float(value) * 2) / 2
    return min(max(snapped, TEMP_MIN_C), TEMP_MAX_C)


def describe_temp(value: Any, unit: str = "F") -> str:
    """A temperature as the user should read it, sentinels included.

    Never format a raw set_temperature_* yourself — 999 is not a temperature.
    """
    if value is None:
        return "unknown"
    try:
        num = float(value)
    except (TypeError, ValueError):
        return str(value)
    if num == MAX_COOL:
        return "max cool"
    if num == MAX_HEAT:
        return "max heat"
    if num == int(num):
        return f"{int(num)}°{unit}"
    return f"{num:g}°{unit}"


def parse_temp_arg(raw: str) -> tuple[str, Any]:
    """A --temp value from the command line -> (api_field, api_value).

    Accepts "60", "60F", "21C", "max-cool", "max-heat". Returns the field name so
    the caller never has to decide between set_temperature_f and _c.
    """
    text = str(raw).strip().lower().replace("°", "")
    if text in ("max-cool", "maxcool", "max cool", "cool", "coldest"):
        return "set_temperature_f", MAX_COOL
    if text in ("max-heat", "maxheat", "max heat", "heat", "hottest"):
        return "set_temperature_f", MAX_HEAT
    if text.endswith("c"):
        return "set_temperature_c", clamp_c(text[:-1])
    if text.endswith("f"):
        return "set_temperature_f", clamp_f(text[:-1])
    try:
        return "set_temperature_f", clamp_f(text)
    except ValueError:
        raise SleepMeError(
            f"--temp {raw!r} is not a temperature. Use 60, 21C, max-cool or max-heat."
        ) from None


def load_devices() -> dict:
    """{nickname: {"id": ..., "name": ...}} from config, written by discover_devices.py."""
    try:
        return json.loads(data_path("chilipad_devices.json").read_text()).get("devices", {})
    except FileNotFoundError:
        return {}
    except ValueError as e:
        raise SleepMeError(f"chilipad_devices.json is not valid JSON: {e}") from None


def load_config() -> dict:
    """The user's settings. Missing file is fatal on purpose: the defaults live in the
    file he edits, not scattered through the scripts."""
    try:
        return json.loads(data_path("chilipad_config.json").read_text())
    except FileNotFoundError:
        raise SleepMeError(
            "chilipad_config.json is missing — see chilipad/SKILL.md's Setup section."
        ) from None


def resolve_device(nickname: Optional[str] = None) -> tuple[str, str]:
    """(device_id, nickname) for a nickname, or for the configured default."""
    devices = load_devices()
    if not devices:
        raise SleepMeError(
            "No ChiliPad configured yet — run `python3 scripts/discover_devices.py` first."
        )
    if nickname is None:
        try:
            nickname = load_config().get("device") or next(iter(devices))
        except SleepMeError:
            nickname = next(iter(devices))
    entry = devices.get(nickname)
    if entry is None:
        raise SleepMeError(
            f"No ChiliPad nicknamed {nickname!r}. Configured: {', '.join(sorted(devices)) or 'none'}."
        )
    return entry["id"], nickname


class SleepMeClient:
    def __init__(self, token: Optional[str] = None) -> None:
        tok = token or _get_token()
        if not tok:
            raise SleepMeError(
                "No sleep.me API token found. Generate one at sleep.me → account portal → "
                "Developer API, then set SLEEPME_API_TOKEN in openclaw.json's env.vars block."
            )
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {tok}",
            "Content-Type": "application/json",
        })
        self._request_times: list[float] = []

    # -- transport ---------------------------------------------------------

    def _wait_for_slot(self) -> None:
        """Sliding window against the account-wide 10/min ceiling."""
        now = time.monotonic()
        self._request_times = [t for t in self._request_times if now - t < RATE_LIMIT_WINDOW]
        if len(self._request_times) >= MAX_REQUESTS_PER_MINUTE:
            sleep_for = RATE_LIMIT_WINDOW - (now - self._request_times[0]) + 0.5
            if sleep_for > 0:
                time.sleep(sleep_for)
            now = time.monotonic()
            self._request_times = [t for t in self._request_times if now - t < RATE_LIMIT_WINDOW]
        self._request_times.append(time.monotonic())

    @staticmethod
    def _retry_after(resp: Optional[requests.Response], attempt: int) -> float:
        """How long to wait, preferring what the server asked for."""
        if resp is not None:
            raw = resp.headers.get("Retry-After")
            if raw:
                try:
                    return min(float(raw), RETRY_AFTER_CAP_SECONDS)
                except ValueError:
                    pass
        return RETRY_BACKOFF_SECONDS * attempt

    def _request(self, method: str, endpoint: str, payload: Optional[dict] = None) -> Any:
        last_error = "no attempt made"
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            self._wait_for_slot()
            try:
                resp = self._session.request(
                    method, f"{API_BASE}/{endpoint.lstrip('/')}",
                    json=payload, timeout=20,
                )
            except requests.RequestException as e:
                last_error = f"{method} {endpoint}: {e}"
                if attempt < RETRY_ATTEMPTS:
                    time.sleep(RETRY_BACKOFF_SECONDS * attempt)
                continue

            if resp.status_code == 401:
                raise SleepMeError(
                    "sleep.me rejected the token (401). Regenerate it in the account "
                    "portal's Developer API section and update SLEEPME_API_TOKEN."
                )
            if resp.status_code == 403:
                raise SleepMeError(
                    "sleep.me refused the request (403) — the token is valid but not "
                    "allowed on this device."
                )
            if resp.status_code == 404:
                raise SleepMeError(
                    f"sleep.me has no such device ({endpoint}). Re-run discover_devices.py — "
                    "the id changes if the unit is re-registered."
                )
            if resp.status_code == 429 or resp.status_code >= 500:
                last_error = f"{method} {endpoint} -> {resp.status_code}: {resp.text[:200]}"
                if attempt < RETRY_ATTEMPTS:
                    time.sleep(self._retry_after(resp, attempt))
                continue
            if not resp.ok:
                raise SleepMeError(f"{method} {endpoint} -> {resp.status_code}: {resp.text[:300]}")

            if not resp.content:
                return {}
            try:
                return resp.json()
            except ValueError:
                raise SleepMeError(
                    f"{method} {endpoint} returned non-JSON: {resp.text[:200]}"
                ) from None

        raise SleepMeError(f"sleep.me unreachable after {RETRY_ATTEMPTS} tries — {last_error}")

    # -- api ---------------------------------------------------------------

    def list_devices(self) -> list[dict]:
        data = self._request("GET", "devices")
        return data if isinstance(data, list) else data.get("devices", [])

    def get_device(self, device_id: str) -> dict:
        return self._request("GET", f"devices/{device_id}")

    def update(self, device_id: str, field: str, value: Any) -> dict:
        """One field per PATCH.

        Both verified clients send a single key per call, so that is what this
        does. A combined body (temperature + status together) looks like it should
        work and may well, but nothing observed confirms it, and two requests out
        of a 9/min budget is not worth the guess.
        """
        return self._request("PATCH", f"devices/{device_id}", {field: value})

    def set_temperature(self, device_id: str, field: str, value: Any) -> dict:
        """The ONE place a temperature reaches the bed. Safety bound enforced here.

        Every path — control.py, ramp.py, a cron, the agent, a future script —
        funnels through this method, which is why the check lives here rather than in
        an argument parser or a plugin hook. A hook only sees the agent's tool calls;
        a cron job would walk straight past it.
        """
        if field not in ("set_temperature_f", "set_temperature_c"):
            raise SleepMeError(f"not a temperature field: {field!r}")

        as_f = value if field == "set_temperature_f" else (float(value) * 9 / 5 + 32)
        if value in (MAX_HEAT, MAX_COOL) or float(as_f) > 1000:
            raise SleepMeError(
                f"Refusing the {'max heat' if value == MAX_HEAT else 'max cool'} preset on a "
                f"bed being slept in. Ask for a number between {SAFE_MIN_F} and {SAFE_MAX_F}°F.")
        if not (SAFE_MIN_F <= float(as_f) <= SAFE_MAX_F):
            raise SleepMeError(
                f"Refusing {round(float(as_f))}°F: outside the safe band "
                f"{SAFE_MIN_F}-{SAFE_MAX_F}°F for a bed. This is a hard limit in "
                f"sleepme_client.py, not a setting.")
        # The user's own floor, narrower than the safety band and his to move (min_temp_f).
        # 2026-10-08: "The absolute coldest it should go right now is 85 degrees." Enforced
        # HERE because this is the one place a temperature reaches the bed — a check in the
        # schedulers would be walked past by a manual call, and one in a plugin hook would be
        # walked past by every cron job. A config that cannot be read never blocks the bed.
        try:
            lowest = floor_f(load_config())
        except Exception:  # noqa: BLE001
            lowest = None
        if lowest is not None and float(as_f) < lowest:
            raise SleepMeError(
                f"Refusing {round(float(as_f))}°F: below the {lowest}°F floor Master Wayne set "
                f"(min_temp_f in chilipad_config.json). Change that if he means to go colder.")
        return self.update(device_id, field, value)

    def set_thermal_status(self, device_id: str, status: str) -> dict:
        if status not in ("active", "standby"):
            raise SleepMeError(f"thermal_control_status must be 'active' or 'standby', not {status!r}")
        return self.update(device_id, "thermal_control_status", status)
