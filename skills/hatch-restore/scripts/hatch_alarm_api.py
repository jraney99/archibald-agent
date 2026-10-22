"""
Synchronous REST client for Hatch's scheduled-routine (alarm) API — plain
HTTP + auth token, no MQTT/AWS IoT involved, same request-per-call shape as
govee_client.py.

Why this exists instead of using hatch_rest_api's alarm support directly:
alarm editing was only added on hatch_rest_api's GitHub main branch, which
requires Python >=3.12 (this machine runs the system Python 3.9 that
hatch_client.py's MQTT code depends on). Rather than pull in a second Python
install, this module ports the alarm REST calls and payload-building logic
straight from that main branch (dahlb/hatch_rest_api, scheduled_routine.py +
hatch.py) into plain requests calls. Endpoints and payload shapes verified
live against the user's account (one restoreIot alarm) on
2026-08-10.

Known gap vs. the original: alarm_update_payload()'s auto-reschedule of a
*one-time* (daysOfWeek == 0) alarm to its next occurrence when re-enabled is
not ported here — the user's alarm is recurring (daysOfWeek=126), and porting
that edge case added real complexity for a path that isn't in use. If a
one-time alarm is ever added in the Hatch app, enabling/disabling it here
won't auto-advance the date the way the app does.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Optional

import requests

API_URL = "https://data.hatchbaby.com/"
OPENCLAW_JSON = Path(os.path.expanduser("~/.openclaw/openclaw.json")).expanduser()
ALARM_ROUTINE_TYPE = "alarm"

WEEKDAY_BITS: dict[str, int] = {
    "monday": 2,
    "tuesday": 4,
    "wednesday": 8,
    "thursday": 16,
    "friday": 32,
    "saturday": 64,
    "sunday": 1,
}
WEEKDAY_SHORT_NAMES: dict[str, str] = {
    "monday": "Mon",
    "tuesday": "Tue",
    "wednesday": "Wed",
    "thursday": "Thu",
    "friday": "Fri",
    "saturday": "Sat",
    "sunday": "Sun",
}
WEEKDAYS_MASK = sum(WEEKDAY_BITS.values())
WEEKDAYS_ONLY_MASK = (
    WEEKDAY_BITS["monday"]
    | WEEKDAY_BITS["tuesday"]
    | WEEKDAY_BITS["wednesday"]
    | WEEKDAY_BITS["thursday"]
    | WEEKDAY_BITS["friday"]
)
WEEKENDS_MASK = WEEKDAY_BITS["saturday"] | WEEKDAY_BITS["sunday"]


class HatchAlarmError(Exception):
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
        raise HatchAlarmError("No Hatch credentials found (HATCH_EMAIL / HATCH_PASSWORD).")
    return email, password


def _check(resp: requests.Response) -> dict:
    if not resp.ok:
        raise HatchAlarmError(f"HTTP {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    if data.get("status") != "success":
        raise HatchAlarmError(f"api error: {data.get('message')}")
    return data


def login() -> str:
    email, password = _credentials()
    resp = requests.post(
        API_URL + "public/v1/login", json={"email": email, "password": password}, timeout=15
    )
    return _check(resp)["token"]


def fetch_alarms(token: str, mac: str) -> list[dict]:
    resp = requests.get(
        API_URL + "service/app/routine/v3/fetch",
        headers={"X-HatchBaby-Auth": token},
        params={"macAddress": mac, "types": "alarm"},
        timeout=15,
    )
    alarms = _check(resp)["payload"]
    alarms.sort(key=lambda a: a.get("displayOrder", float("inf")))
    return alarms


def _edit(token: str, mac: str, mrds: list[dict]) -> list[dict]:
    resp = requests.post(
        API_URL + "service/app/routine/v2/editMultiple",
        headers={"X-HatchBaby-Auth": token},
        json={"mrds": mrds, "type": ALARM_ROUTINE_TYPE},
        timeout=15,
    )
    payload = _check(resp)["payload"]
    updated = [r for r in (payload.get("item") or []) if r.get("type") == ALARM_ROUTINE_TYPE]

    if payload.get("confirmDataVersion") and payload.get("dataVersion"):
        resp2 = requests.post(
            API_URL + "service/app/v2/dataVersion",
            headers={"X-HatchBaby-Auth": token},
            json={
                "dataVersion": payload["dataVersion"],
                "macAddress": mac,
                "success": True,
                "returnAllRoutines": True,
            },
            timeout=15,
        )
        confirmed = _check(resp2)["payload"]
        if confirmed:
            updated = [r for r in confirmed if r.get("type") == ALARM_ROUTINE_TYPE]

    return updated


def set_alarm_enabled(token: str, mac: str, alarm: dict, enabled: bool) -> list[dict]:
    return _edit(token, mac, [_enabled_payload(alarm, enabled)])


def set_alarm_wake_time(token: str, mac: str, alarm: dict, wake_time: time) -> list[dict]:
    return _edit(token, mac, [_wake_time_payload(alarm, wake_time)])


def set_alarm_weekdays(token: str, mac: str, alarm: dict, weekdays) -> list[dict]:
    return _edit(token, mac, [_weekdays_payload(alarm, list(weekdays))])


def weekdays_to_days_of_week(weekdays) -> int:
    if isinstance(weekdays, str):
        weekdays = [weekdays]
    days_of_week = 0
    for weekday in weekdays:
        key = str(weekday).strip().lower()
        if key not in WEEKDAY_BITS:
            raise HatchAlarmError(f"Unsupported alarm weekday: {weekday}")
        days_of_week |= WEEKDAY_BITS[key]
    return days_of_week


def days_of_week_to_weekdays(days_of_week: Optional[int]) -> list[str]:
    d = days_of_week or 0
    return [weekday for weekday, bit in WEEKDAY_BITS.items() if d & bit]


def days_of_week_label(days_of_week: Optional[int]) -> str:
    d = days_of_week or 0
    if d == 0:
        return "Once"
    if d == WEEKDAYS_ONLY_MASK:
        return "Weekdays"
    if d == WEEKENDS_MASK:
        return "Weekends"
    if d == WEEKDAYS_MASK:
        return "Every day"
    return ", ".join(WEEKDAY_SHORT_NAMES[w] for w in days_of_week_to_weekdays(d))


def effective_wake_datetime(alarm: dict) -> Optional[datetime]:
    """
    The actual "you're awake" time. For most alarms that's endTime, but a
    sunrise alarm often stores only startTime (when the sunrise ramp begins)
    and leaves endTime null — the true wake time is startTime + however long
    the Sunrise step ramps for. Falls back to startTime alone if there's no
    sunrise step to measure. Returns None if neither time is parseable.
    """
    parsed_end = _parse_local_datetime(alarm.get("endTime"))
    if parsed_end is not None:
        return parsed_end
    parsed_start = _parse_local_datetime(alarm.get("startTime"))
    if parsed_start is None:
        return None
    duration = _alarm_sunrise_duration_seconds(alarm)
    if duration is None:
        return parsed_start
    return parsed_start + timedelta(seconds=duration)


# -- payload builders (ported from hatch_rest_api's scheduled_routine.py) --


def _enabled_payload(alarm: dict, enabled: bool) -> dict:
    if "id" not in alarm:
        raise HatchAlarmError("Cannot update alarm without an id")
    return {
        "id": alarm["id"],
        "name": alarm.get("name"),
        "active": alarm.get("active"),
        "enabled": enabled,
        "displayOrder": alarm.get("displayOrder"),
        "startTime": alarm.get("startTime"),
        "endTime": alarm.get("endTime"),
    }


def _weekdays_payload(alarm: dict, weekdays: list[str]) -> dict:
    if "id" not in alarm:
        raise HatchAlarmError("Cannot update alarm without an id")
    return {
        "id": alarm["id"],
        "name": alarm.get("name"),
        "active": alarm.get("active"),
        "enabled": alarm.get("enabled"),
        "displayOrder": alarm.get("displayOrder"),
        "startTime": alarm.get("startTime"),
        "endTime": alarm.get("endTime"),
        "daysOfWeek": weekdays_to_days_of_week(weekdays),
    }


def _wake_time_payload(alarm: dict, wake_time: time) -> dict:
    if "id" not in alarm:
        raise HatchAlarmError("Cannot update alarm without an id")

    start_time = alarm.get("startTime")
    end_time = alarm.get("endTime")
    parsed_start_time = _parse_local_datetime(start_time)
    if parsed_start_time is None:
        raise HatchAlarmError("Cannot update alarm wake time without a valid startTime")
    wake_time = wake_time.replace(tzinfo=None)

    parsed_end_time = _parse_local_datetime(end_time)
    if parsed_end_time is None and end_time is not None:
        raise HatchAlarmError("Cannot update alarm wake time without a valid endTime")

    sunrise_lead = _alarm_sunrise_lead(alarm, parsed_start_time, parsed_end_time)
    is_one_time = alarm.get("daysOfWeek") is None or alarm.get("daysOfWeek") == 0

    if is_one_time:
        updated_end_time = _next_datetime_for_time(wake_time)
    elif parsed_end_time is not None:
        updated_end_time = datetime.combine(parsed_end_time.date(), wake_time)
    else:
        existing_wake_time = parsed_start_time + sunrise_lead
        updated_end_time = datetime.combine(existing_wake_time.date(), wake_time)
    updated_start_time = updated_end_time - sunrise_lead

    return {
        "id": alarm["id"],
        "name": alarm.get("name"),
        "active": alarm.get("active"),
        "enabled": alarm.get("enabled"),
        "displayOrder": alarm.get("displayOrder"),
        "startTime": _format_like_original(start_time, updated_start_time),
        "endTime": _format_like_original(end_time, updated_end_time) if end_time is not None else None,
    }


def _alarm_sunrise_lead(alarm: dict, parsed_start_time: datetime, parsed_end_time) -> timedelta:
    if parsed_end_time is not None:
        return parsed_end_time - parsed_start_time
    sunrise_duration = _alarm_sunrise_duration_seconds(alarm)
    if sunrise_duration is None:
        raise HatchAlarmError("Cannot update alarm wake time without a valid sunrise duration")
    return timedelta(seconds=sunrise_duration)


def _alarm_sunrise_duration_seconds(alarm: dict) -> Optional[int]:
    steps = alarm.get("steps") or []
    sunrise_step_duration = next(
        (
            duration
            for step in steps
            if isinstance(step, dict)
            and str(step.get("name", "")).lower() == "sunrise"
            and (duration := _step_color_duration_seconds(step)) is not None
        ),
        None,
    )
    if sunrise_step_duration is not None:
        return sunrise_step_duration
    return next(
        (
            duration
            for step in steps
            if isinstance(step, dict) and (duration := _step_color_duration_seconds(step)) is not None
        ),
        None,
    )


def _step_color_duration_seconds(step: dict) -> Optional[int]:
    if step.get("enabled") is False:
        return None
    color = step.get("color")
    if not isinstance(color, dict) or color.get("ignore") is True:
        return None
    duration = color.get("duration")
    if isinstance(duration, bool) or not isinstance(duration, int) or duration <= 0:
        return None
    return duration


def _next_datetime_for_time(value: time, now: Optional[datetime] = None) -> datetime:
    value = value.replace(tzinfo=None)
    if now is None:
        now = datetime.now()
    if now.tzinfo is not None:
        now = now.replace(tzinfo=None)
    next_time = datetime.combine(now.date(), value)
    if next_time <= now:
        next_time = next_time + timedelta(days=1)
    return next_time


def _parse_local_datetime(value) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return parsed.replace(tzinfo=None)
    return parsed


def _format_like_original(original, value: datetime) -> str:
    if not isinstance(original, str):
        return value.isoformat(timespec="seconds")
    if "." in original:
        formatted = value.isoformat()
    elif len(original.split("T", 1)[-1].split(":")) <= 2:
        formatted = value.isoformat(timespec="minutes")
    else:
        formatted = value.isoformat(timespec="seconds")
    if "T" not in original and " " in original:
        return formatted.replace("T", " ", 1)
    return formatted
