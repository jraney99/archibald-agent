#!/usr/bin/env python3
"""Care & Maintenance (DESIGN.md Module 8) — checks which items are due for
care based on wear count (or elapsed time for watches), logs maintenance
performed, and lets a per-item default be overridden from user feedback.

Usage:
  python3 care.py check
  python3 care.py laundry
  python3 care.py log --item <id> --action "<what was done>" [--date YYYY-MM-DD]
  python3 care.py override --item <id> [--interval-wears N] [--interval-days N] [--action "..."]

`log` and `override` write immediately — the user stating "I got this
cleaned" or "this doesn't need dry cleaning that often" is itself the
confirmation, the same precedent as outfit_history.py's `log` command.

`laundry` is a different question from `check`: `check` is about the
cleaning-interval heuristic (has this been worn enough since last care to be
due again); `laundry` is a simple aggregate of what's marked `dirty` right
now, grouped by category — "is it worth doing a wash today," not "what's
overdue."
"""
import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is required: pip install pyyaml")

ROOT = Path(__file__).resolve().parent.parent
CLOSET_PATH = ROOT / "profile" / "closet.json"
HEURISTICS_PATH = ROOT / "care_heuristics.yaml"

ELIGIBLE_STATES = {"active", "dirty"}

# Categories whose category default DOESN'T already imply a safe universal
# action, unlike blazer/suit (dry clean is fine regardless of exact fabric) —
# leather/suede vs. cotton/nylon/canvas genuinely need different actions here
# (condition/protect vs. spot-clean/air-out vs. wash), so material wins over
# the category default (see get_rule).
MATERIAL_FIRST_CATEGORIES = {"bag", "belt", "wallet", "boots", "loafers", "dress_shoes", "jacket", "coat"}

# Lightweight accessories that should never inherit a generic material rule —
# material_overrides (wool, leather, ...) is calibrated for garment-weight
# items (suits, jackets), not a wool beanie. These stay rule-less unless the
# user sets an explicit care_override.
NO_MATERIAL_FALLBACK = {"sunglasses", "jewelry", "hat", "tie", "scarf"}


def load_closet():
    with open(CLOSET_PATH) as f:
        return json.load(f)


def save_closet(closet):
    with open(CLOSET_PATH, "w") as f:
        json.dump(closet, f, indent=2)


def parse_date(s):
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def get_rule(item, heuristics):
    """Priority: care_override always wins. For a watch with a known
    movement_type, watch_movement_types wins over the generic by_category
    watch rule (a quartz battery change and a mechanical service are
    nothing alike). For MATERIAL_FIRST_CATEGORIES (bag/belt/wallet/
    boots/loafers/dress_shoes/jacket/coat), a material_overrides match wins
    over the category default — a canvas tote and a leather tote need
    completely different care, so category alone isn't enough there. For
    every other category, category wins over material (a cotton blazer is
    still a structured garment, not a t-shirt, even though they share a
    fabric — category encodes more real-world context than fiber alone).
    material_overrides is a fallback for any other uncategorized item,
    EXCEPT NO_MATERIAL_FALLBACK accessories, which stay rule-less rather than
    inherit a generic garment-weight rule that was never calibrated for them.

    care_override is layered ON TOP of whatever default was found, not a full
    replacement — a partial override (e.g. just a real care-label drying
    instruction) should still inherit the rest (interval, action) from the
    default rather than silently losing it."""
    category = item.get("category")
    material = (item.get("material") or "").strip().lower()
    movement_type = (item.get("movement_type") or "").strip().lower()

    base = None
    if category == "watch" and movement_type in heuristics.get("watch_movement_types", {}):
        base = dict(heuristics["watch_movement_types"][movement_type])
    elif category in MATERIAL_FIRST_CATEGORIES and material in heuristics.get("material_overrides", {}):
        base = dict(heuristics["material_overrides"][material])
    elif category in heuristics.get("by_category", {}):
        base = dict(heuristics["by_category"][category])
    elif category not in NO_MATERIAL_FALLBACK and material in heuristics.get("material_overrides", {}):
        base = dict(heuristics["material_overrides"][material])

    override = item.get("care_override")
    if override:
        return {**base, **override} if base else dict(override)
    return base


def cmd_check():
    with open(HEURISTICS_PATH) as f:
        heuristics = yaml.safe_load(f)
    closet = load_closet()
    today = date.today()

    due = []
    unreadable = []
    for item in closet:
        if item.get("lifecycle_state") not in ELIGIBLE_STATES:
            continue
        rule = get_rule(item, heuristics)
        if not rule:
            continue

        log = item.get("maintenance_log", [])
        last = log[-1] if log else None

        if "interval_days" in rule:
            if last:
                last_date = parse_date(last.get("date"))
                if last_date is None:
                    unreadable.append(item.get("id", "?"))
                    continue
            else:
                last_date = parse_date(item.get("added_date"))
                if last_date is None:
                    continue
            days_since = (today - last_date).days
            if days_since >= rule["interval_days"]:
                due.append((item, rule, f"{days_since} days since last service"))
        else:
            wear_at_last = last.get("wear_count_at_maintenance", 0) if last else 0
            wear_count = item.get("wear_count", 0)
            if not isinstance(wear_count, (int, float)) or isinstance(wear_count, bool) \
                    or not isinstance(wear_at_last, (int, float)) or isinstance(wear_at_last, bool):
                unreadable.append(item.get("id", "?"))
                continue
            wears_since = wear_count - wear_at_last
            if wears_since >= rule.get("interval_wears", float("inf")):
                due.append((item, rule, f"{wears_since} wears since last care"))

    if unreadable:
        print(f"Note: {len(unreadable)} item(s) have a missing/invalid date or non-numeric wear_count and couldn't be checked: {', '.join(unreadable)}")

    if not due:
        print("Nothing due for care right now.")
        return

    print(f"{len(due)} item(s) due for care:")
    for item, rule, metric in due:
        line = f"  - {item['id']}: {rule['action']} ({metric})"
        if rule.get("drying"):
            line += f" — drying: {rule['drying']}"
        print(line)


def cmd_laundry():
    closet = load_closet()
    dirty = [i for i in closet if i.get("lifecycle_state") == "dirty"]
    if not dirty:
        print("Nothing marked dirty right now.")
        return

    by_category = {}
    for i in dirty:
        by_category.setdefault(i.get("category", "unknown"), []).append(i.get("id", "?"))

    print(f"{len(dirty)} item(s) dirty, across {len(by_category)} categor{'y' if len(by_category) == 1 else 'ies'}:")
    for cat, ids in sorted(by_category.items(), key=lambda kv: -len(kv[1])):
        print(f"  - {cat}: {len(ids)} ({', '.join(ids)})")


def cmd_log(args):
    closet = load_closet()
    item = next((i for i in closet if i.get("id") == args.item), None)
    if not item:
        sys.exit(f"No item with id '{args.item}'.")

    date_str = args.date or date.today().isoformat()
    item.setdefault("maintenance_log", []).append({
        "date": date_str,
        "action": args.action,
        "wear_count_at_maintenance": item.get("wear_count", 0),
    })
    save_closet(closet)
    print(f"Logged '{args.action}' for {args.item} on {date_str}.")

    if item.get("lifecycle_state") in ("dirty", "at-cleaner-or-repair"):
        print(f"Note: {args.item} is still marked '{item['lifecycle_state']}' — if it's back in rotation now, "
              f"run: python3 closet_bulk_state.py --ids {args.item} --set-state active --apply")


def cmd_override(args):
    closet = load_closet()
    item = next((i for i in closet if i.get("id") == args.item), None)
    if not item:
        sys.exit(f"No item with id '{args.item}'.")

    if args.interval_wears is None and args.interval_days is None and not args.action and not args.drying:
        sys.exit("Provide at least one of --interval-wears, --interval-days, --action, --drying.")

    override = item.get("care_override", {})
    if args.interval_wears is not None:
        override["interval_wears"] = args.interval_wears
        override.pop("interval_days", None)
    if args.interval_days is not None:
        override["interval_days"] = args.interval_days
        override.pop("interval_wears", None)
    if args.action:
        override["action"] = args.action
    if args.drying:
        override["drying"] = args.drying
    item["care_override"] = override

    save_closet(closet)
    print(f"Set care override for {args.item}: {override}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check")
    sub.add_parser("laundry")

    p_log = sub.add_parser("log")
    p_log.add_argument("--item", required=True)
    p_log.add_argument("--action", required=True)
    p_log.add_argument("--date", default=None)

    p_override = sub.add_parser("override")
    p_override.add_argument("--item", required=True)
    p_override.add_argument("--interval-wears", type=int, default=None)
    p_override.add_argument("--interval-days", type=int, default=None)
    p_override.add_argument("--action", default=None)
    p_override.add_argument("--drying", default=None)

    args = parser.parse_args()

    if args.command == "check":
        cmd_check()
    elif args.command == "laundry":
        cmd_laundry()
    elif args.command == "log":
        cmd_log(args)
    elif args.command == "override":
        cmd_override(args)


if __name__ == "__main__":
    main()
