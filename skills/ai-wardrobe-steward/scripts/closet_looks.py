#!/usr/bin/env python3
"""Shop from the closet first: which pinned looks can the user already make?

  python3 closet_looks.py [--board fashion] [--json] [--limit 10]

For every described Pinterest LOOK, matches each piece (garment word + colour) against
active closet items. A look with every piece matched is "can make"; a look missing
exactly one piece is "one piece away" and that piece is a candidate want. Output is
ranked so the single missing piece that unlocks the most looks comes first. Reads
only; the agent relays it or uses it in the Sunday digest.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent / "shared"))
sys.path.insert(0, str(ROOT / "scripts"))
from data_paths import path as data_path  # noqa: E402
from _store import load_json  # noqa: E402
from store_catalog import CATEGORY_WORDS  # noqa: E402

PINS = data_path("inspiration_pins.json")
CLOSET = ROOT / "profile" / "closet.json"
COLOUR_FAMILIES = {
    "navy": ["navy", "dark blue", "midnight"], "blue": ["blue", "light blue", "sky", "chambray", "denim"],
    "white": ["white", "ivory", "cream", "off-white", "bone"], "cream": ["cream", "ivory", "bone", "stone", "ecru"],
    "grey": ["grey", "gray", "charcoal", "heather"], "charcoal": ["charcoal", "dark grey", "dark gray"],
    "black": ["black"], "brown": ["brown", "tan", "cognac", "chocolate", "mocha", "camel", "tobacco"],
    "tan": ["tan", "khaki", "camel", "sand", "beige", "stone"], "olive": ["olive", "army", "sage", "green"],
    "green": ["green", "olive", "forest", "spruce"], "burgundy": ["burgundy", "maroon", "wine", "oxblood"],
    "red": ["red", "cardinal", "burgundy"], "stone": ["stone", "cream", "khaki", "sand"],
}
GENERIC = {"shoes": ["sneakers", "loafers", "boots", "dress_shoes", "athletic_shoes"], "jacket": ["jacket", "coat", "blazer", "vest"],
           "trousers": ["pants", "jeans"], "pants": ["pants", "jeans"], "sweater": ["sweater"], "shirt": ["shirt", "polo"]}


def piece_category(item: str) -> list:
    order = sorted(CATEGORY_WORDS, key=lambda c: 0 if c in ("t-shirt", "sneakers", "boots", "loafers", "dress_shoes", "athletic_shoes", "belt", "hat", "socks", "sunglasses", "watch") else 1)
    cat = next((c for c in order if re.search("|".join(CATEGORY_WORDS[c]), item or "", re.I)), None)
    if cat:
        return GENERIC.get(cat, [cat])
    for g, cats in GENERIC.items():
        if re.search(rf"\b{g}\b", item or "", re.I):
            return cats
    return []


def colour_ok(wanted: str, have: str) -> bool:
    w = (wanted or "").lower().strip(); h = (have or "").lower()
    if not w:
        return True
    fam = COLOUR_FAMILIES.get(w.split()[-1], [w])
    return any(f in h for f in fam) or w in h


def match_piece(piece: dict, closet: list):
    cats = piece_category(piece.get("item", ""))
    if not cats:
        return None
    for it in closet:
        if it.get("lifecycle_state", "active") != "active" or it.get("category") not in cats:
            continue
        if colour_ok(piece.get("colour", ""), f"{it.get('color', '')} {it.get('notes', '')[:60]}"):
            return it
    return None


def analyse(pins: list, closet: list, board: str = None) -> dict:
    looks = [p for p in pins if (p.get("description") or {}).get("kind") == "look" and (not board or p.get("board") == board)]
    can, one_away, missing_counter = [], [], Counter()
    for p in looks:
        pieces = [x for x in p["description"].get("pieces", []) if x.get("item")]
        if not pieces:
            continue
        matched, missing = [], []
        for x in pieces:
            m = match_piece(x, closet)
            (matched if m else missing).append({"piece": x, "closet_item": (m or {}).get("id"), "closet_label": f"{(m or {}).get('brand', '')} {(m or {}).get('color', '')} {(m or {}).get('category', '')}".strip() if m else None})
        rec = {"pin_id": p["pin_id"], "link": p["link"], "summary": p["description"].get("summary", ""), "matched": matched, "missing": missing}
        if not missing:
            can.append(rec)
        elif len(missing) == 1:
            one_away.append(rec)
            x = missing[0]["piece"]; missing_counter[f"{x.get('colour', '')} {x.get('item', '')}".strip().lower()] += 1
    unlock = [{"piece": k, "unlocks_looks": n} for k, n in missing_counter.most_common(8)]
    return {"looks": len(looks), "can_make": can, "one_piece_away": one_away, "best_next_buys": unlock}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--board"); ap.add_argument("--json", action="store_true"); ap.add_argument("--limit", type=int, default=10)
    a = ap.parse_args(argv)
    res = analyse(load_json(PINS, []), load_json(CLOSET, []), a.board)
    if a.json:
        print(json.dumps(res, indent=2)); return 0
    print(f"{res['looks']} pinned looks: {len(res['can_make'])} you can make now, {len(res['one_piece_away'])} one piece away\n")
    print("👔 Can make now")
    for r in res["can_make"][:a.limit]:
        print(f"  • {r['summary']}\n    with: " + "; ".join(m["closet_label"] for m in r["matched"]))
    print("\n🧩 One piece away")
    for r in res["one_piece_away"][:a.limit]:
        x = r["missing"][0]["piece"]; print(f"  • {r['summary']} — missing {x.get('colour', '')} {x.get('item', '')}")
    print("\n🎯 Best next buys (unlocks the most looks)")
    for u in res["best_next_buys"]:
        print(f"  • {u['piece']} → {u['unlocks_looks']} look{'s' if u['unlocks_looks'] != 1 else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
