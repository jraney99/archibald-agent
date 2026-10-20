#!/usr/bin/env python3
"""
Nightly alarm sync: looks at tomorrow's calendar for any timed event with a
resolvable location, works out how early the user needs to leave (drive time +
arrival buffer, via shared/drive_time.py — golf events get a 45-minute
arrival buffer instead of the usual 5, since the user likes to warm up before
tee time), and if waking wake_before_leave_minutes (default 45) before that
leave time is earlier than the default wake time, retimes the Hatch alarm
for tomorrow to that earlier time. Otherwise resets it to the default.
Always actually applies the change (not a dry run) — this is meant to be
the single trigger for both the device update and the summary line folded
into the 8pm evening-briefing message, not a separate cron job, so it
doesn't duplicate the effort of computing this twice.

Uses hatch_alarm_api.py directly (plain REST, no MQTT session) — same as
alarm.py. Config lives in config/alarm_config.json.

The user wants to always be told what his alarm is set for, so this never fails
silently: any error during the sync (calendar fetch, drive-time lookup,
Hatch login/rate-limit, etc.) is caught, and main() falls back to reading
back whatever the alarm is *currently* set to (unchanged) so there's still
something concrete to report. Always exits 0 and always prints a JSON
object with a `sync_failed` flag — callers (the evening-briefing prompt)
should never treat "no output" as a reason to omit the alarm line; there
should always be *something* to say.

Usage:
  sync_alarm.py            # human-readable one-line summary
  sync_alarm.py --json     # machine-readable, for the evening-briefing prompt
"""
import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, time as dtime
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
SHARED_DIR = SCRIPTS_DIR.parent.parent / "shared"
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(SHARED_DIR))

from hatch_client import load_device_map, HatchError  # noqa: E402
import hatch_alarm_api as alarm_api  # noqa: E402
from drive_time import leave_by_minutes  # noqa: E402
from current_location import get_tz, is_away_from_home  # noqa: E402
import wake_override  # noqa: E402
import alarm_cache  # noqa: E402

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "alarm_config.json"

ACCOUNT = "owner@example.com"
CALENDARS = ",".join([
    "owner@example.com",
    "owner@work.example",
    "contact@example.org",
])


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text())


# "DAL → XYZ" (or -> / ⟶) anywhere in a calendar title — same shape as
# schedule_workouts.py's FLIGHT_RE.
_FLIGHT_RE = re.compile(r'\b([A-Z]{3})\s*(?:→|->|⟶)\s*([A-Z]{3})\b')

# "Leave for XYZ <> the user @ …" — the drive-time block the email-monitor daemon
# drops on the calendar (daemon.py::_schedule_flight_reminders). Its START is
# ALREADY the computed leave-home time and its --location is the *origin* the user
# is leaving from, not a destination to travel to. Same title shape that
# playbypoint-golf and schedule_workouts already special-case. See
# earliest_requirement() for why it must not go through the normal
# drive-time-subtraction path.
_LEAVE_FOR_RE = re.compile(r'(?i)^\s*leave for\b')


def flight_tomorrow(events: list) -> bool:
    """True if any timed event on tomorrow's calendar looks like a flight.

    This is the travel signal the nightly brief actually needs, and it does
    NOT depend on is_away_from_home() — that only flips once detect_travel.py
    has run *during* a trip (never the night before), and its 5:45am cron
    silently no-ops when the Mac is off overnight for an early departure. So
    on the night before the user flies out he'd still read as "home": the brief
    would frame the 4am flight wake as a Hatch line and skip the phone-alarm
    backup nudge — exactly the gap that lost him a wake-up on 2026-09-01.
    A flight on tomorrow's calendar makes the wake travel-sensitive on its
    own. See feedback-travel-detection-lag."""
    for ev in events:
        if "dateTime" not in ev.get("start", {}):
            continue
        if _FLIGHT_RE.search(ev.get("summary", "")):
            return True
    return False


def fetch_tomorrow_events() -> list:
    r = subprocess.run(
        ["gog", "cal", "ls", "--account", ACCOUNT, "--calendars", CALENDARS, "--tomorrow", "-j", "--max", "50"],
        capture_output=True, text=True, timeout=30,
    )
    if r.returncode != 0:
        raise RuntimeError(f"gog cal ls failed: {r.stderr.strip()[:200]}")
    return json.loads(r.stdout).get("events", [])


def is_golf(title: str, keywords: list) -> bool:
    lowered = title.lower()
    return any(kw.lower() in lowered for kw in keywords)


def arrival_buffer_for(title: str, location: str, config: dict) -> int:
    """How long before a start the user needs to BE there.

    A golf course earns the big buffer: park, check in at the pro shop, hit the
    range, walk to the tee. The user, 2026-10-14: that does not apply to "the municipal
    course's night holes or the simulators" — you walk in and start. With one
    flat golf buffer the system told him to leave an HOUR early for a 15-minute
    drive to an indoor bay, and stacked wake_before_leave_minutes on top of that,
    so an 11:30 tee time wanted him up at 9:55.

    Matched against the title AND the location, because an event called
    "Golf <> the user @ Example Sim Bar" says nothing about the venue in its title alone.
    """
    if not is_golf(title, config["golf_title_keywords"]):
        return config["default_arrival_buffer_minutes"]
    haystack = f"{title} {location}".lower()
    for phrase in config.get("golf_quick_entry", []):
        if phrase.lower() in haystack:
            return config.get("golf_quick_entry_buffer_minutes",
                              config["default_arrival_buffer_minutes"])
    return config["golf_arrival_buffer_minutes"]


def earliest_requirement(events: list, tz, config: dict):
    """
    Returns (required_wake_dt, event_title, leave_dt, event_start_dt) for the
    most constraining timed event tomorrow with a resolvable location, or
    (None, None, None, None) if nothing requires an earlier wake than default.

    event_start_dt is returned separately from event_title because the title
    is free text pulled straight from the calendar (often written/edited by
    someone else, e.g. an external organizer) and can go stale relative to
    the event's actual dateTime after a reschedule — callers should report
    the real start_dt alongside the title rather than trust a time mentioned
    in the title string.

    A "Leave for <APT>" block (dropped by the email-monitor daemon) is a
    special case: its start time is ALREADY the leave-home time the daemon
    computed backward from the flight, and its location is the origin, not a
    destination. Running it through the normal drive-time subtraction
    double-counts the commute — that's how a 9:45 AM flight with a correct
    7:00 AM "leave" block produced a 5:37 AM phone-alarm brief and a
    self-contradictory "leave by 6:22 AM". For these, leave_dt == start_dt
    and we only subtract wake_before_leave_minutes.
    """
    best = (None, None, None, None)
    for ev in events:
        start = ev.get("start", {})
        if "dateTime" not in start:
            continue  # all-day event, no specific departure time
        title = ev.get("summary", "(untitled)")
        start_dt = datetime.fromisoformat(start["dateTime"]).astimezone(tz)

        if _LEAVE_FOR_RE.match(title):
            leave_dt = start_dt  # the block start IS the leave-by time
        else:
            location = ev.get("location")
            if not location:
                continue  # can't compute drive time without an address
            arrival_buffer = arrival_buffer_for(title, location, config)
            lead_minutes = leave_by_minutes(location, arrival_buffer=arrival_buffer, strict=False)
            if lead_minutes is None:
                continue  # geocode/routing failed; skip rather than guess
            leave_dt = start_dt - timedelta(minutes=lead_minutes)

        wake_dt = leave_dt - timedelta(minutes=config["wake_before_leave_minutes"])
        if best[0] is None or wake_dt < best[0]:
            best = (wake_dt, title, leave_dt, start_dt)
    return best


def find_back_to_back_conflicts(events: list, tz, config: dict) -> list:
    """
    Wake time only covers the *first* event of the day — it says nothing
    about whether the user can actually get from one timed, located event to
    the next with the buffer it needs (e.g. golf's 45-min warmup). This
    checks every consecutive pair of tomorrow's timed+located events for
    that: is there enough time between event A ending and event B starting
    to drive from A's location to B's location and still arrive with B's
    required buffer? Uses the same leave_by_minutes() as the wake-time
    calc, just with origin_address set to the previous event's location
    instead of home.

    Returns a list of conflict dicts, most-severe (biggest shortfall)
    first. Empty list if nothing conflicts or fewer than 2 qualifying
    events. Events without both a location and dateTime start+end are
    skipped rather than guessed at, same as earliest_requirement().
    """
    located = []
    for ev in events:
        start = ev.get("start", {})
        end = ev.get("end", {})
        location = ev.get("location")
        if "dateTime" not in start or "dateTime" not in end or not location:
            continue
        located.append({
            "title": ev.get("summary", "(untitled)"),
            "start": datetime.fromisoformat(start["dateTime"]).astimezone(tz),
            "end": datetime.fromisoformat(end["dateTime"]).astimezone(tz),
            "location": location,
        })
    located.sort(key=lambda e: e["start"])

    conflicts = []
    for prev, nxt in zip(located, located[1:]):
        if nxt["start"] <= prev["end"]:
            continue  # overlapping events are a scheduling problem, not a travel-time one
        arrival_buffer = arrival_buffer_for(nxt["title"], nxt["location"], config)
        lead_minutes = leave_by_minutes(
            nxt["location"], origin_address=prev["location"], arrival_buffer=arrival_buffer, strict=False
        )
        if lead_minutes is None:
            continue  # geocode/routing failed; skip rather than guess
        required_leave_dt = nxt["start"] - timedelta(minutes=lead_minutes)
        shortfall_minutes = (prev["end"] - required_leave_dt).total_seconds() / 60
        if shortfall_minutes <= 0:
            continue  # feasible
        conflicts.append({
            "from_event": prev["title"],
            "to_event": nxt["title"],
            "from_end_fmt": prev["end"].strftime("%-I:%M %p"),
            "to_start_fmt": nxt["start"].strftime("%-I:%M %p"),
            "required_leave_fmt": required_leave_dt.strftime("%-I:%M %p"),
            "needed_minutes": round(lead_minutes),
            "shortfall_minutes": round(shortfall_minutes),
        })
    conflicts.sort(key=lambda c: -c["shortfall_minutes"])
    return conflicts


def _device_and_token(config: dict):
    device_map = load_device_map()
    mac = device_map[config["device"]]["mac"]
    token = alarm_api.login()
    return mac, token


def _save_alarm_id(alarm_id: str) -> None:
    """Rewrite just the alarm_id value in place, leaving the rest of the
    hand-formatted config file untouched."""
    text = CONFIG_PATH.read_text()
    updated, count = re.subn(r'("alarm_id"\s*:\s*)"[^"]*"', rf'\g<1>"{alarm_id}"', text, count=1)
    if count:
        CONFIG_PATH.write_text(updated)


def _find_alarm(alarms: list, config: dict) -> dict:
    """The pinned alarm_id, or — if it's gone and the device has exactly one
    alarm — that one. Hatch issues a new id whenever an alarm is deleted and
    recreated in the app (2026-09-18: 100000002 -> 100000003), which used to
    fail the sync every night until someone hand-edited the config. With a
    single alarm on the device there's no ambiguity about which one the user
    means, so adopt it and persist the new id. Zero or several alarms is
    genuinely ambiguous and still raises."""
    alarm_id = str(config["alarm_id"])
    alarm = next((a for a in alarms if str(a.get("id")) == alarm_id), None)
    if alarm is not None:
        return alarm
    if len(alarms) != 1:
        raise HatchError(
            f"Alarm {alarm_id} not found, and the device has {len(alarms)} alarms so "
            f"there's no single one to fall back to."
        )
    alarm = alarms[0]
    new_id = str(alarm.get("id"))
    print(f"Alarm {alarm_id} not found — adopting the device's only alarm, {new_id}.", file=sys.stderr)
    config["alarm_id"] = new_id
    _save_alarm_id(new_id)
    return alarm


def compute_and_apply(config: dict, tz, events: list) -> dict:
    """Compute tomorrow's required wake time, then act on it. Raises on any
    failure — main() is what makes this fail-safe.

    The wake-time computation (calendar + drive time + golf buffer) is the
    same whether the user's home or travelling — drive_time.py already swaps its
    origin to whatever home base he's near (see origin_coords_and_label()).
    Only the *action* differs:
      - home:    write the computed time to the Hatch, enabling it if off.
      - away:    the Hatch is sitting in an empty house, so disable it (no
                 blaring in an empty room) and hand the computed time back
                 with away=True so the caller can tell the user what to set his
                 phone alarm for and schedule a backup wake nudge.
    Coming home, the disabled Hatch is re-enabled automatically by the
    `not alarm.get("enabled")` branch below on the first home run.
    """
    default_hh, default_mm = (int(p) for p in config["default_wake_time"].split(":"))
    tomorrow = datetime.now(tz).date() + timedelta(days=1)
    default_wake_dt = datetime.combine(tomorrow, dtime(default_hh, default_mm), tzinfo=tz)

    # A time the user set himself for tomorrow (wake_override.py) replaces the default for that one
    # morning. Until 2026-09-20 this function reset the alarm to the default every night, so
    # "make it 9am tomorrow" said before 9pm was silently undone. The calendar still wins when
    # it needs him up EARLIER than the time he set — a flight is never slept through.
    away = is_away_from_home()

    mac, token = _device_and_token(config)
    alarms = alarm_api.fetch_alarms(token, mac)
    alarm = _find_alarm(alarms, config)

    # ...or a time he set on the Hatch itself (the app, the device): nothing announces that, so
    # compare the device with the last wake time known NOT to be a new decision of his
    # (wake_override.changed_on_device). "If I set the alarm to 9:30, everything should follow."
    manual = wake_override.active(tomorrow)
    if manual is None and not away:
        device_dt = alarm_api.effective_wake_datetime(alarm)
        manual = wake_override.changed_on_device(device_dt.time() if device_dt else None)
    manual_dt = datetime.combine(tomorrow, manual, tzinfo=tz) if manual else None
    baseline_dt = manual_dt or default_wake_dt

    required_wake_dt, event_title, leave_dt, event_start_dt = earliest_requirement(events, tz, config)
    use_override = required_wake_dt is not None and required_wake_dt < baseline_dt
    target = required_wake_dt if use_override else baseline_dt

    if away:
        if alarm.get("enabled"):
            alarm_api.set_alarm_enabled(token, mac, alarm, False)
    else:
        if not alarm.get("enabled"):
            alarm_api.set_alarm_enabled(token, mac, alarm, True)
            alarms = alarm_api.fetch_alarms(token, mac)
            alarm = _find_alarm(alarms, config)
        alarm_api.set_alarm_wake_time(token, mac, alarm, target.time())
        # What the device now holds by OUR doing — the yardstick for spotting his next change.
        wake_override.remember_baseline(target.time(), "9pm-sync", datetime.now(tz))

    return {
        "wake_dt_iso": target.isoformat(),
        "wake_time_fmt": target.strftime("%-I:%M %p"),
        # is_default keeps its old meaning — "no calendar event forced this time" — because every
        # message branch that is not is_default prints event_title / leave_time_fmt.
        "is_default": not use_override,
        # is_manual: the time he set stands. manual_wake_time_fmt is also filled when the
        # calendar overruled it, so the message can say which time lost.
        "is_manual": not use_override and manual_dt is not None,
        "manual_wake_time_fmt": manual_dt.strftime("%-I:%M %p") if manual_dt else None,
        "away": away,
        "flight_tomorrow": flight_tomorrow(events),
        "default_wake_time_fmt": default_wake_dt.strftime("%-I:%M %p"),
        "event_title": event_title if use_override else None,
        "event_start_fmt": event_start_dt.strftime("%-I:%M %p") if use_override else None,
        "leave_time_fmt": leave_dt.strftime("%-I:%M %p") if use_override else None,
        "sync_failed": False,
        "error": None,
    }


def read_current_only(config: dict):
    """Fallback when compute_and_apply() fails: read back whatever the
    alarm is currently set to, without changing anything, so there's still
    something concrete to report instead of silence."""
    mac, token = _device_and_token(config)
    alarms = alarm_api.fetch_alarms(token, mac)
    alarm = _find_alarm(alarms, config)
    wake_dt = alarm_api.effective_wake_datetime(alarm)
    return wake_dt.strftime("%-I:%M %p") if wake_dt else None


def read_current_wake_time(config: dict, use_cache: bool = True):
    """The live alarm's time of day, or None if it's switched off or
    unparseable — for callers that act on what the Hatch will really do
    (refresh_breakfast_anchor.py), not just report it. Only the time is
    meaningful: a recurring alarm's stored date is whenever it was last edited.

    Served from the shared short-lived cache when one is fresh (see ALARM_CACHE), so a fan-out
    of followers costs one Hatch call rather than one each."""
    if use_cache:
        cached, hit = alarm_cache.fresh()
        if hit:
            return cached
    mac, token = _device_and_token(config)
    alarm = _find_alarm(alarm_api.fetch_alarms(token, mac), config)
    if not alarm.get("enabled"):
        alarm_cache.note(None)
        return None
    wake_dt = alarm_api.effective_wake_datetime(alarm)
    wake = wake_dt.time() if wake_dt else None
    alarm_cache.note(wake)
    return wake


def build_result(config: dict, tz) -> dict:
    """The full fail-safe sync: compute + apply the wake time, and check for
    back-to-back conflicts, never raising — always returns a result dict.
    Shared by main() (CLI) and any other caller (e.g. a script that also
    wants to notify Telegram directly) so this fail-safe logic exists once."""
    # Fetch once, shared by both the wake-time calc (needs Hatch device
    # access) and the conflict check (doesn't) — a Hatch-side failure
    # shouldn't suppress the conflict check, and vice versa.
    try:
        events = fetch_tomorrow_events()
        events_error = None
    except Exception as e:
        events = []
        events_error = str(e)[:200]

    try:
        conflicts = find_back_to_back_conflicts(events, tz, config) if events else []
    except Exception:
        conflicts = []  # non-critical — never let this take down the wake-time sync

    try:
        if events_error is not None:
            raise RuntimeError(events_error)
        result = compute_and_apply(config, tz, events)
    except Exception as e:
        error_msg = str(e)[:200]
        try:
            wake_time_fmt = read_current_only(config)
        except Exception:
            wake_time_fmt = None
        result = {
            "wake_dt_iso": None,
            "wake_time_fmt": wake_time_fmt,
            "is_default": None,
            "away": None,
            "flight_tomorrow": flight_tomorrow(events) if events else None,
            "default_wake_time_fmt": config.get("default_wake_time"),
            "event_title": None,
            "leave_time_fmt": None,
            "sync_failed": True,
            "error": error_msg,
        }

    result["conflicts"] = conflicts
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    config = load_config()
    tz = get_tz()
    result = build_result(config, tz)

    if args.json:
        print(json.dumps(result))
    elif result["sync_failed"]:
        known = result["wake_time_fmt"] or "unknown — could not read the device"
        print(f"Alarm sync failed ({result['error']}). Currently set to: {known}.")
    elif result.get("away"):
        if result["is_default"]:
            print(f"Away — Hatch OFF. Set your phone alarm for {result['wake_time_fmt']} (default, no early event).")
        else:
            print(
                f"Away — Hatch OFF. Set your phone alarm for {result['wake_time_fmt']} — "
                f"'{result['event_title']}' (actually {result['event_start_fmt']}) "
                f"needs you leaving by {result['leave_time_fmt']}."
            )
    elif result["is_default"]:
        print(f"Alarm set for default {result['wake_time_fmt']} tomorrow — no early event.")
    else:
        backup = " Set a phone alarm too — travel day." if result.get("flight_tomorrow") else ""
        print(
            f"Alarm set for {result['wake_time_fmt']} tomorrow — "
            f"'{result['event_title']}' (actually {result['event_start_fmt']}) "
            f"needs you leaving by {result['leave_time_fmt']}.{backup}"
        )
    for c in result["conflicts"]:
        print(
            f"CONFLICT: '{c['from_event']}' ends {c['from_end_fmt']}, but "
            f"'{c['to_event']}' needs you leaving by {c['required_leave_fmt']} "
            f"(starts {c['to_start_fmt']}) — {c['shortfall_minutes']} min short."
        )


if __name__ == "__main__":
    main()
