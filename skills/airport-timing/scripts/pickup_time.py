#!/usr/bin/env python3
"""When to leave to pick someone up at the airport — and the calendar event for it.

Airport timing only ever covered the user's own departures (security, gate close). On
2026-09-21 he forwarded a guest's flight (DL100 → DAL, checking a bag) and asked
Archibald to work out when to leave. This works backward from the moment the
passenger walks out of baggage claim. It sends nothing and writes nothing: it prints
the plan and the exact `add_calendar_event.py ... --propose` command to run.

    python3 pickup_time.py --flight DL100 --airport DAL \\
        --arrival "2026-09-22 14:05" --checked-bag [--passenger "Name"]

--arrival is the scheduled landing time as shown on the itinerary, local to the
arrival airport (an explicit UTC offset is also accepted).
"""
import argparse
import json
import shlex
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

SHARED = Path(__file__).resolve().parents[2] / "shared"
sys.path.insert(0, str(SHARED))
import drive_time  # noqa: E402

# Gate arrival -> passenger outside, in minutes.
DEPLANE_MINUTES = 10        # narrow-body, mid-cabin seat
BAG_ON_CAROUSEL_MINUTES = 20  # Delta guarantees 20 min domestic; other US carriers are similar
BAG_GRAB_MINUTES = 5        # spot the bag, walk out
AIRPORT_SLACK_MINUTES = 5   # be at the airport (cell lot) this long before they walk out
EVENT_TAIL_MINUTES = 15     # load up and leave the curb


def airport_config(code: str) -> dict:
    airports = drive_time._travel_cfg().get("airports", {})
    if code not in airports:
        raise SystemExit(f"No airport {code!r} in travel_config.json — add it (lat/lng/name/address/tz) first.")
    return airports[code]


def walk_minutes(airport: dict, cfg: dict) -> int:
    terminals = airport.get("terminals") or {}
    if len(terminals) == 1:
        return next(iter(terminals.values())).get("walk_minutes", 10)
    return airport.get("default_walk_minutes", cfg.get("default_walk_minutes", 12))


def parse_arrival(value: str, tz: ZoneInfo) -> datetime:
    dt = datetime.fromisoformat(value.strip().replace(" ", "T", 1))
    return dt.replace(tzinfo=tz) if dt.tzinfo is None else dt.astimezone(tz)


def fmt(dt: datetime) -> str:
    return dt.strftime("%-I:%M %p")


def plan(*, flight: str, airport_code: str, arrival: str, checked_bag: bool,
         passenger: str = "", origin_address: str = "") -> dict:
    cfg = drive_time._travel_cfg()
    airport = airport_config(airport_code)
    tz = ZoneInfo(airport["tz"])
    lands = parse_arrival(arrival, tz)

    walk = walk_minutes(airport, cfg)
    to_curb = DEPLANE_MINUTES + walk
    if checked_bag:
        to_curb = max(to_curb, BAG_ON_CAROUSEL_MINUTES + BAG_GRAB_MINUTES)
    out_at = lands + timedelta(minutes=to_curb)
    be_there = out_at - timedelta(minutes=AIRPORT_SLACK_MINUTES)

    if origin_address:
        coords = drive_time.geocode(origin_address, strict=False)
        if not coords:
            raise SystemExit(f"Could not locate origin {origin_address!r}.")
        o_lat, o_lng, origin_label = coords[0], coords[1], origin_address
    else:
        o_lat, o_lng, origin_label = drive_time.origin_coords_and_label()

    fallback = int((cfg.get("fallback_drive_minutes") or {}).get(airport_code, 30))
    estimated = []
    guess_leave = be_there - timedelta(minutes=fallback)
    drive = drive_time.drive_minutes(o_lat, o_lng, airport["lat"], airport["lng"], depart_at=guess_leave)
    if drive is None:
        drive = fallback
        estimated.append("drive to airport")
    leave = be_there - timedelta(minutes=drive)
    leave -= timedelta(minutes=leave.minute % 5)  # round down to a clock-friendly time
    lead = int((be_there - leave).total_seconds() // 60)  # popup fires at the leave time

    pull_away = out_at + timedelta(minutes=EVENT_TAIL_MINUTES)
    back = drive_time.drive_minutes(airport["lat"], airport["lng"], o_lat, o_lng, depart_at=pull_away)
    if back is None:
        back = fallback
        estimated.append("drive back")

    title = f"{passenger} Pickup <> the user @ {airport['name']}" if passenger else f"Airport Pickup <> the user @ {airport['name']}"
    status_link = f"https://www.google.com/search?q={flight.replace(' ', '')}+flight+status"
    description = "\n".join(filter(None, [
        f"{flight} lands {fmt(lands)}" + (" — checking a bag" if checked_bag else " — carry-on only"),
        f"Out at the curb ~{fmt(out_at)}. Leave {origin_label} by {fmt(leave)} ({drive} min drive).",
        airport.get("pickup_note", ""),
        f"Check live status before leaving (early landing moves everything up): {status_link}",
    ]))

    event = {
        "title": title,
        "from": be_there.isoformat(timespec="seconds"),
        "to": pull_away.isoformat(timespec="seconds"),
        "tz": airport["tz"],
        "location": airport["address"],
        "reminder_minutes": lead,
        "description": description,
    }
    wrapper = SHARED / "add_calendar_event.py"
    propose = ["python3", str(wrapper), "--who", title.split(" <> ")[0], "--venue", airport["name"],
               "--from", event["from"], "--to", event["to"], "--tz", event["tz"],
               "--location", event["location"], "--reminder-minutes", str(lead),
               "--title", title, "--description", description, "--propose"]

    return {
        "flight": flight,
        "airport": airport["name"],
        "lands": fmt(lands),
        "checked_bag": checked_bag,
        "out_at_curb": fmt(out_at),
        "be_at_airport": fmt(be_there),
        "leave_by": fmt(leave),
        "origin": origin_label,
        "drive_minutes": drive,
        "back_by": fmt(pull_away + timedelta(minutes=back)),
        "drive_back_minutes": back,
        "estimated": estimated,
        "event": event,
        "propose_command": shlex.join(propose),
    }


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--flight", required=True, help="e.g. DL100")
    p.add_argument("--airport", required=True, help="Arrival airport code, e.g. DAL (must be in travel_config.json)")
    p.add_argument("--arrival", required=True, help="Scheduled landing, local to the arrival airport, e.g. '2026-09-22 14:05'")
    p.add_argument("--checked-bag", action="store_true", help="Passenger is checking a bag")
    p.add_argument("--passenger", default="", help="Name, for the event title")
    p.add_argument("--origin-address", default="", help="Where the user leaves from, if not home/current location")
    args = p.parse_args()
    print(json.dumps(plan(flight=args.flight, airport_code=args.airport, arrival=args.arrival,
                          checked_bag=args.checked_bag, passenger=args.passenger,
                          origin_address=args.origin_address), indent=2))


if __name__ == "__main__":
    main()
