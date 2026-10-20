#!/usr/bin/env python3
"""Filter the closet down to real, wearable candidates for an outfit request.

This does the deterministic part only — active-state filtering, formality
range, season match — grouped by outfit slot (tops/bottoms/outerwear/etc.
from vocab.yaml). Composing the actual outfit (which top with which bottom,
color coordination, styling notes) is a judgment call made from this filtered
list, not by this script — the point is to guarantee the model only ever
picks real, currently-wearable items instead of inventing or misremembering
one.

Usage:
  python3 outfit_candidates.py --occasion business-casual [--season fall]

Prints JSON: {"target_formality": N, "candidates": {slot: [items]}, "warnings": [...]}
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
sys.path.insert(0, str(Path(__file__).resolve().parent))
from activity_policy import activity_preference, comfort_rules, effective_policy  # noqa: E402


def alternative_slots(alternative):
    """A one_of alternative is a slot name or a list of slots required together."""
    return [alternative] if isinstance(alternative, str) else list(alternative)


def normalize_occasion(raw, vocab):
    value = raw.strip().lower()
    if value in vocab["occasion_formality"]:
        return value, None
    for phrase, occasion in vocab.get("occasion_aliases", {}).items():
        if phrase in value:
            return occasion, phrase
    return None, None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--occasion", required=True)
    parser.add_argument("--season", default=None)
    parser.add_argument("--activity", default="general")
    parser.add_argument("--feels-like-f", type=float, default=None, help="Forecast feels-like temperature for the event.")
    parser.add_argument("--outdoor", action="store_true", help="The event is outdoors.")
    parser.add_argument("--explicit-occasion", action="store_true",
                        help="The event states this dress code outright; keep it even if a personal activity preference says otherwise.")
    args = parser.parse_args()

    with open(VOCAB_PATH) as f:
        vocab = yaml.safe_load(f)

    occasion, matched_alias = normalize_occasion(args.occasion, vocab)
    if occasion is None:
        sys.exit(f"Unknown occasion '{args.occasion}'. Valid: {list(vocab['occasion_formality'].keys())}")

    if args.season and args.season not in vocab["seasons"]:
        sys.exit(f"Unknown season '{args.season}'. Valid: {vocab['seasons']}")
    if args.activity not in vocab.get("activities", []):
        sys.exit(f"Unknown activity '{args.activity}'. Valid: {vocab.get('activities', [])}")

    warnings = []
    preference = activity_preference(args.activity)
    preferred_occasion = preference.get("occasion")
    if preferred_occasion and preferred_occasion != occasion and not args.explicit_occasion:
        warnings.append(f"Occasion '{occasion}' replaced by '{preferred_occasion}': personal preference for {args.activity} ({preference.get('evidence', 'no evidence recorded')}).")
        occasion = preferred_occasion
    slot_categories = {**vocab.get("activity_slot_categories", {}).get(args.activity, {}),
                       **(preference.get("slot_categories", {}) if not args.explicit_occasion else {})}
    season = None if args.activity in vocab.get("climate_controlled_activities", []) else args.season
    # Slots whose categories the wearer picks by temperature: the real forecast
    # outranks a garment's calendar-season tag (85F in October is shorts weather).
    temperature_slots = set()
    for slot, rule in ({} if args.explicit_occasion else preference.get("temperature_slot_categories", {})).items():
        if args.feels_like_f is None:
            warnings.append(f"{slot} for {args.activity} depend on temperature (switch at {rule['threshold_f']:g}F); pass --feels-like-f.")
            continue
        slot_categories[slot] = rule["at_or_above"] if args.feels_like_f >= rule["threshold_f"] else rule["below"]
        temperature_slots.add(slot)
    if preference.get("guidance"):
        warnings.append(f"Personal guidance for {args.activity}: {preference['guidance']}")

    target_formality = vocab["occasion_formality"][occasion]

    heat = comfort_rules().get("long_sleeve_heat", {})
    dressy_floor = heat.get("dressy_indoor_min_formality")
    dressy_indoor = (dressy_floor is not None and target_formality >= dressy_floor and not args.outdoor
                     and args.activity not in vocab.get("physical_activities", []))
    no_long_sleeves = bool(heat) and args.feels_like_f is not None and args.feels_like_f >= heat["limit_f"] and not dressy_indoor
    unknown_sleeves = []

    with open(CLOSET_PATH) as f:
        closet = json.load(f)

    category_to_slot = {}
    for slot, categories in vocab["outfit_slots"].items():
        for cat in categories:
            category_to_slot[cat] = slot

    candidates = {slot: [] for slot in vocab["outfit_slots"]}
    skipped_no_formality = []

    for item in closet:
        if item.get("lifecycle_state") != "active":
            continue
        formality = item.get("formality")
        if formality is None or isinstance(formality, bool) or not isinstance(formality, (int, float)):
            skipped_no_formality.append(item.get("id", "?"))
            continue
        if abs(formality - target_formality) > FORMALITY_TOLERANCE:
            continue
        slot = category_to_slot.get(item.get("category"))
        if season and slot not in temperature_slots and item.get("season") not in (season, "all-season"):
            continue
        if slot == "base_top" and no_long_sleeves:
            if item.get("sleeve") == "long":
                continue
            if item.get("sleeve") is None:
                unknown_sleeves.append(item.get("id", "?"))

        if slot:
            policy = effective_policy(item)
            if args.activity in policy.get("excluded_activities", []):
                continue
            if policy.get("activity_exclusive") and args.activity not in policy.get("allowed_activities", []):
                continue
            allowed_categories = slot_categories.get(slot)
            if allowed_categories and item.get("category") not in allowed_categories:
                continue
            enriched = dict(item)
            enriched["activity_policy"] = policy
            candidates[slot].append(enriched)

    if no_long_sleeves:
        warnings.append(f"Long-sleeve tops removed: {args.feels_like_f:g}F is at/above the wearer's {heat['limit_f']:g}F limit for this kind of event.")
    if unknown_sleeves:
        warnings.append(f"{len(unknown_sleeves)} top(s) have no sleeve length recorded, so the heat rule could not check them: {', '.join(unknown_sleeves)}")
    if skipped_no_formality:
        warnings.append(f"{len(skipped_no_formality)} active item(s) skipped — missing or invalid formality, can't verify fit for this occasion: {', '.join(skipped_no_formality)}")

    requirements = vocab.get("occasion_requirements", {}).get(occasion, {})
    missing_required = [slot for slot in requirements.get("all", []) if not candidates.get(slot)]
    one_of = requirements.get("one_of", [])
    if one_of and not any(all(candidates.get(slot) for slot in alternative_slots(alt)) for alt in one_of):
        missing_required.append("one of: " + ", ".join(" + ".join(alternative_slots(alt)) for alt in one_of))
    if missing_required:
        warnings.append("Incomplete for this occasion; missing required slot(s): " + "; ".join(missing_required))
    if requirements.get("advisory"):
        warnings.append(requirements["advisory"])
    activity_advisory = vocab.get("activity_advisories", {}).get(args.activity)
    if activity_advisory:
        warnings.append(activity_advisory)

    result = {
        "occasion": occasion,
        "occasion_input": args.occasion,
        "matched_alias": matched_alias,
        "season": args.season,
        "activity": args.activity,
        "activity_preference": preference or None,
        "target_formality": target_formality,
        "requirements": requirements,
        "candidates": {slot: items for slot, items in candidates.items() if items},
        "warnings": warnings,
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
