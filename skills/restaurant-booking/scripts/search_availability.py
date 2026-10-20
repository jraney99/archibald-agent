#!/usr/bin/env python3
"""
Read-only OpenTable availability check.

Given a restaurant name, date, party size and target time, resolves the
restaurant within the user's metro and prints the bookable slots near the
target time. Never books anything - this is the "find" half.

Usage:
    python3 search_availability.py --name "Uchi" --date 2026-08-29 \
        --time "7:00 PM" --party 2 [--metro "Dallas - Fort Worth"] [--json]

Exit codes:
    0  one restaurant resolved, slots printed (may be zero slots)
    3  multiple candidate restaurants - names printed, caller must disambiguate
    4  no restaurant matched
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))
import browser_helpers as bh  # noqa: E402

CONFIG = json.loads((SCRIPT_DIR.parent / "config" / "restaurant_booking_config.json").read_text())


def parse_target(date_str: str, time_str: str) -> datetime:
    t = bh.norm_time(time_str)
    if not t:
        raise SystemExit(f"--time {time_str!r} is not a clock time like '7:00 PM'.")
    return datetime.strptime(f"{date_str} {t}", "%Y-%m-%d %I:%M %p")


def iso_slot(dt: datetime) -> str:
    # OpenTable's dateTime query param format: 2026-08-29T19:00
    return dt.strftime("%Y-%m-%dT%H:%M")


def minutes_between(a: str, b: str) -> int:
    fmt = "%I:%M %p"
    return abs(int((datetime.strptime(a, fmt) - datetime.strptime(b, fmt)).total_seconds()) // 60)


def search_term(name: str, metro: str, metro_id: int | None) -> str:
    """Keep the term restaurant-specific when URL context already pins metro."""
    return name if metro_id is not None else f"{name} {metro}"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--name", required=True)
    p.add_argument("--date", required=True, help="YYYY-MM-DD")
    p.add_argument("--time", required=True, help='e.g. "7:00 PM"')
    p.add_argument("--party", type=int, default=CONFIG["defaults"]["party_size"])
    p.add_argument("--metro", default=CONFIG["metro"])
    p.add_argument("--metro-id", type=int, default=CONFIG.get("metro_id"),
                   help="OpenTable metro ID used to pin search context (default: configured metro)")
    p.add_argument("--tolerance", type=int, default=CONFIG["defaults"]["slot_tolerance_minutes"])
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    want_time = bh.norm_time(args.time)
    target = parse_target(args.date, args.time)
    dt_iso = iso_slot(target)

    bh.ensure_logged_in()

    # A verified metro ID supplies the geographic context. Repeating the metro
    # in `term` can make OpenTable classify the entire query as a location
    # search (for example, "Lucia Dallas - Fort Worth") instead of resolving
    # the restaurant name.
    term = search_term(args.name, args.metro, args.metro_id)
    candidates = bh.search_restaurants(term, dt_iso, args.party, args.metro_id)

    key = args.name.strip().lower()
    # City/metro words to ignore when comparing (OpenTable appends the city
    # to many restaurant names, e.g. "Uchi Dallas").
    city_words = {w.lower() for w in re.split(r"[\s/-]+", args.metro) if w}

    def core(name: str) -> str:
        words = [w for w in re.split(r"\s+", name.strip().lower()) if w and w not in city_words]
        return " ".join(words)

    exact = [c for c in candidates if core(c["name"]) == key]
    if exact:
        matches = exact
    else:
        word_re = re.compile(rf"\b{re.escape(key)}\b", re.IGNORECASE)
        matches = ([c for c in candidates if word_re.search(c["name"])]
                   or [c for c in candidates if key in c["name"].lower()]
                   or candidates)

    if not matches:
        out = {"status": "no_match", "term": term}
        print(json.dumps(out) if args.json else f"No OpenTable match for '{args.name}' in {args.metro}.")
        sys.exit(4)

    if len(matches) > 1:
        out = {"status": "ambiguous", "candidates": matches}
        if args.json:
            print(json.dumps(out))
        else:
            print("Multiple matches - which one?")
            for c in matches:
                print(f"  - {c['name']}  ({c['slug']})")
        sys.exit(3)

    r = matches[0]
    info = bh.read_slots(r["url"], dt_iso, args.party, slot_pattern=CONFIG["selectors"]["slot_button"])
    slots = info["slots"]
    near = sorted(
        (s for s in slots if minutes_between(s["time"], want_time) <= args.tolerance),
        key=lambda s: minutes_between(s["time"], want_time),
    )

    out = {
        "status": "ok",
        "restaurant": r,
        "date": args.date,
        "party": args.party,
        "requested_time": want_time,
        "card_required": info["card_required"],
        "slots_near": [s["time"] for s in near],
        "all_slots": [s["time"] for s in slots],
    }
    if args.json:
        print(json.dumps(out))
    else:
        if not slots:
            print(f"{r['name']}: no availability shown for {args.date}, party {args.party}.")
        else:
            print(f"{r['name']} - {args.date}, party {args.party}"
                  + ("  [credit card required to hold]" if info["card_required"] else ""))
            print(f"  Near {want_time}: {', '.join(s['time'] for s in near) or '(none)'}")
            print(f"  All slots: {', '.join(s['time'] for s in slots)}")


if __name__ == "__main__":
    try:
        main()
    except bh.BrowserError as e:
        print(f"browser: {e}", file=sys.stderr)
        sys.exit(5)
