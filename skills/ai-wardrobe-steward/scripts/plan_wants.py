#!/usr/bin/env python3
"""Which gaps in the wardrobe plan are NOT on the shopping list.

  python3 plan_wants.py [--json]            what is missing (reports only)
  python3 plan_wants.py --add [--priority P1]   create the wants

WHY (2026-10-13): `plan_progress.py` knows the holes in the plan and the watchlist is kept
by hand, so the two drifted — the scan was hunting for things he had already filled while
the P1 gaps sat unwatched. This is the join.

It REPORTS by default and only writes with --add, because the skill's own rule is that a
reported gap is not automatically a want (references/13): the offer is the checkpoint. A
plan row counts as covered when an active want shares its category AND one of the row's
distinguishing words, so "knit polos" is not considered covered by a plain golf-polo want.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
from _store import load_json  # noqa: E402
import plan_progress as pp  # noqa: E402

WATCHLIST = ROOT / "profile" / "shopping_watchlist.json"

# plan row (lower-cased start) -> (vocab category, words that distinguish the row, what to watch for)
PLAN_CATEGORY = {
    "casual button-ups": ("shirt", ["oxford", "ocbd", "chambray", "linen", "button"],
                          "Casual button-up: white or blue OCBD, university stripe, chambray, or linen. Not a dress shirt."),
    "dress shirts": ("shirt", ["dress"], "Dress shirt in his recorded neck/sleeve size (body_fit_profile), tailored fit."),
    "layering sweaters": ("sweater", ["fine-gauge", "fine gauge", "layer", "crewneck", "v-neck", "over a collar"],
                          "Fine-gauge sweater to layer OVER a collared shirt. Navy, spruce/forest, grey or burgundy."),
    "heavier knits": ("sweater", ["shawl", "rollneck", "cable", "chunky"], "Heavier knit: shawl cardigan or rollneck."),
    "knit polos": ("polo", ["knit", "ribbed", "open collar"],
                   "KNIT polo worn as a shirt with an open collar. Navy, bone, mocha or blue-slate."),
    "overshirt": ("jacket", ["overshirt", "shirt-jacket", "shirt jacket", "shacket"],
                  "Overshirt / shirt-jacket worn open over a tee or shirt. Cocoa, taupe suede or wool; or chambray / waxed cotton."),
    "field jacket": ("jacket", ["field", "waxed", "m-65"], "Field jacket, waxed or cotton, in sage, drab or olive."),
    "harrington": ("jacket", ["harrington", "blouson"], "Harrington or blouson jacket."),
    "denim / trucker": ("jacket", ["denim", "trucker"], "Denim or trucker jacket."),
    "odd jackets": ("blazer", ["blazer", "sport coat", "unstructured"], "Navy unstructured blazer."),
    "trousers": ("pants", ["chino", "tailored", "trouser"], "Tailored chino: cream/stone, or grey."),
    "jeans": ("jeans", ["jean", "denim", "straight"], "Modern straight-leg jean, mid-wash."),
    "casual chino shorts": ("shorts", ["chino", "flat-front", "flat front"], "Flat-front casual chino short, not athletic."),
    "dress shoes": ("dress_shoes", ["dress", "oxford", "derby"], "Dress shoe."),
}
PRIORITY_ORDER = {"P1": 0, "P2": 1, "P3": 2}


def covered(row_key: str, category: str, words: list, wants: list) -> list:
    """Active wants that plausibly already cover this plan row."""
    hits = []
    for w in wants:
        if w.get("status") != "active" or w.get("category") != category:
            continue
        text = " ".join([str(w.get("criteria", {}).get("notes") or ""), str(w.get("notes") or "")]).lower()
        if not words or any(re.search(rf"\b{re.escape(x)}", text) for x in words):
            hits.append(w["id"])
    return hits


def missing(md: str = None, closet: list = None, wants: list = None) -> list:
    md = md if md is not None else pp.PLAN.read_text()
    closet = closet if closet is not None else load_json(pp.CLOSET, [])
    wants = wants if wants is not None else load_json(WATCHLIST, [])
    res = pp.progress(md, closet)
    out = []
    for r in res["rows"]:
        if r["state"] != "open":
            continue
        key = next((k for k in PLAN_CATEGORY if r["category"].lower().startswith(k)), None)
        if not key:
            continue
        category, words, note = PLAN_CATEGORY[key]
        hits = covered(key, category, words, wants)
        out.append({"row": r["category"], "priority": r["priority"], "category": category,
                    "have": r.get("plan_have"), "target": r["target"], "gap": r.get("gap"),
                    "covered_by": hits, "note": note})
    out.sort(key=lambda x: (PRIORITY_ORDER.get(x["priority"][:2], 3), -(x["gap"] or 0)))
    return out


def add(rows: list, run=subprocess.run) -> list:
    created = []
    for r in rows:
        cmd = [sys.executable, str(SCRIPTS / "shopping_watchlist.py"), "add", "--category", r["category"],
               "--source", "gap-detected",
               "--notes", f"WARDROBE PLAN GAP ({r['priority']}): {r['row']} — {r['have']} of {r['target']}. {r['note']}"]
        res = run(cmd, capture_output=True, text=True)
        created.append({"row": r["row"], "ok": getattr(res, "returncode", 0) == 0,
                        "output": (getattr(res, "stdout", "") or "").strip()[:80]})
    return created


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--add", action="store_true", help="create a want for each uncovered gap")
    ap.add_argument("--priority", help="only this priority, e.g. P1")
    a = ap.parse_args(argv)
    rows = [r for r in missing() if not r["covered_by"]]
    if a.priority:
        rows = [r for r in rows if r["priority"].startswith(a.priority)]
    if a.add:
        print(json.dumps(add(rows), indent=2)); return 0
    if a.json:
        print(json.dumps(rows, indent=2)); return 0
    covered_rows = [r for r in missing() if r["covered_by"]]
    if rows:
        print(f"{len(rows)} plan gap(s) with nothing watching for them:\n")
        for r in rows:
            print(f"  {r['priority']:<9} {r['row'][:44]:46} {r['have']} of {r['target']}")
        print("\nAdd them with: plan_wants.py --add [--priority P1]")
    else:
        print("Every open plan gap already has a want.")
    if covered_rows:
        # shown, not hidden: the keyword test can over-match, and a wrong "covered" is how a
        # gap quietly stops being looked for.
        print(f"\nAlready watched ({len(covered_rows)}) — check these are really the same thing:")
        for r in covered_rows:
            print(f"  {r['priority']:<9} {r['row'][:40]:42} by {', '.join(r['covered_by'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
