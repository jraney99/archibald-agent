#!/usr/bin/env python3
"""Find a bought item in a store's own catalog: its official photo, price and colourway.

  python3 catalog_lookup.py --retailer "Holderness & Bourne" --item "The Mills Jogger" [--color navy] [--json]

WHY (2026-10-13): intake from an order email attaches a photo only when the model finds a
usable image URL in that email — often a tracking pixel, a thumbnail, or a link behind a
login. None of the user's 145 items came in that way. Since 2026-10-12 the wardrobe holds full
catalogs for 25 of his stores, each product carrying the store's own studio image, so the
item can be looked up instead of scraped.

Matching is conservative: the retailer must be a known store and the title must share
enough distinctive words. It returns nothing rather than a plausible wrong product, since
a wrong photo in the closet is worse than no photo.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent / "shared"))
sys.path.insert(0, str(ROOT / "scripts"))
import store_catalog as sc  # noqa: E402
from _stores import canonical, load_stores  # noqa: E402

STOP = {"the", "a", "and", "of", "in", "for", "with", "mens", "men", "men's", "size", "new", "shirt"}
# shade words, not colours: "Heathered Navy" and "Heathered Spruce" are different colourways,
# so matching on "heathered" picked the wrong jogger (2026-10-13).
SHADE = {"heathered", "heather", "light", "dark", "washed", "faded", "vintage", "classic", "solid"}
# Colour words are not distinctive: "liberty red white" shares three words with half the
# catalog. The MIN_SHARED test counts only words that are not a colour or a shade.
COLOUR = {"navy", "blue", "red", "white", "black", "grey", "gray", "charcoal", "green", "olive", "spruce",
          "brown", "tan", "khaki", "stone", "cream", "bone", "burgundy", "maroon", "liberty", "pink",
          "purple", "yellow", "orange", "teal", "aqua", "sand", "camel", "cognac", "silver", "gold", "mocha"}
MIN_SHARED = 2
# H&B and the golf brands sell licensed editions of the same garment. "The Jackson Hoodie:
# Liberty Red" and "University of Example Jackson Hoodie: Liberty Red" are different
# products, and the second has a crest on it (2026-10-13).
# "state" and "club" are deliberately absent: State & Liberty is a brand and "club" is all
# over golf gear. A licensed title is SKIPPED, not merely penalised — when the only match is
# a crested edition, the plain garment simply is not sold any more.
LICENSED = re.compile(r"\buniversity\b|\bcollege\b|\bryder cup\b|\bpga\b|\bu\.?s\.? open\b|\bmasters\b|\bcrest\b", re.I)


def words(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 2 and w not in STOP}


def find(retailer: str, item: str, colour: str = "", stores=None, cache=None) -> dict:
    """The catalog product this purchase is, or {'found': False, 'why': ...}."""
    stores = stores if stores is not None else load_stores()
    slug = canonical(retailer, stores)
    store = next((s for s in stores if s["slug"] == slug), None)
    if not store:
        return {"found": False, "why": f"'{retailer}' is not a known store"}
    if store.get("catalog") != "shopify":
        return {"found": False, "why": f"{store['name']} publishes no catalog"}
    cache = cache if cache is not None else sc.load_cache([slug])
    products = (cache.get(slug) or {}).get("products") or []
    if not products:
        return {"found": False, "why": f"no catalog pulled for {store['name']} yet"}
    want = words(item)
    cw = words(colour) - SHADE
    best, score = None, 0
    for p in products:
        if p.get("excluded"):
            continue
        shared = len(want & words(p.get("title", "")))
        if not shared:
            continue
        title_words = words(p.get("title", ""))
        s = shared * 2 + (2 if cw and cw & title_words else 0)
        if cw and any(cw & words(v.get("color", "")) for v in p.get("variants", [])):
            s += 2
        # A licensed variant ("University of Example Macdonald Shirt: Black") shares every
        # word of the plain product and used to win the tie. Extra words it does not have
        # are evidence it is a different product (2026-10-13).
        if LICENSED.search(p.get("title", "")) and not LICENSED.search(item or ""):
            continue
        s -= 0.75 * len(title_words - want - cw)
        if s > score:
            best, score = p, s
    distinctive = (want & words((best or {}).get("title", ""))) - COLOUR - SHADE
    # A named colourway must actually match: "Navy & Whiskey Dogleg" and "Navy & Slate
    # Microstripe" are both navy Fischer belts and are not the same belt (2026-10-13).
    named_shade = cw - COLOUR
    if best and named_shade:
        shown = words(best.get("title", "")) | {w for v in best.get("variants", []) for w in words(v.get("color", ""))}
        if not (named_shade & shown):
            return {"found": False, "why": f"'{colour}' is not a colourway {store['name']} lists for that product now"}
    if not best or len(distinctive) < MIN_SHARED:
        return {"found": False, "why": f"nothing in {store['name']}'s catalog matches '{item}'"}
    variant = next((v for v in best["variants"] if cw and cw & words(v.get("color", ""))), None) or next((v for v in best["variants"] if v.get("price")), None)
    return {"found": True, "store": slug, "retailer": store["name"], "title": best["title"], "url": best["url"],
            "image_url": best.get("image") or "", "colour": (variant or {}).get("color", ""),
            "price": (variant or {}).get("price"), "sizes_in_stock": [v["size"] for v in best["variants"] if v.get("available")]}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--retailer", required=True)
    ap.add_argument("--item", required=True)
    ap.add_argument("--color", default="")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    res = find(a.retailer, a.item, a.color)
    if a.json:
        print(json.dumps(res, indent=2))
    elif res["found"]:
        print(f"{res['retailer']}: {res['title']}" + (f" — {res['colour']}" if res["colour"] else "")
              + (f" — ${res['price']:.0f}" if res.get("price") else "") + f"\n  {res['url']}\n  image: {res['image_url'][:100]}")
    else:
        print(f"not found: {res['why']}")
    return 0 if res["found"] else 1


if __name__ == "__main__":
    sys.exit(main())
