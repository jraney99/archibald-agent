#!/usr/bin/env python3
"""Turn repeated retirement reasons into purchase lessons the shopping scan reads.

  python3 retirement_lessons.py [--json]        what the record now supports
  python3 retirement_lessons.py --propose       draft the new ones for confirmation

WHY (2026-10-13): culling is where his sharpest opinions show, and nothing captured why.
`closet_bulk_state.py --set-state retired` now requires a `--reason` from a fixed list, and
this is what that record is FOR. Without it the reasons would sit in the closet file as
unread as the free-text notes they replaced.

The old notes are deliberately not mined. Half of them said only "didn't fit" or nothing, and
seven entries for one brand were one sizing mistake written down seven times — reading them
would have produced a confident lesson to stop buying a brand he likes. The user chose to
start fresh, so only retirements carrying a structured reason count.

A lesson needs MIN_ITEMS pieces sharing a brand and a reason, because one retirement is an
incident and two is a pattern. `--propose` writes drafts to pending_purchase_lessons.json for
the normal confirm step; nothing reaches purchase_lessons.json unconfirmed.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from _store import load_json, save_json  # noqa: E402

CLOSET = ROOT / "profile" / "closet.json"
PENDING = ROOT / "profile" / "pending_purchase_lessons.json"
LESSONS = ROOT / "profile" / "purchase_lessons.json"
MIN_ITEMS = 2

LESSON_TYPE = {"doesnt-fit": "fit", "worn-out": "quality", "redundant": "redundancy",
               "off-palette": "colour", "never-wore": "taste", "style-changed": "taste", "replaced": "redundancy"}
PHRASING = {
    "doesnt-fit": "{brand} {cats} did not fit — {n} retired for fit. Treat {brand} sizing as a risk and confirm the cut before recommending one.",
    "worn-out": "{brand} {cats} wore out — {n} retired worn. Expect a short life there and weigh it against the price.",
    "redundant": "{brand} {cats} turned out redundant — {n} retired as duplicates. He already has this covered; do not add another.",
    "off-palette": "{brand} {cats} were retired as off-palette ({n}). The colours he bought there did not work; hold them to the palette strictly.",
    "never-wore": "{brand} {cats} were never worn ({n} retired unworn). They fit and he still did not reach for them — the strongest signal to stop buying this.",
    "style-changed": "He has moved on from {brand} {cats} ({n} retired). Do not surface them unless he asks.",
    "replaced": "{brand} {cats} kept being replaced ({n}). Buy the better version once instead of the cheaper one twice.",
}


def retirements(closet: list = None) -> list:
    closet = closet if closet is not None else load_json(CLOSET, [])
    return [i for i in closet if i.get("lifecycle_state") == "retired" and (i.get("retirement") or {}).get("reason")]


def patterns(closet: list = None, min_items: int = MIN_ITEMS) -> list:
    groups = defaultdict(list)
    for i in retirements(closet):
        brand = (i.get("brand") or "").strip()
        if brand:
            groups[(brand, i["retirement"]["reason"])].append(i)
    out = []
    for (brand, reason), items in groups.items():
        if len(items) < min_items:
            continue
        cats = sorted({(i.get("category") or "item").replace("_", " ") for i in items})
        out.append({"id": f"lesson-{brand.lower().replace(' ', '-').replace('&', 'and')}-{reason}",
                    "brand": brand, "reason": reason, "lesson_type": LESSON_TYPE.get(reason, "taste"),
                    "items": [i.get("id") for i in items], "categories": cats,
                    "lesson": PHRASING[reason].format(brand=brand, cats="/".join(cats), n=len(items)),
                    "notes": [n for n in ((i.get("retirement") or {}).get("note") for i in items) if n][:3]})
    return sorted(out, key=lambda x: -len(x["items"]))


def propose(found: list) -> list:
    known = {l.get("id") for l in load_json(LESSONS, [])} | {l.get("id") for l in load_json(PENDING, [])}
    fresh = [{"id": p["id"], "lesson_type": p["lesson_type"],
              "item_description": f"{p['brand']} {'/'.join(p['categories'])}",
              "linked_closet_item_id": None, "lesson": p["lesson"],
              "evidence": [{"date": date.today().isoformat(),
                            "note": f"{len(p['items'])} retired for '{p['reason']}': {', '.join(p['items'])}"}]}
             for p in found if p["id"] not in known]
    if fresh:
        save_json(PENDING, load_json(PENDING, []) + fresh)
    return fresh


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--propose", action="store_true", help="draft them for confirm_purchase_lesson.py")
    a = ap.parse_args(argv)
    found = patterns()
    if a.propose:
        made = propose(found)
        print(json.dumps({"drafted": [m["id"] for m in made],
                          "confirm_with": "python3 scripts/confirm_purchase_lesson.py --all"}, indent=2))
        return 0
    if a.json:
        print(json.dumps(found, indent=2)); return 0
    total = len(retirements())
    print(f"{total} retirement(s) carry a structured reason; {len(found)} pattern(s) at {MIN_ITEMS}+ items\n")
    for p in found:
        print(f"  {p['lesson']}\n    ({', '.join(p['items'])})")
    if not found:
        print("  Nothing yet — the record starts from 2026-10-13, and a pattern needs two.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
