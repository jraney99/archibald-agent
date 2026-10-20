#!/usr/bin/env python3
"""Turn his described Pinterest LOOKS into closet outfits the scorer can recognise.

  python3 pinned_looks.py [--json] [--limit 10]

WHY (2026-10-13): 59 pins had been described and `closet_looks.py` could already say which
of them his closet can make — but `outfit_score.py` had never heard of any of it, so the
thing that actually picks what he wears ignored the only record of what he likes the look
of. This is the join: each pinned look becomes a set of closet ids, and an outfit that
overlaps one scores higher.

Resemblance, not identity: a pinned look matched to three closet pieces is still a match
when two of them appear together, because what he pinned is a COMBINATION (navy knit over a
light collar, grey trousers) and the point is to favour that pairing, not to reproduce one
photograph. A look that maps to fewer than MIN_PIECES closet items is dropped — one piece
is not a look.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent / "shared"))
sys.path.insert(0, str(ROOT / "scripts"))
from data_paths import path as data_path  # noqa: E402
from _store import load_json  # noqa: E402
from closet_looks import match_piece  # noqa: E402

PINS = data_path("inspiration_pins.json")
CLOSET = ROOT / "profile" / "closet.json"
MIN_PIECES = 2
MAX_LOOKS = 200


def looks(pins: list = None, closet: list = None) -> list:
    """[{pin_id, link, summary, items:[closet ids], coverage}] for every pinned LOOK."""
    pins = pins if pins is not None else load_json(PINS, [])
    closet = closet if closet is not None else load_json(CLOSET, [])
    out = []
    for p in pins:
        d = p.get("description") or {}
        if d.get("kind") != "look":
            continue
        pieces = [x for x in d.get("pieces", []) if x.get("item")]
        if not pieces:
            continue
        ids = []
        for piece in pieces:
            hit = match_piece(piece, closet)
            if hit:
                ids.append(hit["id"])
        ids = list(dict.fromkeys(ids))
        if len(ids) < MIN_PIECES:
            continue
        out.append({"pin_id": p.get("pin_id"), "link": p.get("link"), "summary": d.get("summary", ""),
                    "items": ids, "coverage": round(len(ids) / len(pieces), 2)})
    return out[:MAX_LOOKS]


def overlap_bonus(item_ids, pinned: list, per_piece: int = 6, cap: int = 14) -> tuple:
    """How much a candidate outfit resembles anything he pinned: (bonus, reason or None).

    Scored on the BEST single pinned look, not the sum, so an item that happens to appear in
    many pins cannot stack a large bonus on its own."""
    best, best_hits = None, 0
    chosen = set(item_ids)
    for look in pinned:
        hits = len(chosen & set(look["items"]))
        if hits >= MIN_PIECES and hits > best_hits:
            best, best_hits = look, hits
    if not best:
        return 0, None
    bonus = min(cap, per_piece * best_hits)
    return bonus, {"delta": bonus, "source": "pinned-look", "pin_id": best["pin_id"],
                   "note": f"{best_hits} of these pieces appear together in a look he pinned"
                           + (f": {best['summary'][:60]}" if best.get("summary") else "")}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true"); ap.add_argument("--limit", type=int, default=10)
    a = ap.parse_args(argv)
    found = looks()
    if a.json:
        print(json.dumps(found, indent=2)); return 0
    print(f"{len(found)} pinned look(s) map onto {MIN_PIECES}+ closet pieces\n")
    for x in found[:a.limit]:
        print(f"  • {x['summary'][:62]}\n    {', '.join(x['items'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
