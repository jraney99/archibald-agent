#!/usr/bin/env python3
"""The live Hatch alarm time, read once and shared for a few minutes.

WHY (2026-10-06): the Hatch is rate-limited, and one alarm change fans out to four followers —
the bedroom lights, the ChiliPad night schedule, the breakfast anchor and the morning briefing —
each of which read the alarm back for itself. Six Hatch calls in a row, and the LAST follower
got HTTP 429: the briefing gave up and left tomorrow's at the old wake time, an hour early.

So `alarm.py` records the time it just wrote and the followers read it from here. A cache this
short cannot hide a change the user makes in the app, and `read_current_wake_time(use_cache=False)`
still goes to the device.

Its own module, with no Hatch imports at all, because `alarm.py` is deliberately MQTT-free
(see SKILL.md) and must not pull in `hatch_client` just to leave a note.
"""
import json
import sys
from datetime import datetime, time as dtime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "shared"))
from data_paths import path  # noqa: E402

CACHE = path("hatch_alarm_cache.json")
MAX_AGE_SECONDS = 180

# The real file, captured at import so a test that repoints CACHE is still allowed.
_LIVE_CACHE = CACHE


def _refuse_live_file() -> bool:
    """True when a test is running and has NOT repointed CACHE.

    WHY (2026-10-12): read_plan() records what it read, and test_sunrise_length.py
    drives read_plan() with a mocked device. Without this, that test wrote a
    fabricated wake time into the live cache, where a real follower running within
    MAX_AGE_SECONDS would have believed it. Same guard chat_cleanup uses on its own
    state file, and for the same reason. A test that points CACHE somewhere
    temporary is unaffected."""
    return "unittest" in sys.modules and CACHE == _LIVE_CACHE


def note(wake) -> None:
    """Record an alarm time just read or written. `None` means the alarm is off."""
    if _refuse_live_file():
        return
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps({"at": datetime.now().timestamp(),
                                     "time": wake.strftime("%H:%M") if wake else None}))
    except OSError:
        pass  # a cache that cannot be written is not worth failing a sync over


def fresh():
    """(time-or-None, True) when a fresh read is on file, else (None, False). A cached
    `None` is a real answer — it means the alarm is switched off."""
    if _refuse_live_file():
        return None, False
    try:
        data = json.loads(CACHE.read_text())
        age = datetime.now().timestamp() - float(data["at"])
    except (OSError, ValueError, KeyError, TypeError):
        return None, False
    if age > MAX_AGE_SECONDS or age < -MAX_AGE_SECONDS:
        return None, False
    hhmm = data.get("time")
    if hhmm is None:
        return None, True
    try:
        hour, minute = (int(p) for p in str(hhmm).split(":"))
    except (ValueError, AttributeError):
        return None, False
    return dtime(hour, minute), True


# ── The whole plan, not just the wake time ────────────────────────────────────
#
# WHY (2026-10-12): `note`/`fresh` above only carry the wake time, but the two
# followers that actually fan out on an alarm change — the bedroom sunrise and
# the ChiliPad night schedule — both go through
# `sync_bedroom_sunrise.read_plan()`, which needs the durations too and so hit
# the device itself every time. Two logins back to back, and the ChiliPad's got
# HTTP 429: "⚠️ ChiliPad → couldn't retimed (Hatch rate-limited on the second
# login)". Caching the whole plan lets the second follower reuse the first
# one's read, which is what the Oct 6 cache was for.
#
# Written into the SAME file, including `time`, so a plan read also satisfies
# every plain `fresh()` caller.

def note_plan(plan: dict) -> None:
    """Record the DEVICE-side facts of a plan just read or just written.

    Only what costs a Hatch call: enabled, the wake datetime and the Hatch's own
    sunrise length. The Govee ramp length is local config, so read_plan()
    recomputes it on every hit and a config change is never served stale."""
    if _refuse_live_file():
        return
    wake = plan.get("wake_dt")
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps({
            "at": datetime.now().timestamp(),
            "time": wake.strftime("%H:%M") if wake else None,
            "plan": {
                "enabled": bool(plan.get("enabled")),
                "wake_iso": wake.isoformat() if wake else None,
                "hatch_duration": plan.get("hatch_duration") or 0,
            },
        }))
    except (OSError, AttributeError, ValueError):
        pass  # a cache that cannot be written is not worth failing a sync over


def fresh_plan():
    """({enabled, wake_dt, hatch_duration}, True) when fresh, else (None, False).

    Deliberately stricter than `fresh()`: a file holding only a wake time (what
    `note()` writes) is a MISS, so a caller that needs the whole plan still does
    one real read rather than inventing the missing fields."""
    if _refuse_live_file():
        return None, False
    try:
        data = json.loads(CACHE.read_text())
        age = datetime.now().timestamp() - float(data["at"])
    except (OSError, ValueError, KeyError, TypeError):
        return None, False
    if age > MAX_AGE_SECONDS or age < -MAX_AGE_SECONDS:
        return None, False
    raw = data.get("plan")
    if not isinstance(raw, dict) or not raw.get("wake_iso"):
        return None, False
    try:
        return {
            "enabled": bool(raw.get("enabled")),
            "wake_dt": datetime.fromisoformat(raw["wake_iso"]),
            "hatch_duration": int(raw.get("hatch_duration") or 0),
        }, True
    except (ValueError, TypeError):
        return None, False


def clear() -> None:
    CACHE.unlink(missing_ok=True)
