#!/usr/bin/env python3
"""Look up known brand sizing from body_fit_profile.yaml's brand_sizing
list — the factual grounding for Shopping Advisor step 6 (DESIGN.md Module
6), so fit-critical size suggestions come from a real past data point
instead of manual eyeballing of the YAML.

Usage:
  python3 brand_sizing_lookup.py --brand "J.Crew" [--category shirt]
"""
import argparse
import sys
from difflib import SequenceMatcher
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is required: pip install pyyaml")

PROFILE_PATH = Path(__file__).resolve().parent.parent / "profile" / "body_fit_profile.yaml"


def brand_matches(a, b):
    if not isinstance(a, str) or not isinstance(b, str) or not a or not b:
        return False, 0.0
    a_l, b_l = a.strip().lower(), b.strip().lower()
    if a_l in b_l or b_l in a_l:
        return True, 1.0
    sim = SequenceMatcher(None, a_l, b_l).ratio()
    return sim > 0.8, sim


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--brand", required=True)
    parser.add_argument("--category", default=None)
    args = parser.parse_args()

    if not PROFILE_PATH.exists():
        print("No body_fit_profile.yaml found.")
        return

    with open(PROFILE_PATH) as f:
        profile = yaml.safe_load(f) or {}

    brand_sizing = profile.get("brand_sizing", [])
    if not isinstance(brand_sizing, list):
        sys.exit("'brand_sizing' is not a list — body_fit_profile.yaml may be corrupted. Run validate_body_fit_profile.py.")

    matches = []
    for entry in brand_sizing:
        if not isinstance(entry, dict):
            continue
        matched, sim = brand_matches(args.brand, entry.get("brand"))
        if not matched:
            continue
        if args.category and entry.get("category") != args.category:
            continue
        matches.append((sim, entry))

    if not matches:
        print(f"No brand_sizing entries found for '{args.brand}'" + (f" in category '{args.category}'." if args.category else "."))
        return

    matches.sort(key=lambda m: m[0], reverse=True)
    print(f"{len(matches)} match(es) for '{args.brand}':")
    for sim, entry in matches:
        note = f" — {entry.get('notes')}" if entry.get("notes") else ""
        confidence = entry.get("confidence")
        conf_note = f", confidence {confidence}%" if isinstance(confidence, int) else ""
        evidence = entry.get("evidence") or []
        evidence_note = f" (most recent: {evidence[-1]['note']}, {evidence[-1]['date']})" if evidence else ""
        print(f"  - {entry.get('brand')} / {entry.get('category', '?')}: size {entry.get('size', '?')} (match {sim:.2f}{conf_note}){note}{evidence_note}")


if __name__ == "__main__":
    main()
