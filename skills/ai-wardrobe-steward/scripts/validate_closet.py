#!/usr/bin/env python3
"""Validate profile/closet.json against vocab.yaml's controlled vocabularies.

Run after any write to closet.json. Exits 1 and prints every error if the
file doesn't conform (missing required field, value outside the controlled
vocabulary, malformed date) — the same discipline as the finance skill's
validate_finance_yaml.py.

Usage: python3 validate_closet.py
"""
import json
import sys
from datetime import datetime
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is required: pip install pyyaml")

ROOT = Path(__file__).resolve().parent.parent
CLOSET_PATH = ROOT / "profile" / "closet.json"
VOCAB_PATH = ROOT / "vocab.yaml"

REQUIRED_FIELDS = [
    "id", "category", "color", "season",
    "lifecycle_state", "condition", "added_via", "added_date",
]
# formality is deliberately NOT required — every fit-critical script that
# reads it (outfit_candidates.py, travel_packing.py, closet_report.py) is
# already built to skip and flag a missing formality rather than treat it
# as an error, since it's often genuinely unknown at intake time and has no
# sensible universal default the way season ("all-season") or condition
# ("excellent" for something just bought) usually do. Requiring it here
# would silently fail confirmation immediately after a legitimate
# gaps-included draft gets confirmed — exactly the app-like blocking
# behavior this skill's design rules out.


def flat_categories(vocab):
    cats = []
    for group in vocab["category_groups"].values():
        cats.extend(group)
    return cats


def is_valid_date(s):
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return True
    except (ValueError, TypeError):
        return False


def validate_item(item, index, vocab):
    errors = []
    prefix = f"item[{index}] (id={item.get('id', '?')})"

    for field in REQUIRED_FIELDS:
        if field not in item or item[field] in (None, ""):
            errors.append(f"{prefix}: missing required field '{field}'")

    if "category" in item and item["category"] not in flat_categories(vocab):
        errors.append(f"{prefix}: category '{item['category']}' not in vocab.yaml category_groups")

    if "season" in item and item["season"] not in vocab["seasons"]:
        errors.append(f"{prefix}: season '{item['season']}' not in vocab.yaml seasons")

    if "lifecycle_state" in item and item["lifecycle_state"] not in vocab["lifecycle_states"]:
        errors.append(f"{prefix}: lifecycle_state '{item['lifecycle_state']}' not in vocab.yaml lifecycle_states")

    if item.get("sleeve") is not None and item["sleeve"] not in vocab.get("sleeve_lengths", []):
        errors.append(f"{prefix}: sleeve '{item['sleeve']}' not in vocab.yaml sleeve_lengths")

    if "condition" in item and item["condition"] not in vocab["condition_ratings"]:
        errors.append(f"{prefix}: condition '{item['condition']}' not in vocab.yaml condition_ratings")

    if "added_via" in item and item["added_via"] not in vocab["intake_sources"]:
        errors.append(f"{prefix}: added_via '{item['added_via']}' not in vocab.yaml intake_sources")

    # None is allowed (formality not required, see above) — only a present,
    # non-null value gets checked against the enum. isinstance check first:
    # Python's bool is an int subclass, so `True in {1: ..., 2: ...}` is True
    # (True == 1) — without this, formality: true would silently pass as if
    # it were formality: 1.
    formality = item.get("formality")
    if formality is not None and (isinstance(formality, bool) or formality not in vocab["formality_scale"]):
        errors.append(f"{prefix}: formality '{formality}' must be one of {list(vocab['formality_scale'].keys())}")

    if "added_date" in item and item["added_date"] and not is_valid_date(item["added_date"]):
        errors.append(f"{prefix}: added_date '{item['added_date']}' is not YYYY-MM-DD")

    if item.get("purchase_date") and not is_valid_date(item["purchase_date"]):
        errors.append(f"{prefix}: purchase_date '{item['purchase_date']}' is not YYYY-MM-DD")

    price = item.get("purchase_price")
    if price is not None and (isinstance(price, bool) or not isinstance(price, (int, float))):
        errors.append(f"{prefix}: purchase_price must be numeric or null, got {price!r}")

    wear_count = item.get("wear_count")
    if wear_count is not None and (isinstance(wear_count, bool) or not isinstance(wear_count, (int, float))):
        errors.append(f"{prefix}: wear_count must be numeric or null, got {wear_count!r}")

    if "occasion_tags" in item:
        if not isinstance(item["occasion_tags"], list):
            errors.append(f"{prefix}: occasion_tags must be a list, got {type(item['occasion_tags']).__name__}")
        else:
            for tag in item["occasion_tags"]:
                if tag not in vocab["occasion_tiers"]:
                    errors.append(f"{prefix}: occasion_tag '{tag}' not in vocab.yaml occasion_tiers")

    # estimated_frequency: an optional, self-reported usage baseline captured
    # once at intake for a pre-existing item (see vocab.yaml) — never
    # required, only checked against the enum when actually present.
    freq = item.get("estimated_frequency")
    if freq is not None and freq not in vocab.get("usage_frequency_estimates", []):
        errors.append(f"{prefix}: estimated_frequency '{freq}' not in vocab.yaml usage_frequency_estimates")

    # palette_match: an optional snapshot from color_match.py, set at intake.
    # Not a controlled-vocab field beyond `verdict`; a loose shape check only.
    pm = item.get("palette_match")
    if pm is not None:
        if not isinstance(pm, dict):
            errors.append(f"{prefix}: palette_match must be a mapping or null, got {type(pm).__name__}")
        else:
            verdict = pm.get("verdict")
            if verdict is not None and verdict not in vocab.get("palette_verdicts", []):
                errors.append(f"{prefix}: palette_match.verdict '{verdict}' not in vocab.yaml palette_verdicts")

    # disposition: only meaningful once an item is actually retired — set
    # without lifecycle_state being 'retired' would be a stale or
    # premature record nothing else expects.
    disposition = item.get("disposition")
    if disposition is not None:
        if disposition not in vocab.get("disposition_types", []):
            errors.append(f"{prefix}: disposition '{disposition}' not in vocab.yaml disposition_types")
        if item.get("lifecycle_state") != "retired":
            errors.append(f"{prefix}: disposition is set but lifecycle_state is '{item.get('lifecycle_state')}', not 'retired'")

    disposition_value = item.get("disposition_value")
    if disposition_value is not None and (isinstance(disposition_value, bool) or not isinstance(disposition_value, (int, float))):
        errors.append(f"{prefix}: disposition_value must be numeric or null, got {disposition_value!r}")

    if item.get("disposition_date") and not is_valid_date(item["disposition_date"]):
        errors.append(f"{prefix}: disposition_date '{item['disposition_date']}' is not YYYY-MM-DD")

    return errors


def main():
    if not VOCAB_PATH.exists():
        sys.exit(f"vocab.yaml not found at {VOCAB_PATH}")
    with open(VOCAB_PATH) as f:
        vocab = yaml.safe_load(f)

    if not CLOSET_PATH.exists():
        sys.exit(f"closet.json not found at {CLOSET_PATH}")
    with open(CLOSET_PATH) as f:
        closet = json.load(f)

    if not isinstance(closet, list):
        sys.exit(f"FAIL — closet.json top level must be a list, got {type(closet).__name__}")

    ids_seen = {}
    all_errors = []
    for i, item in enumerate(closet):
        if not isinstance(item, dict):
            all_errors.append(f"item[{i}] must be a mapping, got {type(item).__name__}: {item!r}")
            continue
        all_errors.extend(validate_item(item, i, vocab))
        item_id = item.get("id")
        if item_id:
            ids_seen.setdefault(item_id, []).append(i)

    for item_id, indexes in ids_seen.items():
        if len(indexes) > 1:
            all_errors.append(f"duplicate id '{item_id}' used at indexes {indexes}")

    if all_errors:
        print(f"FAIL — {len(all_errors)} issue(s):")
        for e in all_errors:
            print(f"  - {e}")
        sys.exit(1)

    print(f"OK — {len(closet)} item(s) validated against vocab.yaml")


if __name__ == "__main__":
    main()
