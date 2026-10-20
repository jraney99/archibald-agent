#!/usr/bin/env python3
"""
Fetch calendar events across ALL of the user's calendars (Personal + Work + Side project)
for an arbitrary date range, formatted in his current local time zone.

This exists because a bare `gog calendar` lookup only reads the primary
(Personal) calendar and silently omits owner@work.example — so ad-hoc schedule
questions were missing the entire work day. Always read the calendar through
this wrapper, never raw `gog cal ls`, unless you deliberately want one calendar.

Usage:
  python3 fetch_calendar.py                 # today
  python3 fetch_calendar.py --days 7        # today .. today+7
  python3 fetch_calendar.py --from 2026-09-08 --to 2026-09-12
  python3 fetch_calendar.py --query "dentist" --days 60
  python3 fetch_calendar.py --json          # raw structured output
"""
import argparse
import os
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from current_location import get_tz
from output_mode import Report  # noqa: E402

ACCOUNT = "owner@example.com"
# Canonical calendar scope. Other readers import this list rather than copying it.
CALENDARS = [
    "owner@example.com",  # Personal
    "owner@work.example",     # Work
    "contact@example.org",   # Side project
]



def gog_env():
    """gog's file keyring (2026-10-02) needs GOG_KEYRING_PASSWORD. Gateway-launched jobs inherit it
    from openclaw.json env.vars; a plain shell or a test does not, so read it the same way the other
    scripts read their secrets. Never printed."""
    env = dict(os.environ)
    if not env.get("GOG_KEYRING_PASSWORD"):
        try:
            from runtime_secrets import get_secret
            value = get_secret("GOG_KEYRING_PASSWORD")
            if value:
                env["GOG_KEYRING_PASSWORD"] = value
        except Exception:
            pass
    return env

def classify_error(err: str) -> str:
    if "invalid_grant" in err or "Token has been expired" in err or "revoked" in err:
        return "⚠️ CALENDAR UNAVAILABLE — gog token expired or revoked. Run: gog auth login"
    return f"⚠️ CALENDAR UNAVAILABLE — gog error: {err[:160]}"


def fmt_event(ev: dict, tz) -> dict:
    start, end = ev.get("start", {}), ev.get("end", {})
    title = ev.get("summary", "(untitled)")
    cal = ev.get("calendarId") or ev.get("organizer", {}).get("email", "")
    self_attendee = next((a for a in ev.get("attendees", []) if a.get("self")), {})
    # Who else is on the invite. Emails are a stabler identity than names parsed
    # out of a title (wardrobe event_triage.py keys people on them).
    people = {
        "organizer_email": (ev.get("organizer") or {}).get("email", ""),
        "organizer_self": bool((ev.get("organizer") or {}).get("self")),
        "attendees": [{"email": a.get("email", ""), "name": a.get("displayName", "")}
                      for a in ev.get("attendees", []) if not a.get("self") and not a.get("resource")],
    }
    if "dateTime" in start:
        s = datetime.fromisoformat(start["dateTime"]).astimezone(tz)
        e = datetime.fromisoformat(end["dateTime"]).astimezone(tz) if end.get("dateTime") else None
        return {
            "event_id": ev.get("id", ""),
            "title": title, "calendar": cal, "all_day": False,
            "day": s.strftime("%Y-%m-%d"),
            "start_fmt": s.strftime("%a %b %-d, %-I:%M %p"),
            "end_fmt": e.strftime("%-I:%M %p") if e else "",
            "start_iso": start["dateTime"], "end_iso": end.get("dateTime", ""),
            "location": ev.get("location", ""),
            "description": ev.get("description", ""),
            "status": ev.get("status", "confirmed"),
            "response_status": self_attendee.get("responseStatus", ""),
            **people,
        }
    return {
        "event_id": ev.get("id", ""),
        "title": title, "calendar": cal, "all_day": True,
        "day": start.get("date", ""),
        "start_fmt": f"{start.get('date', '')} (all day)",
        "end_fmt": "", "start_iso": start.get("date", ""), "end_iso": end.get("date", ""),
        "location": ev.get("location", ""),
        "description": ev.get("description", ""),
        "status": ev.get("status", "confirmed"),
        "response_status": self_attendee.get("responseStatus", ""),
        **people,
    }


def gog_command(start, end, query=None) -> list:
    """The gog read for [start, end], both inclusive.

    gog's date-only --to already covers that whole day (checked 2026-09-20:
    `--from 2026-09-19 --to 2026-09-19` returns Saturday's events). This used
    to add a day on top, so every read came back one day long — a read for
    09-18..09-20 also returned Monday 09-21."""
    cmd = [
        "/opt/homebrew/bin/gog", "cal", "ls", "--account", ACCOUNT,
        "--calendars", ",".join(CALENDARS),
        "--from", start.isoformat(),
        "--to", end.isoformat(),
        "-j", "--max", "250",
    ]
    if query:
        cmd += ["--query", query]
    return cmd


def read_snapshot(start, end, query=None, *, tz=None, force=False, store=None):
    """Raw events plus health/freshness. Never infer emptiness from missing JSON."""
    from source_snapshot import SnapshotStore
    tz = tz or get_tz()
    scope = {"account": ACCOUNT, "calendars": sorted(CALENDARS),
             "from": start.isoformat(), "to": end.isoformat(),
             "query": query, "timezone": str(tz)}
    def fetch():
        response = subprocess.run(gog_command(start, end, query), capture_output=True,
                                  text=True, timeout=45, env=gog_env())
        if response.returncode:
            return {"status": "unavailable", "value": None, "error": "calendar_read_failed"}
        payload = json.loads(response.stdout)
        events = payload.get("events")
        if not isinstance(events, list):
            raise ValueError("Missing event list")
        # Until every page is retrieved, this cannot establish free time.
        partial = bool(payload.get("nextPageToken") or payload.get("next_page_token")
                       or payload.get("hasMore") or len(events) >= 250)
        return {"status": "partial" if partial else "complete", "value": events,
                "error": "calendar_truncated" if partial else None}
    return (store or SnapshotStore()).read("calendar", scope, fetch, max_age=60, force=force)


def render(events: list, start, end, tz_abbrev: str) -> str:
    """Output Mode (Documentation/rules/33-output-mode.md). One section per day so
    a week reads as a week; --json is untouched and stays the machine surface."""
    label = start.isoformat() if start == end else f"{start.isoformat()} → {end.isoformat()}"
    days = len(set(ev["start_fmt"].split(",")[0] for ev in events)) if events else 0
    r = Report(
        "📅", "Calendar",
        headline=f"{len(events)} event{'' if len(events) == 1 else 's'}",
        tldr=("Nothing on the calendar in this window."
              if not events else
              f"{len(events)} event{'' if len(events) == 1 else 's'} across {days} day{'' if days == 1 else 's'}."),
        coverage=f"{label} · all calendars · times in {tz_abbrev}",
        needs_you_required=False,   # a lookup the agent runs mid-task, not a delivered report
    )
    by_day: dict[str, list] = {}
    for ev in events:
        by_day.setdefault(ev["start_fmt"].split(",")[0].strip() if "," in ev["start_fmt"] else label, []).append(ev)
    for day, items in by_day.items():
        section = r.section("🗓", day, headline=f"{len(items)} event{'' if len(items) == 1 else 's'}")
        for ev in items:
            when = ev["start_fmt"].split(",", 1)[-1].strip() if "," in ev["start_fmt"] else ev["start_fmt"]
            when += f"–{ev['end_fmt']}" if ev["end_fmt"] else ""
            section.row(when, ev["title"], context=ev["calendar"].split("@")[0])
    return r.render()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="frm", help="start date YYYY-MM-DD (default: today)")
    ap.add_argument("--to", dest="to", help="end date YYYY-MM-DD (inclusive)")
    ap.add_argument("--days", type=int, help="number of days from start (alternative to --to)")
    ap.add_argument("--query", help="text filter passed to gog")
    ap.add_argument("--json", action="store_true", help="emit raw JSON instead of text")
    ap.add_argument("--fresh", action="store_true", help="Force provider revalidation before planning an action")
    args = ap.parse_args()

    tz = get_tz()
    today = datetime.now(tz).date()
    start = datetime.fromisoformat(args.frm).date() if args.frm else today
    if args.to:
        end = datetime.fromisoformat(args.to).date()
    elif args.days is not None:
        end = start + timedelta(days=args.days)
    else:
        end = start
    snapshot = read_snapshot(start, end, args.query, tz=tz, force=args.fresh)
    if snapshot["status"] != "complete":
        msg = "⚠️ CALENDAR UNAVAILABLE OR INCOMPLETE — availability cannot be confirmed."
        if args.json:
            print(json.dumps({"calendar_unavailable": True, "error": msg, "events": [],
                              "source": {k: v for k, v in snapshot.items() if k != "value"}}))
        else:
            print(msg)
        sys.exit(1)
    raw = snapshot["value"]

    events = sorted((fmt_event(ev, tz) for ev in raw), key=lambda x: x["start_iso"])
    tz_abbrev = datetime.now(tz).strftime("%Z")

    if args.json:
        print(json.dumps({
            "calendar_unavailable": False, "timezone": str(tz),
            "tz_abbrev": tz_abbrev, "range": [start.isoformat(), end.isoformat()],
            "events": events,
            "source": {k: v for k, v in snapshot.items() if k != "value"},
        }, indent=2))
        return

    print(render(events, start, end, tz_abbrev))


if __name__ == "__main__":
    main()
