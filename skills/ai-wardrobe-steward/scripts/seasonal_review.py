#!/usr/bin/env python3
"""Seasonal rotation review — given an incoming season, lists packed-away
items tagged for it, so a season change ("it's getting cold") can prompt a
real, specific "want to bring these back into rotation?" instead of a vague
reminder. Deterministic filtering only, same discipline as
outfit_candidates.py/travel_packing.py — never invents an item, never
changes lifecycle_state itself (that's a closet_bulk_state.py call once the
user confirms which items are actually coming back).

No calendar/weather hook is wired up yet, so this is triggered by the season
actually coming up in conversation, not on a timer — the same "optional
hook, ask directly otherwise" pattern as the rest of this skill's external
integrations.

Usage: python3 seasonal_review.py --season fall
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", required=True)
    args = parser.parse_args()

    with open(VOCAB_PATH) as f:
        vocab = yaml.safe_load(f)

    if args.season not in vocab["seasons"]:
        sys.exit(f"'{args.season}' is not a valid season. Valid: {vocab['seasons']}")

    with open(CLOSET_PATH) as f:
        closet = json.load(f)

    packed_away = [i for i in closet if i.get("lifecycle_state") == "packed-away"]
    matches = [i for i in packed_away if i.get("season") in (args.season, "all-season")]

    print(json.dumps({
        "season": args.season,
        "packed_away_total": len(packed_away),
        "match_count": len(matches),
        "matches": [
            {
                "id": i.get("id"),
                "category": i.get("category"),
                "color": i.get("color"),
                "season": i.get("season"),
            }
            for i in matches
        ],
    }, indent=2))


if __name__ == "__main__":
    main()
