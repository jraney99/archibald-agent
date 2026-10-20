#!/usr/bin/env python3
"""Read-only: open tee times at the user's approved courses, from GolfNow's tee sheet.

    python3 search_tee_times.py --date 2026-10-17 [--course "example creek"] [--players 2]
                                [--earliest 07:00 --latest 18:30] [--max-price 90]
                                [--format telegram|json|plain]

Searches only the approved list (config/approved_courses.json). Asking for a course that
is not on it exits 3 and says so; an approved course on a provider without an adapter is
listed with its own booking link instead of a tee sheet. Nothing here books anything.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import courses as course_list  # noqa: E402
import golfnow  # noqa: E402


def parse_day(text: str) -> date:
    t = (text or "").strip().lower()
    today = date.today()
    if t in ("", "today"):
        return today
    if t == "tomorrow":
        return today + timedelta(days=1)
    days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    if t in days:
        ahead = (days.index(t) - today.weekday()) % 7 or 7
        return today + timedelta(days=ahead)
    return datetime.strptime(t, "%Y-%m-%d").date()


def search(cfg: dict, day: date, players: int, wanted: list, earliest: str, latest: str,
           max_price: float, fetch=golfnow.fetch) -> dict:
    """{"day", "players", "courses": [{course, slots|error|not_bookable}]}. `fetch` is
    injectable so tests never touch the network."""
    if wanted:
        picked = []
        for w in wanted:
            c = course_list.find(cfg, w)
            if c is None:
                return {"error": f"{w!r} is not on your approved course list.", "unapproved": w}
            if isinstance(c, list):
                return {"error": f"{w!r} could be " + " or ".join(x["short"] for x in c) + ". Which one?",
                        "ambiguous": [x["id"] for x in c]}
            picked.append(c)
    else:
        picked = course_list.approved(cfg)

    home = cfg.get("home", {})
    rows = []
    for c in picked:
        row = {"course": c["short"], "id": c["id"]}
        if not course_list.bookable(c):
            row["not_bookable"] = course_list.not_bookable_reason(c)
            rows.append(row)
            continue
        try:
            data = fetch(c["facility_id"], day, players, home.get("lat", 32.78), home.get("lon", -96.80))
        except golfnow.ProviderError as exc:
            row["error"] = str(exc)
            rows.append(row)
            continue
        found = [s for s in golfnow.slots(data) if golfnow.within(s, earliest, latest)
                 and (max_price is None or s["price"] <= max_price) and not s["restricted"]]
        row["slots"] = found
        rows.append(row)
    return {"day": day.isoformat(), "players": players, "earliest": earliest, "latest": latest,
            "max_price": max_price, "courses": rows}


def _group(slots: list) -> list:
    """'7:30 AM - 9:10 AM (6) $45' lines: consecutive same-price slots collapsed."""
    lines = []
    run = []
    for s in slots + [None]:
        if s and run and s["price"] == run[-1]["price"] and s["holes"] == run[-1]["holes"]:
            run.append(s)
            continue
        if run:
            first, last = run[0], run[-1]
            span = first["time_label"] if len(run) == 1 else f"{first['time_label']} - {last['time_label']} ({len(run)})"
            tag = " hot deal" if any(r["hot"] for r in run) else ""
            lines.append(f"  {span}  ${first['price']:.0f}/golfer, {first['holes']} holes{tag}")
            run = []
        if s:
            run = [s]
    return lines


def format_telegram(result: dict) -> str:
    """Plain text (no markdown - it is relayed as-is). One block per course."""
    if result.get("error"):
        return result["error"]
    day = datetime.strptime(result["day"], "%Y-%m-%d").strftime("%A %b %-d")
    out = [f"Tee times {day}, {result['players']} golfer{'s' if result['players'] != 1 else ''}:"]
    for row in result["courses"]:
        out.append("")
        out.append(row["course"])
        if row.get("not_bookable"):
            out.append("  " + row["not_bookable"])
        elif row.get("error"):
            out.append("  " + row["error"])
        elif not row["slots"]:
            out.append(f"  nothing open between {result['earliest']} and {result['latest']}"
                       + (f" under ${result['max_price']:.0f}" if result.get("max_price") else ""))
        else:
            out.extend(_group(row["slots"]))
    out.append("")
    out.append("Prices per golfer before GolfNow's fee and tax. Say the course and time and I will propose it.")
    return "\n".join(out)


def _json_ready(result: dict) -> dict:
    def conv(o):
        if isinstance(o, datetime):
            return o.isoformat()
        raise TypeError(str(type(o)))
    return json.loads(json.dumps(result, default=conv))


def main(argv=None) -> int:
    cfg = course_list.load()
    d = cfg.get("defaults", {})
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--date", default="today", help="YYYY-MM-DD, today, tomorrow or a weekday name")
    p.add_argument("--course", action="append", default=[], help="approved course name/alias; repeatable; default all")
    p.add_argument("--players", type=int, default=d.get("players", 1))
    p.add_argument("--earliest", default=d.get("earliest", "07:00"))
    p.add_argument("--latest", default=d.get("latest", "18:30"))
    p.add_argument("--max-price", type=float, default=d.get("max_price_per_golfer"))
    p.add_argument("--format", choices=("telegram", "json", "plain"), default="telegram")
    a = p.parse_args(argv)
    try:
        day = parse_day(a.date)
    except ValueError:
        print(f"--date {a.date!r}: use YYYY-MM-DD, today, tomorrow or a weekday.", file=sys.stderr)
        return 2
    result = search(cfg, day, a.players, a.course, a.earliest, a.latest, a.max_price)
    if a.format == "json":
        print(json.dumps(_json_ready(result), indent=2))
    else:
        print(format_telegram(result))
    if result.get("error"):
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
