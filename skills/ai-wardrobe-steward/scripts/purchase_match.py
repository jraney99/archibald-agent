#!/usr/bin/env python3
"""Close the loop: a real purchase marks the recommendation and the want that led to it.

Called by purchase_lifecycle.py `add` (a purchase read from an order email). Finds
the open recommendation whose retailer and words match, records user_action=bought,
and fulfils the first active want whose category and brand fit. Everything it
touches is a fact already true (he bought it), so no confirmation step.

  python3 purchase_match.py --retailer "Holderness & Bourne" --item "The Mills Jogger Navy L" [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from _store import load_json, save_json  # noqa: E402

DECISIONS = ROOT / "profile" / "purchase_decisions.json"
WATCHLIST = ROOT / "profile" / "shopping_watchlist.json"
STOP = {"the", "a", "and", "in", "of", "size", "navy", "black", "blue", "white", "grey", "gray", "shirt", "mens", "men"}


def words(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 2 and w not in STOP}


def guess_category(item: str, vocab_categories: list) -> str:
    try:
        from store_catalog import CATEGORY_WORDS
    except Exception:  # noqa: BLE001
        return ""
    order = sorted(CATEGORY_WORDS, key=lambda c: 0 if c in ("t-shirt", "sneakers", "boots", "loafers", "dress_shoes", "athletic_shoes", "belt", "hat", "socks") else 1)
    return next((c for c in order if c in vocab_categories and re.search("|".join(CATEGORY_WORDS[c]), item or "", re.I)), "")


def match_decision(decisions: list, retailer: str, item: str):
    """Best open 'buy' recommendation for this purchase: same retailer words + 2 shared item words."""
    rw, iw = words(retailer), words(item)
    best, score = None, 0
    for d in decisions:
        if d.get("user_action") not in (None, "unknown"):
            continue
        desc = d.get("item_description") or ""
        if rw and not (rw & words(desc)):
            continue
        s = len(iw & words(desc))
        if s > score:
            best, score = d, s
    return best if score >= 2 or (best and not iw) else None


def match_want(wants: list, retailer: str, category: str):
    for w in wants:
        if w.get("status") != "active" or (category and w.get("category") != category):
            continue
        brands = [b.lower() for b in (w.get("criteria", {}).get("brands") or [])]
        if not brands or any(b in retailer.lower() or retailer.lower() in b for b in brands):
            return w
    return None


def close_loop(retailer: str, item: str, category: str = "", decisions=None, wants=None, dry_run: bool = False) -> dict:
    decisions = decisions if decisions is not None else load_json(DECISIONS, [])
    wants = wants if wants is not None else load_json(WATCHLIST, [])
    import yaml
    vocab = yaml.safe_load((ROOT / "vocab.yaml").read_text())
    cats = [c for g in vocab["category_groups"].values() for c in g]
    category = category or guess_category(item, cats)
    out = {"retailer": retailer, "item": item, "category": category, "decision": None, "want": None}
    d = match_decision(decisions, retailer, item)
    if d:
        out["decision"] = d["id"]
        if not dry_run:
            d["user_action"] = "bought"
    w = match_want(wants, retailer, category)
    if w:
        out["want"] = w["id"]
        if not dry_run:
            w["status"] = "fulfilled"
            w["fulfilled_note"] = f"bought: {item} ({retailer})"
    if not dry_run:
        save_json(DECISIONS, decisions)
        save_json(WATCHLIST, wants)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--retailer", required=True); ap.add_argument("--item", required=True)
    ap.add_argument("--category", default=""); ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    print(json.dumps(close_loop(a.retailer, a.item, a.category, dry_run=a.dry_run), indent=2))


if __name__ == "__main__":
    main()
