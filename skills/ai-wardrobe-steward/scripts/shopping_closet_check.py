#!/usr/bin/env python3
"""Check the closet for existing items that overlap with a potential purchase.

The deterministic half of the Shopping Advisor (DESIGN.md Module 6) — finds
real items in the same category (optionally narrowed by formality/season) so
the model can reason about duplication and cost-per-wear from actual data,
not a guess. Judgment (need vs. want, quality read, style fit, buy/skip) is
made from this output, not by this script.

Includes items in every lifecycle_state, not just active — a retired or
needs-repair item in the same category is relevant history (e.g. "the last
one of these didn't hold up") even though it's not currently wearable.

Usage:
  python3 shopping_closet_check.py --category jacket [--formality 3] [--season fall]
"""
import argparse
import json
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is required: pip install pyyaml")

ROOT = Path(__file__).resolve().parent.parent
CLOSET_PATH = ROOT / "profile" / "closet.json"
VOCAB_PATH = ROOT / "vocab.yaml"

FORMALITY_TOLERANCE = 1


def flat_categories(vocab):
    cats = []
    for group in vocab["category_groups"].values():
        cats.extend(group)
    return cats


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--category", required=True)
    parser.add_argument("--formality", type=int, default=None)
    parser.add_argument("--season", default=None)
    args = parser.parse_args()

    with open(VOCAB_PATH) as f:
        vocab = yaml.safe_load(f)

    if args.category not in flat_categories(vocab):
        sys.exit(f"Unknown category '{args.category}'. Valid: {flat_categories(vocab)}")

    with open(CLOSET_PATH) as f:
        closet = json.load(f)

    matches = [item for item in closet if item.get("category") == args.category]

    if args.formality is not None:
        # A category match with unknown (or non-numeric/junk) formality is
        # kept, not dropped — silently excluding it here would hide a real
        # potential duplicate, which is the worse failure mode for a
        # stewardship-over-consumption tool (a missed "you already own one"
        # enables an unneeded purchase). isinstance check first: bool is an
        # int subclass and would otherwise silently compare as 0/1.
        def formality_ok(m):
            f = m.get("formality")
            if f is None:
                return True
            if isinstance(f, bool) or not isinstance(f, (int, float)):
                return True
            return abs(f - args.formality) <= FORMALITY_TOLERANCE

        matches = [m for m in matches if formality_ok(m)]

    if args.season:
        matches = [m for m in matches if m.get("season") in (args.season, "all-season")]

    output_matches = []
    for m in matches:
        price = m.get("purchase_price")
        wear_count = m.get("wear_count", 0)
        price_numeric = isinstance(price, (int, float)) and not isinstance(price, bool)
        wear_count_numeric = isinstance(wear_count, (int, float)) and not isinstance(wear_count, bool)
        cost_per_wear = round(price / wear_count, 2) if price_numeric and wear_count_numeric and price and wear_count else None
        output_matches.append({
            "id": m.get("id"),
            "brand": m.get("brand"),
            "color": m.get("color"),
            "lifecycle_state": m.get("lifecycle_state"),
            "condition": m.get("condition"),
            "wear_count": wear_count,
            "purchase_price": price,
            "realized_cost_per_wear": cost_per_wear,
            "last_worn": m.get("last_worn"),
            "formality_unknown": not isinstance(m.get("formality"), (int, float)) or isinstance(m.get("formality"), bool),
        })

    print(json.dumps({
        "category": args.category,
        "match_count": len(output_matches),
        "matches": output_matches,
    }, indent=2))


if __name__ == "__main__":
    main()
