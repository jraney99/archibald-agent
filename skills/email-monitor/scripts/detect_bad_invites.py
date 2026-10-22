#!/usr/bin/env python3
"""
Fixes up externally-organized calendar invites (Calendly bookings, invites
import os
from other people) to match the user's calendar conventions:

1. Missing reminder override (relying on Google's useDefault) — fixed
   immediately and silently on every run. This is a mechanical, unambiguous
   rule ("popup:15m on every event, never useDefault") so it doesn't need
   confirmation.
2. Title doesn't follow the "{Activity} <> the user @ {Venue}" convention —
   proposed via a Telegram confirm/skip prompt, since picking the right
   activity/venue names requires judgment a wrong guess would be annoying
   to have applied silently.

Both only touch the user's own copy of the event — Google Calendar lets a guest
personalize their own view (title, reminders) without organizer permission,
so the organizer's and other guests' copies are untouched.

Run: python3 detect_bad_invites.py
"""
import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import anthropic

sys.path.insert(0, os.path.expanduser("~/.openclaw/workspace/Skills/shared"))
from send_inline_proposal import send as send_proposal  # noqa: E402
from usage_logger import log_usage  # noqa: E402

GOG       = "/opt/homebrew/bin/gog"
ACCOUNT   = "owner@example.com"
PENDING   = Path(os.path.expanduser("~/.openclaw/pending_cal_rename.json")).expanduser()
SEEN_FILE = Path(os.path.expanduser("~/.openclaw/cal_rename_seen.json")).expanduser()

FORMAT_PROMPT = """You are formatting a Google Calendar event title to match a personal convention.

Convention: "{{Activity or Person}} <> the user @ {{Venue Name}}"
- No brackets of any kind.
- Venue name only (no address, city, state, zip) - the full address is stored separately.
- The user always appears second, preceded by "<>".
- Keep it short and natural - e.g. "Haircut <> the user @ Main Street Barbers", "Dinner <> the user @ The Bistro".
- Phone calls: use "@ Call" as the venue - e.g. "Intro Call <> the user @ Call".
- Video meetings: use the platform as the venue - e.g. "Intro Call <> the user @ Teams", "Standup <> the user @ Zoom", "Interview <> the user @ Google Meet".
- For calls/video meetings, infer the activity from context (who it's with, subject line) even without a physical venue - a Teams/Zoom/Meet link or dial-in info in the description is enough to use the platform name as venue. Only use "low" confidence if you truly can't tell what the meeting is about at all.
- If you cannot confidently infer a venue name from the data given, set confidence to "low".

Given this calendar event, propose the correctly formatted title.

Title: {title}
Description: {description}
Location: {location}

Respond with ONLY JSON: {{"title": "...", "confidence": "high"|"medium"|"low"}}
"""


def load_seen() -> set:
    if SEEN_FILE.exists():
        try:
            return set(json.loads(SEEN_FILE.read_text()).get("ids", []))
        except Exception:
            return set()
    return set()


def save_seen(seen: set) -> None:
    SEEN_FILE.write_text(json.dumps({"ids": sorted(seen)}))


def fetch_events() -> list:
    today = date.today()
    r = subprocess.run(
        [GOG, "cal", "ls", "--account", ACCOUNT,
         "--from", today.isoformat(), "--to", (today + timedelta(days=60)).isoformat(),
         "-j", "--max", "200"],
        capture_output=True, text=True, timeout=30,
    )
    if r.returncode != 0:
        return []
    try:
        return json.loads(r.stdout).get("events", [])
    except Exception:
        return []


def is_external(ev: dict) -> bool:
    """True if this is an event on the user's calendar that he didn't create himself."""
    if "dateTime" not in ev.get("start", {}):
        return False  # skip all-day events (holidays, birthdays, subscribed calendars)
    organizer = ev.get("organizer", {}).get("email", "")
    if not organizer or organizer == ACCOUNT:
        return False
    if "group.v.calendar.google.com" in organizer or "#holiday@" in organizer:
        return False
    attendees = ev.get("attendees", [])
    self_att  = next((a for a in attendees if a.get("self")), None)
    if self_att and self_att.get("responseStatus") not in ("accepted", "needsAction"):
        return False
    return True


def needs_reminder_fix(ev: dict) -> bool:
    return ev.get("reminders", {}).get("useDefault") is True


def needs_accept_fix(ev: dict) -> bool:
    """True if the user's own RSVP is stuck at needsAction on an event that's already
    confirmed. Covers real invites the daemon's ICS parser missed (e.g. Teams/
    Outlook meetings sent as a reply, where the calendar data may not surface as
    a detectable text/calendar part) as well as Google's own auto-created events —
    same fix either way, no need to diagnose which case it is."""
    if ev.get("status") != "confirmed":
        return False
    self_att = next((a for a in ev.get("attendees", []) if a.get("self")), None)
    return bool(self_att) and self_att.get("responseStatus") == "needsAction"


def needs_title_fix(ev: dict) -> bool:
    return "<> the user @" not in ev.get("summary", "")


def fix_reminder(ev: dict) -> bool:
    r = subprocess.run(
        [GOG, "cal", "update", "primary", ev["id"], "--account", ACCOUNT,
         "--reminder", "popup:15m"],
        capture_output=True, text=True, timeout=30,
    )
    return r.returncode == 0


def fix_accept(ev: dict) -> bool:
    r = subprocess.run(
        [GOG, "cal", "respond", "primary", ev["id"], "--account", ACCOUNT,
         "--status", "accepted"],
        capture_output=True, text=True, timeout=30,
    )
    return r.returncode == 0


def propose_title(ev: dict) -> dict:
    client = anthropic.Anthropic()
    prompt = FORMAT_PROMPT.format(
        title=ev.get("summary", ""),
        description=(ev.get("description") or "")[:500],
        location=ev.get("location", ""),
    )
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}],
    )
    log_usage("detect_bad_invites.py:propose_title", "claude-haiku-4-5-20251001", msg.usage, response_id=getattr(msg, "id", None))
    text = msg.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


def main():
    events   = fetch_events()
    external = [e for e in events if is_external(e)]

    # 1. Reminder + RSVP-accept fixes — mechanical and unambiguous, applied
    # immediately to every qualifying event in this scan, no confirmation needed.
    reminders_fixed = []
    accepted        = []
    for ev in external:
        if needs_reminder_fix(ev):
            if fix_reminder(ev):
                reminders_fixed.append(ev.get("summary"))
        if needs_accept_fix(ev):
            if fix_accept(ev):
                accepted.append(ev.get("summary"))

    if PENDING.exists():
        print(json.dumps({"status": "pending_exists", "reminders_fixed": reminders_fixed,
                          "accepted": accepted,
                          "message": "A rename proposal is already awaiting a response."}))
        return

    # 2. Title fix — needs judgment, propose one at a time via confirm/skip.
    seen       = load_seen()
    candidates = [e for e in external if needs_title_fix(e) and e.get("id") not in seen]

    if not candidates:
        print(json.dumps({"status": "clean", "scanned": len(events),
                          "reminders_fixed": reminders_fixed, "accepted": accepted}))
        return

    ev = candidates[0]
    try:
        result = propose_title(ev)
    except Exception as e:
        print(json.dumps({"status": "error", "message": str(e)}))
        return

    new_title = (result.get("title") or "").strip()
    if not new_title or result.get("confidence") == "low":
        seen.add(ev["id"])
        save_seen(seen)
        print(json.dumps({"status": "low_confidence", "event_id": ev["id"],
                          "old_title": ev.get("summary")}))
        return

    when = ev.get("start", {}).get("dateTime", "")[:16].replace("T", " ")
    text = (
        f"Found an invite that doesn't match your calendar format, Master Wayne:\n\n"
        f"\"{ev.get('summary')}\" ({when})\n\n"
        f"Rename to: \"{new_title}\"?"
    )
    msg_id = send_proposal(text, "calrename", "✅ Rename", "❌ Leave it")

    PENDING.write_text(json.dumps({
        "event_id":    ev["id"],
        "calendar_id": "primary",
        "old_title":   ev.get("summary"),
        "new_title":   new_title,
        "source_tg_msg_id": msg_id,
    }))

    print(json.dumps({"status": "proposed", "event_id": ev["id"],
                      "old_title": ev.get("summary"), "new_title": new_title}))


if __name__ == "__main__":
    main()
