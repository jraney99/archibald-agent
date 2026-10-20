#!/usr/bin/env python3
"""Pick the belt that belongs with a chosen outfit — or decide it needs none.

  python3 belt_pick.py --items <id,id,id> [--activity golf-round] [--occasion smart-casual] [--json]

WHY (2026-10-13): the Outfit Engine fills base_top, bottoms and footwear and nothing
else, so no board had ever named a belt. The user owns over a dozen, including woven golf
belts, and asked: "if i am playing a round of golf, should i not be
wearing a belt? There is no belt listed on this board." `accessories` already exists as
a slot in vocab.yaml; it was simply never filled.

Chosen AFTER the outfit, not as a fourth pool: a belt answers to the shoes and the
bottoms, and adding 13 more combinations to the ranking would multiply the search for
no gain. Returns nothing, with a reason, when the bottoms take no belt.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from _store import load_json  # noqa: E402

CLOSET = ROOT / "profile" / "closet.json"
NO_BELT_OCCASIONS = {"athletic", "loungewear"}
# He dresses for these himself, in workout clothes (vocab workout_activities).
# The user 2026-10-13, on a simulator board: "this doesnt even need this".
NO_BELT_ACTIVITIES = {"golf-simulator", "driving-range", "gym", "running"}
# bottoms that have no belt loops, said in his own closet notes
NO_LOOPS = re.compile(r"elastic|drawstring|jogger|sweat|training|athletic|pull-on|track|lounge|swim", re.I)
WOVEN = re.compile(r"woven|stretch|braided|ribbon|grosgrain|fischer", re.I)
DRESS = re.compile(r"dress belt|smooth|leather dress", re.I)
LEATHER_SHOE = {"dress_shoes", "loafers", "boots"}
BROWN = re.compile(r"brown|cognac|tan|tabaco|tobacco|chestnut|camel|oxblood|espresso|whiskey|saddle", re.I)
BLACK = re.compile(r"black", re.I)
LIGHT_SHOE = re.compile(r"white|cream|bone|light grey|light gray", re.I)


def text_of(item: dict) -> str:
    return " ".join(str(item.get(k) or "") for k in ("brand", "model", "color", "notes"))


def pick(items: list, closet: list, activity: str = "general", occasion: str = "casual") -> dict:
    by_id = {i.get("id"): i for i in closet}
    chosen = [by_id[i] for i in items if i in by_id]
    bottoms = next((i for i in chosen if i.get("category") in ("pants", "jeans", "shorts")), None)
    shoes = next((i for i in chosen if i.get("category") in LEATHER_SHOE | {"sneakers", "athletic_shoes", "specialty_footwear"}), None)
    if occasion in NO_BELT_OCCASIONS:
        return {"belt": None, "why": f"no belt for {occasion}"}
    if activity in NO_BELT_ACTIVITIES:
        return {"belt": None, "why": f"no belt for {activity} — workout clothes"}
    if not bottoms:
        return {"belt": None, "why": "no bottoms in the outfit"}
    if NO_LOOPS.search(text_of(bottoms)):
        return {"belt": None, "why": f"{bottoms.get('category')} has no belt loops"}
    if any(i.get("category") == "belt" for i in chosen):
        return {"belt": None, "why": "the outfit already has a belt"}
    belts = [b for b in closet if b.get("category") == "belt" and b.get("lifecycle_state", "active") == "active"]
    if not belts:
        return {"belt": None, "why": "no active belt in the closet"}
    shoe_text = text_of(shoes) if shoes else ""
    light_shoe = bool(LIGHT_SHOE.search(shoe_text))
    # a white sneaker with a black swoosh is a WHITE shoe; the trim is not the colour to match
    want_brown = bool(BROWN.search(shoe_text))
    want_black = bool(BLACK.search(shoe_text)) and not light_shoe
    sporty = activity == "golf-round" or occasion == "casual"
    ranked = []
    for b in belts:
        t = text_of(b)
        score = 0
        if sporty and WOVEN.search(t):
            score += 4                       # the Fischer belts are what he bought for golf
        if not sporty and DRESS.search(t):
            score += 4
        if want_brown and BROWN.search(t):
            score += 3                       # a belt answers to the shoes before anything else
        if want_black and BLACK.search(t):
            score += 3
        if shoes and shoes.get("category") in LEATHER_SHOE and WOVEN.search(t) and not sporty:
            score -= 2
        if light_shoe and BLACK.search(t):
            score -= 3                       # black belt under white golf shoes reads harsh, and is off his palette
        bottoms_colour = str(bottoms.get("color") or "").lower().split()[0] if bottoms.get("color") else ""
        if bottoms_colour and bottoms_colour in t.lower():
            score += 1
        ranked.append((score, b))
    ranked.sort(key=lambda x: (-x[0], x[1].get("id") or ""))
    best_score, best = ranked[0]
    if best_score <= 0:
        return {"belt": None, "why": "no belt clearly suits this outfit"}
    return {"belt": best.get("id"),
            "label": f"{best.get('brand') or ''} {str(best.get('color') or '').split(',')[0]} belt".strip(),
            "why": "matches the shoes" if (want_brown or want_black) else ("woven, for golf" if sporty else "dress belt")}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--items", required=True)
    ap.add_argument("--activity", default="general")
    ap.add_argument("--occasion", default="casual")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    res = pick([x.strip() for x in a.items.split(",") if x.strip()], load_json(CLOSET, []), a.activity, a.occasion)
    print(json.dumps(res, indent=2) if a.json else (f"{res['label']} ({res['belt']}) — {res['why']}" if res["belt"] else f"no belt: {res['why']}"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
