#!/usr/bin/env python3
"""Check a candidate closet item against existing items for likely duplicates.

Used during Intake (DESIGN.md Module 4) before creating a new closet record,
so the same physical item entered via photo, receipt, and manual entry doesn't
become three separate records.

Usage:
  python3 closet_dedup.py --category jacket --brand "J.Crew" --color navy [--purchase-date 2026-06-01]

Prints candidate matches (existing item id + score + reason), highest first.
Exit code is always 0 — this is advisory, the calling conversation decides
whether a match is close enough to merge instead of create.
"""
import argparse
import json
import sys
from datetime import date, datetime
from difflib import SequenceMatcher
from pathlib import Path

CLOSET_PATH = Path(__file__).resolve().parent.parent / "profile" / "closet.json"

DATE_PROXIMITY_DAYS = 14


def text_similarity(a, b):
    if not isinstance(a, str) or not isinstance(b, str) or not a or not b:
        return 0.0
    return SequenceMatcher(None, a.strip().lower(), b.strip().lower()).ratio()


def colors_match(a, b):
    """Substring containment catches naming variants (navy / navy blue,
    brown / dark brown) that plain sequence similarity scores too low."""
    if not isinstance(a, str) or not isinstance(b, str) or not a or not b:
        return False, 0.0
    a_l, b_l = a.strip().lower(), b.strip().lower()
    if a_l in b_l or b_l in a_l:
        return True, 1.0
    sim = SequenceMatcher(None, a_l, b_l).ratio()
    return sim > 0.8, sim


def parse_date(s):
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def score_match(candidate, existing):
    existing_category = existing.get("category")
    if not isinstance(existing_category, str) or candidate["category"].strip().lower() != existing_category.strip().lower():
        return 0.0, []

    reasons = ["same category"]
    score = 0.3  # category match is necessary but not sufficient on its own

    brand_sim = text_similarity(candidate.get("brand"), existing.get("brand"))
    if brand_sim > 0.8:
        score += 0.4
        reasons.append(f"brand match ({brand_sim:.2f})")

    color_matched, color_sim = colors_match(candidate.get("color"), existing.get("color"))
    if color_matched:
        score += 0.2
        reasons.append(f"color match ({color_sim:.2f})")

    c_date = parse_date(candidate.get("purchase_date"))
    e_date = parse_date(existing.get("purchase_date"))
    if c_date and e_date:
        delta = abs((c_date - e_date).days)
        if delta <= DATE_PROXIMITY_DAYS:
            score += 0.1
            reasons.append(f"purchase dates within {delta} day(s)")

    return round(min(score, 1.0), 2), reasons


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--category", required=True)
    parser.add_argument("--brand", default="")
    parser.add_argument("--color", default="")
    parser.add_argument("--purchase-date", default="", help="YYYY-MM-DD")
    parser.add_argument("--threshold", type=float, default=0.5)
    args = parser.parse_args()

    candidate = {
        "category": args.category,
        "brand": args.brand,
        "color": args.color,
        "purchase_date": args.purchase_date,
    }

    if not CLOSET_PATH.exists():
        print("No closet.json found — nothing to compare against.")
        return

    with open(CLOSET_PATH) as f:
        closet = json.load(f)

    matches = []
    for item in closet:
        score, reasons = score_match(candidate, item)
        if score >= args.threshold:
            matches.append((score, item, reasons))

    matches.sort(key=lambda m: m[0], reverse=True)

    if not matches:
        print("No likely duplicates found.")
        return

    print(f"{len(matches)} possible match(es):")
    for score, item, reasons in matches:
        label = f"{item.get('brand', '?')} {item.get('color', '?')} {item.get('category', '?')}"
        print(f"  [{score}] id={item.get('id')} — {label} — {', '.join(reasons)}")


if __name__ == "__main__":
    main()
