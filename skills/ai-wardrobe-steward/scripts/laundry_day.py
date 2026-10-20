#!/usr/bin/env python3
"""Return washed clothes to the closet on laundry day. Silent: never messages anyone.

A confirmed wear marks one-wear washables `dirty` (mark_after_wear.py). Nothing
ever marked them clean again, so once wear logging worked the closet would have
emptied itself of shirts and tees within weeks. Rather than ask "laundry done?",
this follows how he actually lives: set laundry days (profile/laundry.json).

Runs daily; decides for itself whether today counts:
  - laundry day at home  -> every home-washable item dirty since BEFORE today is active again
                            (what he wore today is not in this load)
  - laundry day while away -> nothing; the clothes at home were not washed. The next
                            laundry day at home picks them up.
  - any day at home      -> safety net: dirty for more than `max_dirty_days` comes back,
                            so a skipped week can never empty the closet.
Dry-clean and spot-clean pieces are never touched; he says when those are back.

Usage:
  python3 laundry_day.py [--dry-run] [--today YYYY-MM-DD] [--location auto|home|away]
"""
import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CLOSET = ROOT / "profile" / "closet.json"
CONFIG = ROOT / "profile" / "laundry.json"
CARE = ROOT / "care_heuristics.yaml"
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
DEFAULTS = {"laundry_days": [], "max_dirty_days": 10}


def home_washable():
    rules = yaml.safe_load(CARE.read_text()).get("by_category", {})
    return {cat for cat, rule in rules.items()
            if "wash" in str(rule.get("action", "")).lower() and "dry clean" not in str(rule.get("action", "")).lower()}


def away_from_home():
    # A failed lookup counts as home: wrongly returning a shirt costs one bad
    # suggestion; never returning them empties the closet.
    try:
        sys.path.insert(0, str(ROOT.parent / "shared"))
        from current_location import is_away_from_home
        return bool(is_away_from_home())
    except Exception:  # noqa: BLE001
        return False


def dirty_since(item):
    for key in ("dirty_since", "last_worn"):
        try:
            return datetime.strptime(str(item.get(key))[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
    return None      # marked dirty by hand with no date: treat as part of any load


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--today")
    ap.add_argument("--location", choices=["auto", "home", "away"], default="auto")
    args = ap.parse_args()

    config = {**DEFAULTS, **(json.loads(CONFIG.read_text()) if CONFIG.exists() else {})}
    days = [str(d).lower() for d in config["laundry_days"]]
    unknown = [d for d in days if d not in WEEKDAYS]
    if unknown:
        sys.exit(f"Unknown laundry day(s) in {CONFIG.name}: {', '.join(unknown)}")
    today = datetime.strptime(args.today, "%Y-%m-%d").date() if args.today else date.today()
    away = args.location == "away" or (args.location == "auto" and away_from_home())
    is_laundry_day = WEEKDAYS[today.weekday()] in days

    closet = json.loads(CLOSET.read_text())
    washable = home_washable()
    returned = []
    if not away:
        for item in closet:
            if item.get("lifecycle_state") != "dirty" or item.get("category") not in washable:
                continue
            since = dirty_since(item)
            in_this_load = is_laundry_day and (since is None or since < today)
            overdue = since is not None and (today - since).days > config["max_dirty_days"]
            if in_this_load or overdue:
                returned.append({"id": item.get("id"), "why": "laundry day" if in_this_load else f"dirty {(today - since).days} days"})
                if not args.dry_run:
                    item["lifecycle_state"] = "active"
                    item.pop("dirty_since", None)
    if returned and not args.dry_run:
        CLOSET.write_text(json.dumps(closet, indent=2))
    print(json.dumps({"date": today.isoformat(), "laundry_day": is_laundry_day, "away": away,
                      "dry_run": args.dry_run, "returned": returned}, indent=2))


if __name__ == "__main__":
    main()
