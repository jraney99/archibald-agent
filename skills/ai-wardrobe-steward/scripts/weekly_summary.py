#!/usr/bin/env python3
"""One line for the Sunday briefing: what the wardrobe learned this week. Silent when nothing happened.

  👔 Wardrobe this week: 3 outfits worn (H&B polos twice) · skipped the grey trousers · no more outfits for Jordan.

Approved by the user 2026-09-28. Only weeks with at least one of: confirmed wears, pieces he
passed over (skips, swaps, "too …" taps), people or venues he switched off, people he
categorised, rules he accepted.

Usage:  python3 weekly_summary.py --brief [--today YYYY-MM-DD] [--days 7]
"""
import argparse
import json
import re
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROFILE = ROOT / "profile"
import sys  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
from proposal_caption import first_name, piece_label  # noqa: E402


def load(name, default):
    p = PROFILE / name
    return json.loads(p.read_text()) if p.exists() else default


def in_window(stamp, start, end):
    m = re.search(r"\d{4}-\d{2}-\d{2}", str(stamp or ""))
    return bool(m) and start <= date.fromisoformat(m.group(0)) <= end


def summary(today=None, days=7):
    today = today or date.today()
    start = today - timedelta(days=days - 1)
    closet = {x.get("id"): x for x in load("closet.json", [])}
    parts = []

    worn = [h for h in load("outfit_history.json", []) if in_window(h.get("date"), start, today)]
    if worn:
        pairs = Counter()
        for h in worn:
            for i in h.get("items", []):
                item = closet.get(i) or {}
                if item.get("category") in ("polo", "shirt", "t-shirt", "sweater", "blazer", "jacket"):
                    pairs[f"{item.get('brand') or 'unbranded'} {item.get('category')}s"] += 1
        repeat = next(((k, n) for k, n in pairs.most_common() if n >= 2), None)
        parts.append(f"{len(worn)} outfit{'s' if len(worn) != 1 else ''} worn" + (f" ({repeat[0]} {'twice' if repeat[1] == 2 else str(repeat[1]) + ' times'})" if repeat else ""))

    passed = Counter()
    verbs = {"skip": "skipped", "alternate": "skipped", "swapped": "swapped out", "too-formal": "called too formal", "too-casual": "called too casual",
             "too-warm": "found too warm", "too-cold": "found too cold", "unavailable": "flagged unavailable"}
    for s in load("style_signals.json", []):
        if s.get("source") == "outfit-proposal-response" and s.get("action") in verbs and in_window(s.get("date"), start, today):
            for i in s.get("items", []):
                passed[(verbs[s["action"]], i)] += 1
    if passed:
        (verb, item_id), _ = passed.most_common(1)[0]
        parts.append(f"{verb} the {piece_label(closet.get(item_id)) if closet.get(item_id) else item_id}")

    people = load("people.json", {}).get("people", {})
    off = [first_name(p) for p in people.values() if p.get("category") in ("no-outfits", "friend") and in_window(p.get("evidence"), start, today)]
    venues = [v.get("name") for v in load("venues.json", {}).values() if v.get("dressy") is False and v.get("name") and in_window(v.get("evidence"), start, today)]
    for name in off[:2]:
        parts.append(f"no more outfits for {name}")
    for name in venues[:1]:
        parts.append(f"no more outfits for {name}")
    tagged = [f"{first_name(p)} is {p['category']}" for p in people.values() if p.get("category") in ("business", "date", "family") and in_window(p.get("evidence"), start, today)]
    parts.extend(tagged[:2])
    rules = [f"new rule: {r['activity'].replace('-', ' ')} dresses {r['to']}" for r in load("rule_proposals.json", {}).values()
             if r.get("status") == "accepted" and in_window(r.get("answered_at"), start, today)]
    parts.extend(rules[:1])
    return ("👔 Wardrobe this week: " + " · ".join(parts) + ".") if parts else ""


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--brief", action="store_true")
    ap.add_argument("--today")
    ap.add_argument("--days", type=int, default=7)
    args = ap.parse_args()
    line = summary(date.fromisoformat(args.today) if args.today else None, args.days)
    if line:
        print(line)


if __name__ == "__main__":
    main()
