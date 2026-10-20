#!/usr/bin/env python3
"""
Scan upcoming calendar events for Zoom meetings and schedule one-shot Govee
console-light cron jobs: on a few minutes before each meeting starts (as a
backlight/rim accent for video calls), off when it ends.

Run on a recurring schedule (see govee-lights SKILL.md) rather than once —
idempotent via `zoom_lighting_state.json` (seen event IDs) so re-running
doesn't create duplicate cron jobs for the same event.

Detection: an event counts as a Zoom call if "zoom.us" appears in its
description or location — that's where gog surfaces the Zoom join link,
confirmed against real events in the user's calendar. Non-Zoom video calls
(Meet, etc.) are intentionally not matched.
"""
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

STATE_FILE = Path(__file__).parent / "zoom_lighting_state.json"
from fetch_calendar import ACCOUNT, CALENDARS, read_snapshot
CONTROL_PY = "~/.openclaw/workspace/Skills/govee-lights/scripts/control.py"
# When a call ends, restore the console to whatever the daytime schedule
# wants at that moment (afternoon hold, focus scene, evening gold, or off)
# instead of a hard power-off — see console_schedule.py.
CONSOLE_SCHEDULE_PY = "~/.openclaw/workspace/Skills/govee-lights/scripts/console_schedule.py"
LIGHT = "console_light"
LEAD_MINUTES = 3       # turn the light on this many minutes before the call starts
BRIGHTNESS = "20"      # low — the strip head is partly in the user's camera frame,
                       # so it should read as an ambient glow, not a light
                       # source; 30% bloomed / pulled webcam auto-exposure
COLOR_TEMP = "3500"    # soft warm-neutral white, close to typical room light
                       # so it doesn't grey out skin tone on camera
TELEGRAM_TO = "1000000001"
OPENCLAW = "/opt/homebrew/bin/openclaw"


def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {"scheduled_event_ids": []}


def _save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


def _fetch_events() -> list:
    from current_location import get_tz
    from source_snapshot import require_complete
    tz = get_tz()
    today = datetime.now(tz).date()
    return require_complete(read_snapshot(today, today + timedelta(days=2), tz=tz))


def _is_zoom(ev: dict) -> bool:
    text = (ev.get("description", "") + " " + ev.get("location", "")).lower()
    return "zoom.us" in text


def _add_job(name: str, at_dt: datetime, argv: list) -> None:
    cmd = [
        OPENCLAW, "cron", "add", name,
        "--at", at_dt.isoformat(),
        "--command-argv", json.dumps(argv),
        "--session", "isolated",
        "--channel", "telegram", "--to", TELEGRAM_TO,
        "--best-effort-deliver", "--no-deliver",
        "--delete-after-run",
        "--json",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError(f"cron add failed for {name}: {r.stderr.strip() or r.stdout.strip()}")


def main():
    state = _load_state()
    seen = set(state.get("scheduled_event_ids", []))
    events = _fetch_events()
    now = datetime.now().astimezone()
    newly_scheduled = []

    for ev in events:
        eid = ev.get("id")
        if not eid or eid in seen or not _is_zoom(ev):
            continue
        start = ev.get("start", {}).get("dateTime")
        end = ev.get("end", {}).get("dateTime")
        if not start or not end:
            continue
        start_dt = datetime.fromisoformat(start)
        end_dt = datetime.fromisoformat(end)
        if start_dt < now:
            continue

        # Clamp the on-time forward if the meeting is closer than the lead
        # time (e.g. added to the calendar with only 1 minute's notice) so
        # --at never receives a past timestamp.
        on_dt = max(start_dt - timedelta(minutes=LEAD_MINUTES), now + timedelta(seconds=30))

        _add_job(
            f"zoom-light-on-{eid}", on_dt,
            ["python3", CONTROL_PY, "--light", LIGHT, "--on",
             "--brightness", BRIGHTNESS, "--temp", COLOR_TEMP, "--skip-if-away"],
        )
        _add_job(
            f"zoom-light-off-{eid}", end_dt,
            ["python3", CONSOLE_SCHEDULE_PY, "--skip-if-away"],
        )
        newly_scheduled.append(eid)
        print(f"scheduled {eid}: on {on_dt.isoformat()} / off {end_dt.isoformat()}")

    if newly_scheduled:
        merged = list(seen | set(newly_scheduled))
        state["scheduled_event_ids"] = merged[-200:]  # keep the file bounded
        _save_state(state)


if __name__ == "__main__":
    main()
