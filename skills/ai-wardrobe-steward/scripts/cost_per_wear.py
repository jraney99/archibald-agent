#!/usr/bin/env python3
"""Real cost-per-wear from what the user paid and what he actually wore.

  python3 cost_per_wear.py [--json] [--brand NAME] [--sell]

Wears per item = outfit_history entries naming the item + the closet's wear_count.
Cost-per-wear = purchase_price / wears. Also ranks brands by average CPW and, with
--sell, lists retired items with a price and no resale note. Read-only; says plainly
when there is no wear data yet (as of 2026-10-12 outfit_history is empty and every
wear_count is 0, so nothing here is meaningful until wear taps are logged).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from _store import load_json  # noqa: E402

CLOSET = ROOT / "profile" / "closet.json"
HISTORY = ROOT / "profile" / "outfit_history.json"


def wears_by_item(history) -> Counter:
    rows = history if isinstance(history, list) else history.get("entries", history.get("history", []))
    c = Counter()
    for h in rows:
        for i in h.get("items", []) or h.get("item_ids", []):
            c[i] += 1
    return c


def report(closet: list, history) -> dict:
    wears = wears_by_item(history)
    items, by_brand = [], defaultdict(list)
    for it in closet:
        price = it.get("purchase_price")
        n = wears.get(it.get("id"), 0) + int(it.get("wear_count") or 0)
        rec = {"id": it.get("id"), "brand": it.get("brand"), "category": it.get("category"), "color": it.get("color"),
               "price": price, "wears": n, "cpw": round(price / n, 2) if price and n else None, "state": it.get("lifecycle_state", "active")}
        items.append(rec)
        if price and n:
            by_brand[it.get("brand") or "?"].append(rec["cpw"])
    priced = [i for i in items if i["price"]]
    worn = [i for i in items if i["wears"]]
    brands = sorted(({"brand": b, "avg_cpw": round(sum(v) / len(v), 2), "items": len(v)} for b, v in by_brand.items()), key=lambda x: x["avg_cpw"])
    return {"items": len(items), "with_price": len(priced), "with_wears": len(worn),
            "no_wear_data": not worn,
            "best_value": sorted([i for i in items if i["cpw"]], key=lambda i: i["cpw"])[:10],
            "worst_value": sorted([i for i in items if i["cpw"]], key=lambda i: -i["cpw"])[:10],
            "never_worn_priced": [i for i in priced if not i["wears"] and i["state"] == "active"][:15],
            "brands": brands}


def sell_candidates(closet: list) -> list:
    return [{"id": i.get("id"), "label": f"{i.get('brand', '')} {i.get('color', '')} {i.get('category', '')}".strip(),
             "price_paid": i.get("purchase_price"), "notes": (i.get("notes") or "")[:100]}
            for i in closet if i.get("lifecycle_state") == "retired" and not re.search(r"sold|donat|gave away|trash", i.get("notes") or "", re.I)]


import re  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true"); ap.add_argument("--brand"); ap.add_argument("--sell", action="store_true")
    a = ap.parse_args(argv)
    closet = load_json(CLOSET, [])
    if a.sell:
        rows = sell_candidates(closet)
        print(json.dumps(rows, indent=2) if a.json else "\n".join(f"• {r['label']}" + (f" (paid ${r['price_paid']})" if r['price_paid'] else "") for r in rows) or "Nothing retired is waiting to be sold.")
        return 0
    res = report(closet, load_json(HISTORY, []))
    if a.brand:
        res["items_for_brand"] = [i for i in res["best_value"] + res["worst_value"] if (i["brand"] or "").lower() == a.brand.lower()]
    if a.json:
        print(json.dumps(res, indent=2)); return 0
    print(f"{res['items']} items, {res['with_price']} with a price, {res['with_wears']} with any wear recorded")
    if res["no_wear_data"]:
        print("No wear data yet: outfit_history is empty and wear_count is 0 everywhere. Log wears (the 'wore it' tap) and this becomes real.")
        return 0
    print("\nBest value (lowest $/wear)"); [print(f"  • {i['brand']} {i['color']} {i['category']}: ${i['cpw']}/wear ({i['wears']} wears)") for i in res["best_value"]]
    print("\nWorst value"); [print(f"  • {i['brand']} {i['color']} {i['category']}: ${i['cpw']}/wear ({i['wears']} wears)") for i in res["worst_value"]]
    print("\nBrands by avg $/wear"); [print(f"  • {b['brand']}: ${b['avg_cpw']} ({b['items']} items)") for b in res["brands"]]
    return 0


if __name__ == "__main__":
    sys.exit(main())
