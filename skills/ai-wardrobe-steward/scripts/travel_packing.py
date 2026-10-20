#!/usr/bin/env python3
"""Travel Packing (DESIGN.md Module 9) — combines outfit_candidates.py-style
filtering across every occasion a trip needs, deduped by item and annotated
with which occasion(s) each one covers. A capsule wardrobe is built from
whichever items cover the MOST occasions with the FEWEST pieces — this
script surfaces that data, composing the actual capsule/packing list is a
judgment call made from it, same division of labor as the Outfit Engine.

Usage:
  python3 travel_packing.py --occasions business-casual,casual --season fall [--days 5]

`--days` is for display/context only (how many outfits need to be covered),
it does not filter candidates.
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--occasions", required=True, help="comma-separated occasion tiers")
    parser.add_argument("--season", default=None)
    parser.add_argument("--days", type=int, default=None)
    args = parser.parse_args()

    with open(VOCAB_PATH) as f:
        vocab = yaml.safe_load(f)

    occasions = list(dict.fromkeys(o.strip() for o in args.occasions.split(",") if o.strip()))
    if not occasions:
        sys.exit("At least one occasion is required (--occasions casual,business-casual,...).")
    for occasion in occasions:
        if occasion not in vocab["occasion_formality"]:
            sys.exit(f"Unknown occasion '{occasion}'. Valid: {list(vocab['occasion_formality'].keys())}")

    if args.season and args.season not in vocab["seasons"]:
        sys.exit(f"Unknown season '{args.season}'. Valid: {vocab['seasons']}")

    with open(CLOSET_PATH) as f:
        closet = json.load(f)

    category_to_slot = {}
    for slot, categories in vocab["outfit_slots"].items():
        for cat in categories:
            category_to_slot[cat] = slot

    pool = {}  # item id -> {**item, "matches_occasions": [...]}
    skipped_no_id = set()  # closet indexes, so the same item isn't double-counted across occasions
    skipped_no_formality = set()
    for occasion in occasions:
        target_formality = vocab["occasion_formality"][occasion]
        for idx, item in enumerate(closet):
            if item.get("lifecycle_state") != "active":
                continue
            formality = item.get("formality")
            if formality is None or isinstance(formality, bool) or not isinstance(formality, (int, float)):
                skipped_no_formality.add(idx)
                continue
            if abs(formality - target_formality) > FORMALITY_TOLERANCE:
                continue
            if args.season and item.get("season") not in (args.season, "all-season"):
                continue
            item_id = item.get("id")
            if not item_id:
                skipped_no_id.add(idx)
                continue
            if item_id not in pool:
                pool[item_id] = {**item, "matches_occasions": []}
            pool[item_id]["matches_occasions"].append(occasion)

    candidates = {slot: [] for slot in vocab["outfit_slots"]}
    for item in pool.values():
        slot = category_to_slot.get(item.get("category"))
        if slot:
            candidates[slot].append(item)

    warnings = []
    # full_body (a suit) and athletic_wear (a tracksuit/one-piece) both cover
    # top+bottoms on their own — same substitution rule for either.
    has_top = bool(candidates.get("base_top")) or bool(candidates.get("layering_top")) or bool(candidates.get("full_body")) or bool(candidates.get("athletic_wear"))
    if not has_top:
        warnings.append("No active base_top/layering_top/full_body/athletic_wear items match any requested occasion/season.")
    if not candidates.get("bottoms") and not candidates.get("full_body") and not candidates.get("athletic_wear"):
        warnings.append("No active 'bottoms' items match any requested occasion/season.")
    if not candidates.get("footwear"):
        # a suit/full_body substitutes for a top+bottoms, but never for shoes
        warnings.append("No active 'footwear' items match any requested occasion/season.")
    if skipped_no_id:
        warnings.append(f"{len(skipped_no_id)} closet item(s) skipped — missing 'id'.")
    if skipped_no_formality:
        warnings.append(f"{len(skipped_no_formality)} closet item(s) skipped — missing or invalid formality, can't verify fit for any requested occasion.")

    print(json.dumps({
        "occasions": occasions,
        "season": args.season,
        "days": args.days,
        "candidates": {slot: items for slot, items in candidates.items() if items},
        "warnings": warnings,
    }, indent=2))


if __name__ == "__main__":
    main()
