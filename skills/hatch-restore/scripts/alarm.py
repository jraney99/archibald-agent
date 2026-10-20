#!/usr/bin/env python3
"""
List and adjust Hatch Restore wake alarms — the alarms configured in the
Hatch app's Alarms screen. Plain REST (see hatch_alarm_api.py), no MQTT
connection needed. Only lists/enables/disables/retimes/reschedules existing
alarms; the Hatch API used here doesn't support creating new ones.
"""
import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, time as dtime
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(SCRIPTS_DIR.parent.parent / "shared"))
from current_location import get_tz
import wake_override
import alarm_cache
from hatch_client import load_device_map, HatchError
import hatch_alarm_api as alarm_api
from hatch_alarm_api import HatchAlarmError, days_of_week_label, effective_wake_datetime

WEEKDAY_ALIASES = {
    "everyday": ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"],
    "every day": ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"],
    "all": ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"],
    "weekdays": ["monday", "tuesday", "wednesday", "thursday", "friday"],
    "weekends": ["saturday", "sunday"],
}


def format_alarm(alarm: dict) -> str:
    wake_dt = effective_wake_datetime(alarm)
    wake = wake_dt.strftime("%Y-%m-%d %-I:%M %p") if wake_dt else "?"
    return (
        f"  [{alarm.get('id')}] {alarm.get('name') or '(unnamed)'} "
        f"— {'ON' if alarm.get('enabled') else 'off'} — wakes {wake} — "
        f"{days_of_week_label(alarm.get('daysOfWeek'))}"
    )


def parse_weekdays(value: str) -> list[str]:
    normalized = value.strip().lower()
    if normalized in WEEKDAY_ALIASES:
        return WEEKDAY_ALIASES[normalized]
    return [d.strip() for d in normalized.split(",") if d.strip()]


def resolve_mac(nickname: str) -> str:
    entry = load_device_map().get(nickname)
    if entry is None:
        raise HatchError(f"Unknown device nickname '{nickname}'. Run discover_devices.py first.")
    return entry["mac"]


ALARM_CONFIG = Path(__file__).resolve().parent.parent / "config" / "alarm_config.json"


def load_alarm_config() -> dict:
    try:
        return json.loads(ALARM_CONFIG.read_text())
    except Exception:  # noqa: BLE001
        return {}


def parse_wake_time(value: str) -> dtime:
    """"09:00", "9:00", "9am", "9:30 pm" -> time. Anything else is refused out loud."""
    m = re.fullmatch(r"\s*(\d{1,2})(?::(\d{2}))?\s*(?:([ap])\.?m?\.?)?\s*", value.strip().lower())
    if m and (m.group(2) or m.group(3)):
        hh, mm, half = int(m.group(1)), int(m.group(2) or 0), m.group(3)
        if half and 1 <= hh <= 12:
            hh = hh % 12 + (12 if half == "p" else 0)
        if 0 <= hh <= 23 and 0 <= mm <= 59 and not (half and int(m.group(1)) > 12):
            return dtime(hh, mm)
    raise HatchAlarmError(f"Can't read the time {value!r}. Use HH:MM (24-hour) or e.g. 9am / 9:30pm.")


def resolve_alarm_id(requested, configured, alarms: list[dict]) -> tuple[str, str]:
    """Which alarm a change applies to -> (id, note). 2026-09-19/20: every alarm change
    took three or four tries because the device and alarm id had to be supplied by hand
    although config/alarm_config.json holds both, and once a stale id was used. An id that
    is absent falls back to the configured one, then to the device's only alarm (the same
    adoption rule sync uses) — and the note says so, so nothing is silent."""
    ids = [str(a.get("id")) for a in alarms]
    for source, candidate in (("", requested), ("configured alarm", configured)):
        if candidate and str(candidate) in ids:
            note = f"(alarm {requested} not found; used the {source} {candidate})" if requested and source else ""
            return str(candidate), note
    if len(ids) == 1:
        wanted = requested or configured
        return ids[0], (f"(alarm {wanted} not found; used the device's only alarm {ids[0]})" if wanted else "")
    raise HatchAlarmError(
        f"No usable alarm id (asked for {requested or 'none'}, config has {configured or 'none'}). "
        f"Alarms on the device: {', '.join(ids) or 'none'}.")


# Everything timed off the wake time. Until 2026-09-20 these were retimed once a night
# (9:15pm lights, 3am breakfast), so a wake time changed after that never reached them: the
# bedroom sunrise came on at 7:00 whatever the alarm said, and the user unplugged the lamp.
FOLLOWERS = (
    ("bedroom sunrise lights", SCRIPTS_DIR.parent.parent / "shared" / "sync_bedroom_sunrise.py"),
    ("ChiliPad morning timing", SCRIPTS_DIR.parent.parent / "chilipad" / "scripts" / "night_schedule.py"),
    ("breakfast reminder", SCRIPTS_DIR / "refresh_breakfast_anchor.py", ("--from-alarm-change",)),
    ("morning briefing", SCRIPTS_DIR / "refresh_morning_briefing.py"),
)


def events_on(day) -> list:
    """That day's timed events, for the calendar guard below."""
    import subprocess as _sp
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "shared"))
    from fetch_calendar import gog_env  # noqa: E402 — needs GOG_KEYRING_PASSWORD
    import sync_alarm  # noqa: E402
    flag = {"today": "--today", "tomorrow": "--tomorrow"}.get(day)
    args = [flag] if flag else ["--from", day.isoformat(), "--to", day.isoformat()]
    r = _sp.run(["gog", "cal", "ls", "--account", sync_alarm.ACCOUNT,
                 "--calendars", sync_alarm.CALENDARS, *args, "-j", "--max", "50"],
                capture_output=True, text=True, timeout=60, env=gog_env())
    if r.returncode != 0:
        raise HatchError(f"could not read the calendar: {r.stderr.strip()[:160]}")
    return json.loads(r.stdout).get("events", [])


def calendar_required_wake(wake: dtime, now: datetime, config: dict, tz):
    """What the calendar needs for the morning this wake time belongs to.

    Returns (required_time, event_title, event_start_dt) or (None, None, None).

    WHY (2026-10-13): the 9pm sync has always applied the rule "the calendar wins
    when it needs him up EARLIER than the time he set — a flight is never slept
    through" (sync_alarm.compute_and_apply). Setting the alarm by hand went
    straight to the device and recorded an override that BEAT the calendar, with
    no check at all. An alarm set for 12:00 PM sat on top of an 11:30 AM golf
    booking made two hours earlier, and because the override was for that same
    morning no sync would run in time to catch it. Same rule, same function,
    applied on the manual path too."""
    import sync_alarm  # noqa: E402
    day = wake_override.wake_date(wake, now)
    required_dt, title, _leave, start_dt = sync_alarm.earliest_requirement(
        events_on(day), tz, config)
    if required_dt is None:
        return None, None, None
    return required_dt.timetz().replace(tzinfo=None), title, start_dt


def follow_wake_change() -> list[str]:
    """Retime what hangs off the wake time, now. One line each; a failure is said out loud."""
    lines = []
    for label, script, *extra in FOLLOWERS:
        try:
            run = subprocess.run([sys.executable, "-W", "ignore", str(script), *(extra[0] if extra else ())],
                                 capture_output=True, text=True, timeout=120)
            out = run.stdout.strip()
            try:
                info = json.loads(out.splitlines()[-1]) if out else {}
            except ValueError:
                info = {}
            if run.returncode != 0 or info.get("status") == "error":
                why = info.get("error") or (out or run.stderr.strip())[-160:] or f"exit {run.returncode}"
                lines.append(f"⚠️ {label} NOT retimed — {why}")
            elif info.get("ramp_start_local"):
                lines.append(f"✓ {label}: start {info['ramp_start_local']}, off {info.get('morning_off_local', '?')}")
            elif info.get("warm_at"):
                lines.append(f"✓ {label}: warm {info['warm_at']}, off {info.get('morning_off_at', '?')}")
            else:
                # Repeat what the follower actually said. A blanket "✓ retimed" on exit 0 was a
                # false claim: on 2026-10-01 an evening alarm change printed "✓ morning briefing
                # retimed" while the refresh had decided to change nothing (and the breakfast
                # refresh is misfire-guarded outside 01:00-06:00, so it usually changes nothing
                # here either). The user reads these lines as confirmation, so they have to be true.
                # Followers report a guarded skip on stderr (misfire-guard), so read that too.
                spoken = out or run.stderr.strip()
                said = spoken.splitlines()[-1].strip() if spoken else ""
                lines.append(f"· {label}: {said}" if said else f"✓ {label} retimed")
        except Exception as e:  # noqa: BLE001
            lines.append(f"⚠️ {label} NOT retimed — {str(e)[:160]}")
    return lines


def main() -> None:
    config = load_alarm_config()
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", default=config.get("device"),
                        help="device nickname (default: config/alarm_config.json)")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--alarm-id", help="default: config/alarm_config.json")
    parser.add_argument("--enable", action="store_true")
    parser.add_argument("--disable", action="store_true")
    parser.add_argument("--wake-time", "--time", dest="wake_time", help="HH:MM 24-hour, or 9am / 9:30pm")
    parser.add_argument("--weekdays", help="comma-separated weekday names, or 'weekdays'/'weekends'/'everyday'")
    parser.add_argument("--ignore-calendar", action="store_true",
                        help="set the time even if the calendar needs him up earlier "
                             "(use after the clashing commitment is actually cancelled)")
    parser.add_argument("--no-follow", action="store_true",
                        help="change the Hatch only: do not record the time or retime the lights/breakfast reminder")
    args = parser.parse_args()
    if not args.device:
        raise HatchError("No --device given and config/alarm_config.json names none.")
    wake = parse_wake_time(args.wake_time) if args.wake_time else None  # refuse a bad time before any network call

    # Before touching the device: a wake time later than the calendar allows is
    # refused outright. Clamping silently would be worse — the user asked for a time
    # and must be told he is not getting it, and why.
    if wake is not None and not args.ignore_calendar:
        try:
            required, title, start_dt = calendar_required_wake(
                wake, datetime.now(get_tz()), config, get_tz())
        except Exception as e:  # noqa: BLE001 — an unreadable calendar must not block an alarm
            print(f"(could not check the calendar: {str(e)[:120]})", file=sys.stderr)
            required = None
        if required is not None and wake > required:
            raise HatchError(
                f"{wake.strftime('%-I:%M %p')} is too late: "
                f"{title or 'a commitment'}"
                + (f" starts {start_dt.strftime('%-I:%M %p')}" if start_dt else "")
                + f", so you need to be up by {required.strftime('%-I:%M %p')}. "
                f"Nothing was changed. Set {required.strftime('%-I:%M %p')} or earlier, "
                f"or pass --ignore-calendar if that commitment is cancelled."
            )

    mac = resolve_mac(args.device)
    token = alarm_api.login()
    alarms = alarm_api.fetch_alarms(token, mac)

    changing = args.enable or args.disable or wake is not None or args.weekdays
    if args.list or not changing:
        print(f"Alarms for {args.device}:")
        for a in alarms:
            print(format_alarm(a))
        return

    args.alarm_id, note = resolve_alarm_id(args.alarm_id, config.get("alarm_id"), alarms)
    alarm = next(a for a in alarms if str(a.get("id")) == args.alarm_id)

    updated = None
    if args.enable:
        updated = alarm_api.set_alarm_enabled(token, mac, alarm, True)
    if args.disable:
        updated = alarm_api.set_alarm_enabled(token, mac, alarm, False)
    if wake is not None:
        alarm = next((a for a in (updated or alarms) if str(a.get("id")) == args.alarm_id), alarm)
        updated = alarm_api.set_alarm_wake_time(token, mac, alarm, wake)
    if args.weekdays:
        alarm = next((a for a in (updated or alarms) if str(a.get("id")) == args.alarm_id), alarm)
        updated = alarm_api.set_alarm_weekdays(token, mac, alarm, parse_weekdays(args.weekdays))

    result = next((a for a in (updated or []) if str(a.get("id")) == args.alarm_id), None)
    print(format_alarm(result) if result else f"Alarm {args.alarm_id} updated.")
    if note:
        print(note)
    # Switching the alarm ON or OFF moves the wake time as much as retiming it does: everything
    # downstream reads "alarm off" as no wake time at all (the ChiliPad falls back to its 10:00
    # backstop, the sunrise does not run). 2026-10-08: The user asked for his alarm back on, it came
    # on at 7:30, and nothing else moved, because only a --time change ran the followers.
    toggled = (args.enable or args.disable) and not args.no_follow
    if wake is not None and not args.no_follow:
        # Hand the followers the time we just wrote: they each read the alarm back, and the
        # Hatch rate-limits (2026-10-06: six calls in a row and the briefing follower got 429,
        # leaving tomorrow's briefing an hour early). See alarm_cache.py.
        # Hand them the whole device-side plan, not just the time: read_plan()
        # (the bedroom sunrise AND the ChiliPad night schedule both go through it)
        # needs `enabled` and the Hatch's own sunrise length too, so a time-only
        # note still sent the first follower back to the device. With the budget
        # already spent setting the alarm, that read 429'd and BOTH followers were
        # left at the old timing (2026-10-13, after a second change the same
        # evening). Everything below is already in hand here — no extra call.
        plan_alarm = result or alarm
        try:
            alarm_cache.note_plan({
                "enabled": bool(plan_alarm.get("enabled")),
                "wake_dt": effective_wake_datetime(plan_alarm),
                "hatch_duration": alarm_api._alarm_sunrise_duration_seconds(plan_alarm) or 0,
            })
        except Exception:  # noqa: BLE001 — a cache miss costs one call; a crash costs the alarm
            alarm_cache.note(wake)
        now = datetime.now(get_tz())
        entry = wake_override.record(wake, now)
        if entry["for_date"] == now.date().isoformat():
            # A change for THIS morning is not a wish for tomorrow: make it the yardstick, so
            # tonight's sync does not read the device's 9:30 as "he set 9:30 for tomorrow".
            wake_override.remember_baseline(wake, "alarm.py (this morning)", now)
        print(f"Kept for the morning of {entry['for_date']} — tonight's 9pm sync will not reset it "
              f"(an earlier calendar commitment still wins).")
        for line in follow_wake_change():
            print(line)
    elif toggled:
        # No new time to record or cache — the followers read the device themselves and will
        # see the alarm's new enabled state.
        alarm_cache.clear()
        for line in follow_wake_change():
            print(line)


if __name__ == "__main__":
    try:
        main()
    except (HatchError, HatchAlarmError) as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
