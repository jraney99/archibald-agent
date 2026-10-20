#!/usr/bin/env python3
"""Total money invested in the closet — a real sum from purchase_price data,
not an estimate. Seeds DESIGN.md Module 7 (Wardrobe Analytics: spending
patterns) without requiring the full analytics module to exist yet.

Items missing a purchase_price are excluded from the total and counted
separately, so the number is never silently wrong. Same treatment for a
purchase_price that's present but non-numeric (e.g. a string extracted from
a receipt without being converted) — excluded and called out by id, rather
than crashing the whole report or silently mis-summing (bool is explicitly
excluded too, since Python's bool is an int subclass and would otherwise
silently sum as 0/1).

Also reports what's actually been recouped from retired items via
disposition_value (resold/etc.) — money in is only half the stewardship
picture; a closet that resells well is a real, measurable outcome the
skill's "buy less, buy better" goal should be able to point to, not just
assert.

Usage: python3 closet_value.py
"""
import json
from collections import defaultdict
from pathlib import Path

CLOSET_PATH = Path(__file__).resolve().parent.parent / "profile" / "closet.json"


def main():
    if not CLOSET_PATH.exists():
        print("No closet.json found.")
        return

    with open(CLOSET_PATH) as f:
        closet = json.load(f)

    if not closet:
        print("Closet is empty — nothing to total.")
        return

    def is_numeric_price(i):
        price = i.get("purchase_price")
        return isinstance(price, (int, float)) and not isinstance(price, bool)

    unpriced = [i for i in closet if i.get("purchase_price") is None]
    corrupted = [i for i in closet if i.get("purchase_price") is not None and not is_numeric_price(i)]
    priced = [i for i in closet if is_numeric_price(i)]

    total = sum(i["purchase_price"] for i in priced)

    by_category = defaultdict(lambda: [0, 0])  # [total, count]
    by_state = defaultdict(lambda: [0, 0])
    for i in priced:
        by_category[i.get("category", "unknown")][0] += i["purchase_price"]
        by_category[i.get("category", "unknown")][1] += 1
        by_state[i.get("lifecycle_state", "unknown")][0] += i["purchase_price"]
        by_state[i.get("lifecycle_state", "unknown")][1] += 1

    print(f"Total invested: ${total:,.2f} across {len(priced)} item(s)")
    if unpriced:
        print(f"({len(unpriced)} item(s) missing a purchase_price, excluded: {', '.join(i.get('id', '?') for i in unpriced)})")
    if corrupted:
        print(f"({len(corrupted)} item(s) have a non-numeric purchase_price, excluded: {', '.join(i.get('id', '?') for i in corrupted)} — run validate_closet.py)")

    print("\nBy category:")
    for cat, (amount, count) in sorted(by_category.items(), key=lambda kv: -kv[1][0]):
        print(f"  {cat}: ${amount:,.2f} ({count} item{'s' if count != 1 else ''})")

    print("\nBy lifecycle state:")
    for state, (amount, count) in sorted(by_state.items(), key=lambda kv: -kv[1][0]):
        print(f"  {state}: ${amount:,.2f} ({count} item{'s' if count != 1 else ''})")

    def is_numeric_value(v):
        return isinstance(v, (int, float)) and not isinstance(v, bool)

    recouped_items = [i for i in closet if i.get("lifecycle_state") == "retired" and is_numeric_value(i.get("disposition_value"))]
    if recouped_items:
        recouped_total = sum(i["disposition_value"] for i in recouped_items)
        print(f"\nRecouped from retired items: ${recouped_total:,.2f} across {len(recouped_items)} item(s)")
        print(f"Net invested (total minus recouped): ${total - recouped_total:,.2f}")

        by_disposition = defaultdict(lambda: [0, 0])
        for i in recouped_items:
            disp = i.get("disposition", "unknown")
            by_disposition[disp][0] += i["disposition_value"]
            by_disposition[disp][1] += 1
        print("\nBy disposition:")
        for disp, (amount, count) in sorted(by_disposition.items(), key=lambda kv: -kv[1][0]):
            print(f"  {disp}: ${amount:,.2f} ({count} item{'s' if count != 1 else ''})")


if __name__ == "__main__":
    main()
