#!/usr/bin/env python3
"""
Daily scan of the user's calendar for events that look like trips to one of
the watched opportunistic destinations (config/watchlist.json), so the
flight-deal tracker can pick up a trip without the user having to say
anything -- e.g. a "Weekend in LA" event becomes a candidate DAL->LAX
trip watch.

Matches are a keyword heuristic only, so every new match is added to
watchlist.json as a "candidate" (not "watching") and triggers exactly one
Telegram confirm ping -- never auto-starts tracking, and never re-pings
for the same calendar event again (tracked in
state/calendar_seen_events.json, kept even if the candidate is later
declined/removed) regardless of what happens to the resulting trip entry.

Confirm/decline happens via manage_trips.py:
    manage_trips.py --confirm <trip_id>   # candidate -> watching
    manage_trips.py --remove <trip_id>    # discard the candidate

Usage:
    python3 detect_trips_from_calendar.py [--dry-run]
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import uuid
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _state_lock import locked_state  # noqa: E402

SKILL_DIR = Path(__file__).parent.parent
WATCHLIST_FILE = SKILL_DIR / "config" / "watchlist.json"
SEEN_EVENTS_FILE = SKILL_DIR / "state" / "calendar_seen_events.json"
SEND_TELEGRAM = SKILL_DIR.parent / "shared" / "send_telegram.py"
GOG = "/opt/homebrew/bin/gog"
ACCOUNT = "owner@example.com"
CALENDARS = ",".join([
    "owner@example.com",
    "owner@work.example",
    "contact@example.org",
])
HORIZON_DAYS = 180

# route_id -> (destination airport, match keywords, home-metro exclusion terms)
ROUTE_KEYWORDS = {
    "la": (["los angeles", "l.a."], "LAX"),
    "nyc": (["new york", "nyc", "manhattan", "brooklyn"], "JFK"),
    "sfo": (["san francisco", "bay area", "oakland"], "SFO"),
    "aus": (["austin"], "AUS"),
}
HOME_METRO_TERMS = ["dallas", "fort worth", "dfw", "plano", "frisco", "addison", "love field"]

# Auto-generated drive-to-airport reminders (see email-monitor/scripts/daemon.py
# leave_summary) are logistics for a flight that's ALREADY booked -- not a
# signal that a new trip needs a price watch. Must not be treated as a match.
EXCLUDE_PREFIXES = ["leave for "]

# Booked-flight calendar events are titled "{AIRLINE} {NUM} {ORIGIN} -> {DEST}
# <> the user @ {Venue}" (see RULES.md calendar title convention). The return leg
# of a round trip (e.g. "WN 202 LAX -> DAL") names the destination airport
# in its own title/location, so the generic keyword+home-metro-exclusion path
# below either drops the outbound leg (its venue is a home airport, e.g.
# "Dallas Love Field") or keeps the return leg but has no way to know it's a
# return, not a departure -- confirmed 2026-08-26 on the LA trip: the return
# leg landed in watchlist.json as depart_date with return_date null, and
# outbound (today's WN 201 DAL->LAX, already on the calendar) was silently
# dropped as "no_match". Parse explicit airport codes instead so direction is
# never guessed from location text.
FLIGHT_LEG_RE = re.compile(r"\b([A-Z]{3})\s*(?:→|->)\s*([A-Z]{3})\b")


def parse_flight_leg(summary: str) -> tuple[str, str] | None:
    m = FLIGHT_LEG_RE.search(summary or "")
    return (m.group(1), m.group(2)) if m else None


def is_too_late(leg_date: str) -> bool:
    """True if a leg's date is today or earlier -- already flying (or flown)
    by the time the daily cron runs, so there's no window left to catch a
    price drop and rebook. Fixed 2026-08-26: a same-day outbound leg used to
    be proposed for tracking anyway (see feedback-flight-leg-direction-bug
    memory)."""
    return leg_date <= date.today().isoformat()


def match_flight_leg(
    origin: str, dest: str, home_airports: set[str], routes_by_id: dict
) -> tuple[str, str] | tuple[None, None]:
    """Classify a parsed flight leg as the outbound or return half of a watched route."""
    for route_id, route in routes_by_id.items():
        dests = set(route.get("destinations", []))
        if origin in home_airports and dest in dests:
            return route_id, "outbound"
        if dest in home_airports and origin in dests:
            return route_id, "return"
    return None, None


def load_json(path: Path, default):
    import json
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def fetch_events() -> list[dict]:
    import json
    cmd = [GOG, "cal", "ls", "--account", ACCOUNT, "--calendars", CALENDARS,
           "--days", str(HORIZON_DAYS), "-j", "--max", "300"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0 or not result.stdout.strip():
        return []
    try:
        return json.loads(result.stdout).get("events", [])
    except Exception:
        return []


def event_dates(ev: dict) -> tuple[str, str] | None:
    start, end = ev.get("start", {}), ev.get("end", {})
    if "dateTime" in start:
        s = datetime.fromisoformat(start["dateTime"]).date().isoformat()
    elif "date" in start:
        s = start["date"]
    else:
        return None
    if "dateTime" in end:
        e = datetime.fromisoformat(end["dateTime"]).date().isoformat()
    elif "date" in end:
        e = end["date"]
    else:
        e = s
    return s, e


def match_route(text: str) -> str | None:
    text = text.lower()
    if any(text.startswith(prefix) for prefix in EXCLUDE_PREFIXES):
        return None
    if any(term in text for term in HOME_METRO_TERMS):
        return None
    for route_id, (keywords, _dest) in ROUTE_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return route_id
    return None


# Verdicts in calendar_seen_events.json that are permanent: a real confirm ping
# went out (candidate_sent — kept even after the user declines, so it never re-pings),
# or the event's own date has already passed (past). Every other verdict
# ("no_match", "return_leg_unpaired", "covered_by_flight") is PROVISIONAL — it
# depends on the current watchlist config and the other events on the calendar,
# so it's dropped and re-derived every run. Without this, a "no_match" filed when
# the nyc route only listed JFK stayed frozen forever: adding LGA/EWR later never
# rescued the real DAL→LGA flight, and a dinner whose street address read
# "…, New York, NY" got proposed as the trip instead (2026-08-29).
STICKY_VERDICTS = {"candidate_sent", "past"}


def route_of_airport(dest_code: str, routes_by_id: dict) -> str | None:
    for route_id, route in routes_by_id.items():
        if dest_code in route.get("destinations", []):
            return route_id
    return None


def send_telegram(text: str, dry_run: bool) -> None:
    if dry_run:
        print(f"--- would send Telegram ---\n{text}\n")
        return
    subprocess.run(["python3", str(SEND_TELEGRAM), "--text", text], capture_output=True, text=True, timeout=20)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    watchlist = load_json(WATCHLIST_FILE, {"home_airports": ["DAL"], "opportunistic_routes": [], "trips": []})
    routes_by_id = {r["id"]: r for r in watchlist.get("opportunistic_routes", [])}
    home_origin = (watchlist.get("home_airports") or ["DAL"])[0]
    home_airports = set(watchlist.get("home_airports") or ["DAL"])

    events = fetch_events()

    with locked_state(SEEN_EVENTS_FILE, default={}) as seen:
        # Drop provisional verdicts so this run re-derives them against the
        # current watchlist config + calendar (see STICKY_VERDICTS).
        for stale_id in [k for k, v in seen.items()
                         if v.get("status") not in STICKY_VERDICTS]:
            del seen[stale_id]

        new_candidates = []
        keyword_matches = []  # (event, route_id, depart, return_date) — resolved after flight legs
        leg_events = []  # (event, route_id, direction, event_date, airport_code)

        for ev in events:
            event_id = ev.get("id")
            if not event_id or event_id in seen:
                continue

            summary = ev.get("summary", "")
            leg = parse_flight_leg(summary)
            if leg:
                # A recognized flight-leg title is definitive about direction;
                # never fall through to the generic keyword/location matcher
                # below, which can't tell outbound from return (see note above
                # FLIGHT_LEG_RE) and would misclassify or wrongly exclude it.
                route_id, direction = match_flight_leg(leg[0], leg[1], home_airports, routes_by_id)
                if not route_id:
                    seen[event_id] = {"status": "no_match"}
                    continue
                dates = event_dates(ev)
                if not dates or is_too_late(dates[0]):
                    seen[event_id] = {"status": "past" if dates else "no_match"}
                    continue
                airport_code = leg[1] if direction == "outbound" else leg[0]
                leg_events.append((ev, route_id, direction, dates[0], airport_code))
                continue

            text = f"{summary} {ev.get('location', '')}"
            route_id = match_route(text)
            if not route_id or route_id not in routes_by_id:
                seen[event_id] = {"status": "no_match"}
                continue
            dates = event_dates(ev)
            if not dates:
                seen[event_id] = {"status": "no_match"}
                continue
            depart, return_date = dates
            if is_too_late(depart):
                seen[event_id] = {"status": "past"}
                continue

            # Defer: a keyword match is only a real candidate if it isn't
            # already inside a flight-covered window (resolved below).
            keyword_matches.append((ev, route_id, depart, return_date))

        # Pair each outbound flight leg with the nearest later return leg on
        # the same route into one correctly-dated candidate, instead of
        # creating two separate (and in the return leg's case, mislabeled)
        # entries. An outbound leg with no matching return becomes a one-way
        # candidate, same as before. A return leg with no matching outbound
        # (e.g. the outbound already flew) is dropped rather than surfaced
        # with depart_date missing -- nothing useful to watch without it.
        by_route: dict[str, list] = {}
        for item in leg_events:
            by_route.setdefault(item[1], []).append(item)

        for route_id, items in by_route.items():
            route = routes_by_id[route_id]
            outbound_legs = sorted((i for i in items if i[2] == "outbound"), key=lambda i: i[3])
            return_legs = sorted((i for i in items if i[2] == "return"), key=lambda i: i[3])
            used_return_ids = set()

            for ev, _route_id, _direction, depart_date, dest_code in outbound_legs:
                event_id = ev.get("id")
                paired = next(
                    (r for r in return_legs if r[3] > depart_date and r[0].get("id") not in used_return_ids),
                    None,
                )
                trip_id = str(uuid.uuid4())[:8]
                trip = {
                    "id": trip_id, "label": f"{route['label']} ({ev.get('summary', 'calendar event')})",
                    "origin": home_origin, "destination": dest_code,
                    "depart_date": depart_date, "return_date": None,
                    "flexible_days": 0, "target_price": None, "source": "calendar",
                    "event_id": event_id, "status": "candidate", "created": date.today().isoformat(),
                }
                seen[event_id] = {"status": "candidate_sent", "trip_id": trip_id}
                if paired:
                    ret_ev, _, _, return_date, _ = paired
                    ret_id = ret_ev.get("id")
                    used_return_ids.add(ret_id)
                    trip["return_date"] = return_date
                    trip["return_event_id"] = ret_id
                    seen[ret_id] = {"status": "candidate_sent", "trip_id": trip_id}
                new_candidates.append(trip)

            for ev, _route_id, _direction, _return_date, _dest_code in return_legs:
                event_id = ev.get("id")
                if event_id not in used_return_ids:
                    seen[event_id] = {"status": "return_leg_unpaired"}

        # Resolve deferred keyword matches. A non-flight event (dinner, meeting)
        # that falls inside a window already covered by a trip for the same
        # route is just part of that trip — don't pitch it separately. This is
        # what stopped "Dinner - Example Trattoria" (address "…, New York, NY") from
        # being proposed as a NYC trip while WN 101 DAL→LGA / WN 102 LGA→DAL
        # sat on the calendar (2026-08-29).
        #
        # "Covered" = every trip currently in watchlist.json (any status — a
        # declined trip is deleted outright by manage_trips.py, so anything
        # still listed is live intent) plus the flight candidates this run just
        # produced. Using every status, not just "watching", is deliberate: a
        # keyword event must not re-ping on the next run just because the flight
        # candidate it belongs to hasn't been confirmed yet.
        covered: dict[str, list] = {}

        def _add_covered(dest: str, depart: str, ret: str | None) -> None:
            rid = route_of_airport(dest, routes_by_id)
            if rid and depart:
                covered.setdefault(rid, []).append((depart, ret or depart))

        for trip in new_candidates:
            _add_covered(trip["destination"], trip["depart_date"], trip.get("return_date"))
        for trip in watchlist.get("trips", []):
            _add_covered(trip.get("destination", ""), trip.get("depart_date"), trip.get("return_date"))

        for ev, route_id, depart, return_date in keyword_matches:
            event_id = ev.get("id")
            if any(lo <= depart <= hi for lo, hi in covered.get(route_id, [])):
                seen[event_id] = {"status": "covered_by_flight"}
                continue
            route = routes_by_id[route_id]
            trip_id = str(uuid.uuid4())[:8]
            trip = {
                "id": trip_id, "label": f"{route['label']} ({ev.get('summary', 'calendar event')})",
                "origin": home_origin, "destination": route["destinations"][0],
                "depart_date": depart, "return_date": return_date if return_date != depart else None,
                "flexible_days": 0, "target_price": None, "source": "calendar",
                "event_id": event_id, "status": "candidate", "created": date.today().isoformat(),
            }
            new_candidates.append(trip)
            seen[event_id] = {"status": "candidate_sent", "trip_id": trip_id}

        if new_candidates:
            with locked_state(WATCHLIST_FILE, default={"home_airports": [home_origin], "opportunistic_routes": [], "trips": []}) as watchlist_w:
                watchlist_w.setdefault("trips", []).extend(new_candidates)

        for trip in new_candidates:
            date_str = trip["depart_date"] + (f" – {trip['return_date']}" if trip["return_date"] else "")
            text = (
                f"✈️ Looks like a trip: {trip['label']}\n"
                f"{date_str}\n\n"
                f"Want me to start tracking flights for it? Reply to confirm, or say to skip it.\n"
                f"(id: {trip['id']})"
            )
            send_telegram(text, args.dry_run)


if __name__ == "__main__":
    main()
