#!/usr/bin/env python3
"""Look up past purchase lessons relevant to a potential purchase — the
factual grounding for PART 3's regret memory ("I'd avoid slim chinos, you
previously found that cut uncomfortable") so the Shopping Advisor draws on a
real recorded lesson instead of the agent trying to recall a prose note.

Usage:
  python3 purchase_lessons_lookup.py --keyword "slim chinos" [--lesson-type fit]
"""
import argparse
import json
import sys
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LESSONS_PATH = ROOT / "profile" / "purchase_lessons.json"

SIMILARITY_THRESHOLD = 0.5


def text_similarity(a, b):
    if not isinstance(a, str) or not isinstance(b, str) or not a or not b:
        return 0.0
    a_l, b_l = a.strip().lower(), b.strip().lower()
    if a_l in b_l or b_l in a_l:
        return 1.0
    return SequenceMatcher(None, a_l, b_l).ratio()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keyword", required=True, help="item description or category to match against")
    parser.add_argument("--lesson-type", default=None)
    args = parser.parse_args()

    if not LESSONS_PATH.exists():
        print("No purchase_lessons.json found.")
        return

    with open(LESSONS_PATH) as f:
        lessons = json.load(f)

    if not isinstance(lessons, list):
        sys.exit("'purchase_lessons.json' is not a list — may be corrupted. Run validate_purchase_lessons.py.")

    matches = []
    for entry in lessons:
        if not isinstance(entry, dict):
            continue
        if args.lesson_type and entry.get("lesson_type") != args.lesson_type:
            continue
        sim = text_similarity(args.keyword, entry.get("item_description"))
        if sim >= SIMILARITY_THRESHOLD:
            matches.append((sim, entry))

    if not matches:
        print(f"No purchase lessons found matching '{args.keyword}'" + (f" (type: {args.lesson_type})" if args.lesson_type else "."))
        return

    matches.sort(key=lambda m: m[0], reverse=True)
    print(f"{len(matches)} lesson(s) matching '{args.keyword}':")
    for sim, entry in matches:
        print(f"  - [{entry.get('lesson_type')}] {entry.get('item_description')}: {entry.get('lesson')} (confidence {entry.get('confidence')}%, match {sim:.2f})")


if __name__ == "__main__":
    main()
