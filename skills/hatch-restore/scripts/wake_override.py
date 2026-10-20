#!/usr/bin/env python3
"""A wake time the user set himself, and the one morning it is for.

WHY (2026-09-20): the 9pm sync (sync_alarm.compute_and_apply) reset the Hatch to the
default every night unless the calendar needed an earlier wake, so a time the user had set
himself that day was overwritten — and the bedroom sunrise, retimed once at 9:15pm,
never heard about a change made after that. The light came on at 7:00 whatever his
alarm said, so he unplugged the lamp at night. 56 of 70 "light failures" in a month
were that unplugged lamp.

alarm.py records the time here whenever it changes the wake time. The 9pm sync keeps
it for that one morning (a calendar event that needs an EARLIER wake still wins — a
flight is never slept through), and it expires by itself: the next night is back to
calendar + default.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))
from data_paths import path  # noqa: E402

NAME = "manual_wake.json"


def wake_date(wake: time, now: datetime) -> date:
    """The morning a newly set wake time is for: today if that time is still ahead of
    `now` (set at 1am for 9:30am), otherwise tomorrow (set at 8pm for 9am)."""
    return now.date() if now.time() < wake else now.date() + timedelta(days=1)


def record(wake: time, now: datetime) -> dict:
    entry = {"time": wake.strftime("%H:%M"), "for_date": wake_date(wake, now).isoformat(),
             "set_at": now.isoformat(timespec="seconds")}
    target = path(NAME)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(entry, indent=2) + "\n")
    return entry


# ── noticing a change the user made on the Hatch itself (in the app, at the device) ──────────────
# Nothing tells us when he does that, so the sync compares the device with the last wake time
# it knows was NOT a new decision of his: what the 9pm sync applied, refreshed pre-dawn with
# what the alarm actually was for that morning. Device != baseline at 9pm  =>  he changed it
# since this morning  =>  it is his time for tomorrow. (The pre-dawn refresh is what stops a
# 1am "9:30 for today" from also being read as "9:30 for tomorrow".)
BASELINE = "wake_sync_state.json"


def remember_baseline(wake: time, source: str, now: datetime) -> None:
    target = path(BASELINE)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"time": wake.strftime("%H:%M"), "source": source,
                                  "at": now.isoformat(timespec="seconds")}, indent=2) + "\n")


def baseline() -> time | None:
    try:
        hh, mm = (int(p) for p in json.loads(path(BASELINE).read_text())["time"].split(":"))
        return time(hh, mm)
    except Exception:  # noqa: BLE001 — no baseline yet just means nothing can be inferred tonight
        return None


def changed_on_device(device_wake: time | None) -> time | None:
    """The device's wake time if the user changed it since the baseline, else None."""
    known = baseline()
    if device_wake is None or known is None:
        return None
    same = (device_wake.hour, device_wake.minute) == (known.hour, known.minute)
    return None if same else time(device_wake.hour, device_wake.minute)


def active(for_date: date) -> time | None:
    """The time the user set for `for_date`, or None (nothing set, another day, unreadable)."""
    try:
        entry = json.loads(path(NAME).read_text())
        if entry.get("for_date") != for_date.isoformat():
            return None
        hh, mm = (int(p) for p in entry["time"].split(":"))
        return time(hh, mm)
    except Exception:  # noqa: BLE001 — a bad file must never block the nightly wake sync
        return None
