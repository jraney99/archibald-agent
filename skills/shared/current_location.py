#!/usr/bin/env python3
"""
Single source of truth for "where is the user right now."

detect_travel.py updates hevy_config.json's `timezone` (and `latitude`/
`longitude`, once a location changes) whenever it detects a flight or hotel
stay on the calendar. Any script that needs to know the user's current local
time zone should read it from here instead of hardcoding America/Chicago.
"""
from __future__ import annotations

import os
import json
from pathlib import Path
from zoneinfo import ZoneInfo

HEVY_CONFIG = Path(os.path.expanduser("~/.openclaw/workspace/Skills/hevy-workout/config/hevy_config.json")).expanduser()

HOME_TZ = "America/Chicago"
HOME_LAT, HOME_LON = 40.0000, -75.0000  # home (placeholder coordinates)

# Equipment profiles that mean the user is in his home city, near his kitchen and
# its prepped/frozen meals. Anything else (hotel, gym_b, bodyweight_only,
# …) means he is away and can't act on a freezer/fridge reminder. Must stay in
# sync with detect_travel.py's AUTO_PROFILES and the Gym Photo Handler in
# RULES.md, which saves a home gym-equipment photo under "home_gym" (not
# "home" — detect_equipment.py's update_profile() writes active_profile to
# whatever --profile-name was passed).
HOME_EQUIPMENT_PROFILES = {"gym_a", "home", "home_gym"}


def _load_config() -> dict:
    try:
        return json.loads(HEVY_CONFIG.read_text())
    except Exception:
        return {}


def get_tz_name() -> str:
    """Current IANA timezone name, e.g. 'America/Los_Angeles'."""
    cfg = _load_config()
    return cfg.get("timezone") or cfg.get("home_timezone") or HOME_TZ


def get_tz() -> ZoneInfo:
    return ZoneInfo(get_tz_name())


def is_away_from_home(cfg: dict | None = None) -> bool:
    """True when the user is travelling / not near his home kitchen.

    Two independent signals, either one is enough:
      - hevy-workout's vacation_mode.active (fires on a vacation-keyword
        calendar event), or
      - detect_travel.py has switched the active equipment profile off a
        home-city profile after spotting a flight or hotel stay (catches
        short business trips that vacation_mode misses).

    Any script that prescribes a physical action at home (thaw a meal, take
    something out of the freezer, water the plants) should gate on this.
    """
    if cfg is None:
        cfg = _load_config()
    if cfg.get("vacation_mode", {}).get("active", False):
        return True
    return cfg.get("equipment", {}).get("active_profile") not in HOME_EQUIPMENT_PROFILES


def get_coords() -> tuple[float, float]:
    """Current (latitude, longitude), falling back to home if unset."""
    cfg = _load_config()
    lat = cfg.get("latitude")
    lon = cfg.get("longitude")
    if lat is None or lon is None:
        return (HOME_LAT, HOME_LON)
    return (lat, lon)
