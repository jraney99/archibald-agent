#!/usr/bin/env python3
"""
CLI for adding/listing/removing flight-deal-tracker trip watches and
tuning opportunistic-route thresholds, so the agent never hand-edits
config/watchlist.json directly from chat.

Usage:
    manage_trips.py --add --label "Austin trip" --origin DAL --dest AUS \\
        --depart 2026-10-12 --return 2026-10-19 [--flexible-days 2] [--target-price 65]
    manage_trips.py --list
    manage_trips.py --remove <trip_id>
    manage_trips.py --confirm <trip_id>          # candidate (calendar-detected) -> watching
    manage_trips.py --set-threshold <route_id> <price>
"""
from __future__ import annotations

import argparse
import sys
import uuid
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _state_lock import locked_state  # noqa: E402

SKILL_DIR = Path(__file__).parent.parent
WATCHLIST_FILE = SKILL_DIR / "config" / "watchlist.json"
ALERT_STATE_FILE = SKILL_DIR / "state" / "deal_alert_state.json"
DEFAULT_WATCHLIST = {"home_airports": [], "opportunistic_routes": [], "trips": []}


def cmd_add(args) -> None:
    with locked_state(WATCHLIST_FILE, default=DEFAULT_WATCHLIST) as watchlist:
        trip = {
            "id": str(uuid.uuid4())[:8],
            "label": args.label,
            "origin": args.origin,
            "destination": args.dest,
            "depart_date": args.depart,
            "return_date": args.return_date,
            "flexible_days": args.flexible_days,
            "target_price": args.target_price,
            "source": "manual",
            "status": "watching",
            "created": date.today().isoformat(),
        }
        watchlist.setdefault("trips", []).append(trip)
        print(f"Added trip {trip['id']}: {trip['label']} ({trip['origin']}->{trip['destination']}, {trip['depart_date']})")


def cmd_list(args) -> None:
    with locked_state(WATCHLIST_FILE, default=DEFAULT_WATCHLIST) as watchlist:
        trips = watchlist.get("trips", [])
        if not trips:
            print("No trips being watched.")
            return
        for t in trips:
            ret = f" -> {t['return_date']}" if t.get("return_date") else ""
            target = f", target ${t['target_price']}" if t.get("target_price") else ""
            print(f"[{t['status']}] {t['id']}: {t['label']} {t['origin']}->{t['destination']} {t['depart_date']}{ret}{target}")


def cmd_remove(args) -> None:
    with locked_state(WATCHLIST_FILE, default=DEFAULT_WATCHLIST) as watchlist:
        before = len(watchlist.get("trips", []))
        watchlist["trips"] = [t for t in watchlist.get("trips", []) if t["id"] != args.remove]
        after = len(watchlist["trips"])
        removed = after < before
    if removed:
        with locked_state(ALERT_STATE_FILE, default={}) as alert_state:
            alert_state.pop(f"trip:{args.remove}", None)
        print(f"Removed trip {args.remove}")
    else:
        print(f"No trip found with id {args.remove}")


def cmd_confirm(args) -> None:
    with locked_state(WATCHLIST_FILE, default=DEFAULT_WATCHLIST) as watchlist:
        found = False
        for t in watchlist.get("trips", []):
            if t["id"] == args.confirm:
                t["status"] = "watching"
                found = True
        print(f"Confirmed trip {args.confirm} -> watching" if found else f"No trip found with id {args.confirm}")


def cmd_set_threshold(args) -> None:
    route_id, price = args.set_threshold
    with locked_state(WATCHLIST_FILE, default=DEFAULT_WATCHLIST) as watchlist:
        found = False
        for r in watchlist.get("opportunistic_routes", []):
            if r["id"] == route_id:
                # Opportunistic routes are priced as round trips (check_deals.py).
                r["max_price_roundtrip"] = int(price)
                r.pop("max_price_oneway", None)
                found = True
        print(f"Set {route_id} round-trip threshold to ${price}" if found else f"No route found with id {route_id}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--add", action="store_true")
    parser.add_argument("--label")
    parser.add_argument("--origin")
    parser.add_argument("--dest")
    parser.add_argument("--depart")
    parser.add_argument("--return", dest="return_date")
    parser.add_argument("--flexible-days", type=int, default=0)
    parser.add_argument("--target-price", type=int, default=None)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--remove")
    parser.add_argument("--confirm")
    parser.add_argument("--set-threshold", nargs=2, metavar=("ROUTE_ID", "PRICE"))
    args = parser.parse_args()

    if args.add:
        if not (args.label and args.origin and args.dest and args.depart):
            parser.error("--add requires --label --origin --dest --depart")
        cmd_add(args)
    elif args.list:
        cmd_list(args)
    elif args.remove:
        cmd_remove(args)
    elif args.confirm:
        cmd_confirm(args)
    elif args.set_threshold:
        cmd_set_threshold(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
