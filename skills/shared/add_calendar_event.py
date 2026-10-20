#!/usr/bin/env python3
"""
Create a calendar event in the user's standard "{who} <> the user @ {venue}" format
without hand-constructing gog CLI flags.

Why this exists: the agent has repeatedly guessed nonexistent gog flags
(--start, --title, --date, --format instead of --from, --to, --summary),
burning several tool calls rediscovering the real syntax from --help on
nearly every calendar add (see RULES.md "gog Calendar CLI — Exact Flags" and
LRN-20260714-001 in LEARNINGS.md). This script is the single correct-by-
construction entry point for ad-hoc calendar adds during chat — use it
instead of calling `gog calendar create` directly.

Usage:
    python3 add_calendar_event.py \\
        --who "Alex Example" --venue "Example Cafe" \\
        --from "2026-07-15T10:30:00" --to "2026-07-15T12:00:00" \\
        --tz "America/Los_Angeles" \\
        --location "100 Example St, Springfield, CA 90000"

Builds the title automatically, always passes --start-timezone/
--end-timezone and an explicit --reminder (never useDefault), and verifies
the event is retrievable before reporting success (a clean gog exit code
alone is not proof the write stuck — see feedback-cal-duplicate-cleanup-bug
in memory).

Prints the created event as JSON on success (including hangoutLink/
conferenceData when --meet is passed). Exits 1 with a message on stderr on
any failure (including failed verification) — nothing is ever reported as
added without a successful read-back.

Video calls: pass --meet to attach a Google Meet link. gog generates it
server-side via --with-meet — never ask the user for a Meet URL, and never tell
him one is required before the event can be created.
"""
import argparse
import json
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_calendar import gog_env  # noqa: E402

GOG   = "/opt/homebrew/bin/gog"
EMAIL = "owner@example.com"


def _run(args: list) -> subprocess.CompletedProcess:
    # gog's file keyring needs GOG_KEYRING_PASSWORD (2026-10-02). A gateway-launched job
    # inherits it from openclaw.json env.vars, a plain shell does not, and the failure
    # reads as "no TTY available for keyring file backend password prompt" rather than
    # as a missing secret. 2026-10-06: a restaurant booking went through fine and then
    # reported "calendar add FAILED" for exactly this reason. fetch_calendar owns the
    # lookup; this borrows it rather than keeping a second copy.
    return subprocess.run([GOG, *args], capture_output=True, text=True, timeout=30,
                          env=gog_env())


def normalize_datetime(value: str) -> str:
    """Return a full RFC3339 local time (seconds included, any offset kept).

    gog passes --from/--to to Google verbatim, and Google 400s on a time
    without seconds ("2026-09-22T12:59") — an opaque "Bad Request" that sent
    Archibald off hand-building raw gog commands (2026-09-21, an airport pickup)."""
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        raise RuntimeError(f"Not a valid date-time: {value!r} (use e.g. 2026-09-22T14:05:00)")
    return parsed.isoformat(timespec="seconds")


def normalize_date(value: str) -> str:
    """Return a date-only YYYY-MM-DD for an all-day event.

    Rejects a value carrying a time. An all-day event whose bounds silently
    acquired 00:00:00 is what produced the 2026-10-09 golf trip: a timed
    00:00→00:00 event that Google rendered on the first day only."""
    text = str(value).strip()
    if "T" in text or " " in text:
        raise RuntimeError(f"An all-day event needs a plain date, not a time: {value!r} (use e.g. 2026-10-22)")
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        raise RuntimeError(f"Not a valid date: {value!r} (use e.g. 2026-10-22)")


def exclusive_end(last_day: str) -> str:
    """Google's all-day `end.date` is EXCLUSIVE, so a trip through Sunday ends
    on Monday. Callers pass the LAST DAY THEY MEAN and this adds the day.

    Why this lives here rather than in the caller (2026-10-09): asked for an
    all-day block "Thu Oct 22 to Sun Oct 25", the agent passed end 2026-10-25
    and Sunday fell off the event. Every caller would have to remember the
    same off-by-one, so the module that exists to be correct-by-construction
    owns it. See https://developers.google.com/workspace/calendar/api/concepts/events-calendars
    """
    return (date.fromisoformat(normalize_date(last_day)) + timedelta(days=1)).isoformat()


TRANSPARENCY = {"busy": "busy", "free": "free", "opaque": "busy", "transparent": "free"}


def last_inclusive(exclusive: str) -> str:
    """The last day a viewer actually sees, for showing the user a span he can check."""
    return (date.fromisoformat(exclusive) - timedelta(days=1)).isoformat()


def create_event(
    who: str,
    venue: str,
    start: str,
    end: str,
    tz: str,
    location: str,
    description: str = "",
    attendees: str = "",
    reminder_minutes: int = None,
    extra_reminders=None,   # list[int] | None — written bare: this module has no
                            # `from __future__ import annotations` and runs on 3.9,
                            # where `list | None` raises at import time and would have
                            # taken every calendar-creating skill down with it.
    calendar_id: str = "primary",
    title: str = "",
    meet: bool = False,
    origin_address: str = "",
    golf: bool = False,
    all_day: bool = False,
    transparency: str = "",
) -> dict:
    """Create the event and verify it stuck. Raises RuntimeError on failure.

    all_day: `start`/`end` are plain dates and `end` is the LAST DAY INCLUDED
    (this converts it to Google's exclusive end). transparency: "busy" or
    "free" — left empty, Google decides, and its default for an all-day event
    is FREE, which is right for a trip block someone can still take calls
    during and wrong for anything that must block the day."""
    summary = title or f"{who} <> the user @ {venue}"
    reminder_minutes = resolve_reminder(location, reminder_minutes, origin_address, golf)

    if all_day:
        start, end = normalize_date(start), exclusive_end(end)

    args = [
        "calendar", "create", "--account", EMAIL, calendar_id,
        "--summary", summary,
        "--from", start,
        "--to", end,
        # gog refuses --start-timezone alongside date-only bounds ("cannot be used
        # with all-day dates"), and Google ignores a timezone on an all-day event
        # anyway: it occupies the whole day in whatever zone the viewer is in.
        *([] if all_day else ["--start-timezone", tz, "--end-timezone", tz]),
        "--location", location,
        "--reminder", f"popup:{reminder_minutes}m",
        # EXTRA REMINDERS ARE OPT-IN (2026-10-09): default empty, so every existing
        # caller emits exactly the one flag it always did. gog accepts up to 5.
        *[f"--reminder=popup:{int(m)}m" for m in (extra_reminders or ())
          if int(m) != int(reminder_minutes)],
        "-j",
    ]
    if all_day:
        args.append("--all-day")
    if transparency:
        try:
            args += ["--transparency", TRANSPARENCY[transparency.strip().lower()]]
        except KeyError:
            raise RuntimeError(f"transparency must be busy or free, not {transparency!r}")
    if description:
        args += ["--description", description]
    if attendees:
        args += ["--attendees", attendees]
    if meet:
        args += ["--with-meet"]

    r = _run(args)
    if r.returncode != 0:
        raise RuntimeError(f"gog calendar create failed: {r.stderr.strip() or r.stdout.strip()}")

    try:
        event_id = json.loads(r.stdout)["event"]["id"]
    except Exception as e:
        raise RuntimeError(f"could not parse event id from gog output: {e}\n{r.stdout}")

    verify = _run(["calendar", "event", "--account", EMAIL, calendar_id, event_id, "-j"])
    if verify.returncode != 0:
        raise RuntimeError(f"event {event_id} created but not retrievable: {verify.stderr.strip()}")

    created = json.loads(verify.stdout)["event"]
    if all_day:
        # Prove it is date-only AND spans the days asked for. A timed event that
        # merely starts at midnight reads as all-day in a list view and is not.
        got_start = (created.get("start") or {}).get("date")
        got_end = (created.get("end") or {}).get("date")
        if not got_start or not got_end:
            raise RuntimeError(
                f"event {event_id} was asked to be all-day but came back timed "
                f"(start={created.get('start')}, end={created.get('end')})")
        if (got_start, got_end) != (start, end):
            raise RuntimeError(
                f"event {event_id} all-day span is {got_start}..{got_end}, expected {start}..{end}")
    return created


def resolve_reminder(location, minutes=None, origin_address="", golf=False):
    if minutes is not None:
        if not 0 <= minutes <= 40320:
            raise RuntimeError("Reminder must be between 0 and 40320 minutes")
        return minutes
    if location.strip().lower() in {"call", "zoom", "google meet", "meet", "teams", "online"}:
        return 15
    if not origin_address:
        raise RuntimeError("Physical event needs --origin-address (preceding same-day event or verified home), or an explicitly computed --reminder-minutes")
    from drive_time import leave_by_minutes
    minutes = leave_by_minutes(location, origin_address=origin_address, arrival_buffer=45 if golf else 5)
    if minutes is None:
        raise RuntimeError("Could not verify driving time. Confirm the addresses before creating the event")
    return minutes


def propose_event(*, who, venue, start, end, tz, location, description="", attendees="",
                  reminder_minutes=None, extra_reminders=None, title="", origin_address="",
                  golf=False, all_day=False, transparency=""):
    """Send the draft to Telegram with a real, bound approval button, and return its
    message id. Nothing reaches the calendar until the user approves.

    Calendar writes are Tier (b) (SECURITY.md → Confirmation Protocol): show the draft,
    wait for a literal ✅. Until 2026-09-20 there was no way to DO that from a
    conversation — approvals were only ever issued by the email daemon — so Archibald
    typed its own "✅ to confirm" under a plain chat message. That looks identical to a
    real proposal and cannot work: a bare ✅ has nothing to bind to, and the reply was
    "Reply with ✅ to the specific proposal, or tap its approval button" with no button
    anywhere (2026-09-20 20:21, a friend's housewarming). This writes the same
    pending_cal_event.json the daemon writes and hands it to show_pending_proposal.py,
    which issues the approval token and the buttons."""
    summary = title or f"{who} <> the user @ {venue}"
    minutes = resolve_reminder(location, reminder_minutes, origin_address, golf)
    if all_day:
        start, end = normalize_date(start), exclusive_end(end)
    spec = {
        "summary": summary, "from": start, "to": end,
        "start_timezone": tz, "end_timezone": tz,
        "location": location, "description": description,
        "reminder_minutes": minutes,
    }
    if all_day:
        spec["all_day"] = True
    if transparency:
        try:
            spec["transparency"] = TRANSPARENCY[transparency.strip().lower()]
        except KeyError:
            raise RuntimeError(f"transparency must be busy or free, not {transparency!r}")
    if attendees:
        spec["attendees"] = attendees
    when = (f"all day {start} → {last_inclusive(end)} ({tz})" if all_day
            else f"{start} → {end} ({tz})")
    lines = [summary, "", f"🕒 {when}", f"📍 {location}", f"🔔 {minutes} min before"]
    if transparency:
        lines.append(f"🗓 shows as {spec['transparency']}")
    if attendees:
        lines.append(f"👥 {attendees}")
    if description:
        lines.append(f"📝 {description}")
    payload = {"proposal_text": "\n".join(lines), "gog_events": [spec],
               "source": "conversation", "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}

    pending = Path.home() / ".openclaw/pending_cal_event.json"
    if pending.exists():
        raise RuntimeError(
            "A calendar proposal is already waiting. Show it with "
            "`python3 Skills/shared/show_pending_proposal.py cal` and let the user approve or skip "
            "that one first — a second proposal would overwrite it.")
    pending.write_text(json.dumps(payload, indent=2))

    show = subprocess.run(
        [sys.executable, "-W", "ignore", str(Path(__file__).parent / "show_pending_proposal.py"), "cal"],
        capture_output=True, text=True, timeout=60,
    )
    if show.returncode != 0:
        pending.unlink(missing_ok=True)
        raise RuntimeError(f"proposal not delivered: {(show.stderr or show.stdout).strip()[:300]}")
    return payload


def main():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--who", required=True, help="Person or activity name, e.g. 'Alex Example'")
    p.add_argument("--venue", required=True, help="Venue name only — no city/state/zip")
    p.add_argument("--from", dest="start", required=True, help="Start time, e.g. 2026-07-15T10:30:00-07:00")
    p.add_argument("--to", dest="end", required=True, help="End time, e.g. 2026-07-15T12:00:00-07:00")
    p.add_argument("--tz", required=True, help="IANA timezone of the event's physical location")
    p.add_argument("--location", required=True, help="Full street address, or 'Call' / 'Zoom'")
    p.add_argument("--description", default="")
    p.add_argument("--attendees", default="", help="Comma-separated emails (never --guest-email)")
    p.add_argument("--reminder-minutes", type=int, help="Explicit, previously computed leave-by offset")
    p.add_argument("--extra-reminder-minutes", type=int, action="append", default=None,
                   help="Additional popup offsets, e.g. a get-ready warning before the "
                        "leave-by one. Repeatable; gog accepts 5 reminders in total.")
    p.add_argument("--origin-address", default="", help="Previous same-day event's address, otherwise verified home")
    p.add_argument("--golf", action="store_true", help="Arrive 45 minutes before the tee time, plus driving time")
    p.add_argument("--calendar-id", default="primary")
    p.add_argument("--title", default="", help="Override the auto-built '{who} <> the user @ {venue}' title")
    p.add_argument("--all-day", action="store_true",
                   help="All-day event. --from and --to are plain dates (YYYY-MM-DD) and "
                        "--to is the LAST DAY INCLUDED; Google's exclusive end is added for you.")
    p.add_argument("--transparency", default="", choices=["", "busy", "free"],
                   help="Show as busy or free. Google's default for an all-day event is FREE — "
                        "pass busy explicitly for anything that must block the day.")
    p.add_argument("--meet", action="store_true", help="Attach a Google Meet video conference (gog auto-generates the link — never ask the user for one)")
    p.add_argument("--propose", action="store_true",
                   help="Send the draft to Telegram with a real approval button instead of creating it. "
                        "Use this for a calendar write the user has not already approved — never type your own "
                        "'✅ to confirm' under a plain message; a bare ✅ cannot bind to it.")
    args = p.parse_args()

    try:
        if args.all_day:
            args.start, args.end = normalize_date(args.start), normalize_date(args.end)
        else:
            args.start = normalize_datetime(args.start)
            args.end = normalize_datetime(args.end)
    except RuntimeError as e:
        print(f"{'NOT PROPOSED' if args.propose else 'FAILED'}: {e}", file=sys.stderr)
        sys.exit(1)

    if args.propose:
        try:
            payload = propose_event(
                who=args.who, venue=args.venue, start=args.start, end=args.end,
                tz=args.tz, location=args.location, description=args.description,
                attendees=args.attendees, reminder_minutes=args.reminder_minutes,
                extra_reminders=args.extra_reminder_minutes,
                title=args.title, origin_address=args.origin_address, golf=args.golf,
                all_day=args.all_day, transparency=args.transparency,
            )
        except RuntimeError as e:
            print(f"NOT PROPOSED: {e}", file=sys.stderr)
            sys.exit(1)
        print("Proposal sent with its own approval button. Nothing is on the calendar until the user approves.\n")
        print(payload["proposal_text"])
        return

    try:
        event = create_event(
            who=args.who, venue=args.venue, start=args.start, end=args.end,
            tz=args.tz, location=args.location, description=args.description,
            attendees=args.attendees, reminder_minutes=args.reminder_minutes,
                extra_reminders=args.extra_reminder_minutes,
            calendar_id=args.calendar_id, title=args.title, meet=args.meet,
            origin_address=args.origin_address, golf=args.golf,
            all_day=args.all_day, transparency=args.transparency,
        )
    except RuntimeError as e:
        print(f"FAILED: {e}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(event, indent=2))


if __name__ == "__main__":
    main()
