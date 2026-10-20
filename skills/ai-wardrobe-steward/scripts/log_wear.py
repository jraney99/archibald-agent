#!/usr/bin/env python3
"""Log what the user wore, from his own words, without a calendar board.

  python3 log_wear.py --text "navy H&B polo and the stone johnnie-O shorts" [--date YYYY-MM-DD]
                      [--activity golf-round] [--occasion smart-casual] [--dry-run]
  python3 log_wear.py --items id1,id2 [...]        # when the ids are already known

WHY (2026-10-13): wear history was empty — 145 items, 0 wears. Not a broken writer:
`outfit_history.py log` works end to end. The only road to it was a calendar outfit
board, and triage is deliberately narrow (references/22), so 3 boards existed in a
month and all 3 were skipped. Everything he wears on an ordinary day was invisible,
which left cost-per-wear, the wear ceilings and "I want to wear my whole closet"
with nothing to measure. This is the ordinary-day road.

Matching is conservative: every phrase must land on exactly one active closet item.
An ambiguous or unknown phrase is REPORTED, never guessed — a wrong id is worse than
no record, and `outfit_history.py` bumps wear_count on whatever it is handed.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
from _store import load_json  # noqa: E402

CLOSET = ROOT / "profile" / "closet.json"
NOISE = {"the", "a", "an", "and", "my", "with", "i", "wore", "worn", "today", "yesterday", "it", "them",
         "plus", "then", "some", "that", "this", "to", "in", "on", "for", "was", "were", "under", "over",
         "pair", "of", "one", "new", "old", "same"}

# He does not type catalogue names. These are the words he actually uses.
BRAND_ALIASES = {
    "h&b": "holderness", "hb": "holderness", "holderness and bourne": "holderness & bourne",
    "johnnie-o": "johnnie", "johnnieo": "johnnie", "johnny o": "johnnie", "rl": "ralph lauren",
    "prl": "ralph lauren", "s&l": "state & liberty", "tecovas": "tecovas", "lulu": "lululemon",
}
COLOUR_WORDS = {
    "navy", "blue", "light blue", "royal", "black", "white", "cream", "ivory", "bone", "grey", "gray",
    "charcoal", "heather", "olive", "green", "spruce", "forest", "sage", "brown", "tan", "khaki", "camel",
    "cognac", "stone", "sand", "beige", "burgundy", "maroon", "red", "pink", "purple", "yellow", "orange",
    "teal", "aqua", "mint", "lavender", "mauve", "silver", "gold", "denim", "indigo",
}
# words that name a kind of garment but are not a vocab category
SUBTYPE_WORDS = {"quarter", "zip", "quarterzip", "1/4", "half", "pullover", "hoodie", "crewneck", "cardigan",
                 "chino", "chinos", "jogger", "joggers", "trouser", "trousers", "tee", "oxford", "ocbd",
                 "overshirt", "shacket", "chukka", "sneaker", "trainers", "parka", "puffer", "vest"}


def phrases(text: str) -> list:
    """Split his sentence into the garment phrases he named."""
    parts = re.split(r",| and | with | plus |\+|/|\n", text or "", flags=re.I)
    return [" ".join(p.split()).strip(" .;:") for p in parts if p and p.strip(" .;:")]


def tokens(s: str) -> set:
    return {w for w in re.findall(r"[a-z0-9&]+", (s or "").lower()) if w not in NOISE and len(w) > 1}


def _categories() -> list:
    import yaml
    v = yaml.safe_load((ROOT / "vocab.yaml").read_text())
    return [c for g in v["category_groups"].values() for c in g]


def named_category(phrase: str, cats: list) -> str:
    """The vocab category he named, if any. 'polo' in 'Polo Ralph Lauren' is a brand, not a category,
    so a category word immediately followed by 'ralph' does not count."""
    low = " " + re.sub(r"[^a-z0-9 ]", " ", (phrase or "").lower()) + " "
    low = re.sub(r"\s+", " ", low)
    for c in sorted(cats, key=len, reverse=True):
        word = c.replace("_", " ")
        for form in (word, word + "s", word.replace("-", " "), word.replace("-", "")):
            if f" {form} " in low and not re.search(rf" {re.escape(form)} (ralph|rl) ", low):
                return c
    if " jeans " in low or " denim " in low:
        return "jeans"
    return ""


def named_colours(phrase: str) -> set:
    low = re.sub(r"[^a-z ]", " ", (phrase or "").lower())
    return {c for c in COLOUR_WORDS if re.search(rf"\b{c}\b", low)}


def brand_hint(phrase: str) -> str:
    low = (phrase or "").lower()
    for alias, real in BRAND_ALIASES.items():
        if re.search(rf"(^|[^a-z]){re.escape(alias)}([^a-z]|$)", low):
            return real
    return ""


def item_text(it: dict) -> str:
    return " ".join(str(it.get(k) or "") for k in ("id", "brand", "model", "color", "category", "material", "size"))


def match_phrase(phrase: str, closet: list, cats: list = None) -> dict:
    """Field-aware: a colour, brand or category he NAMES must agree with the item.

    Loose token overlap matched a navy quarter-zip to "grey quarter zip" (2026-10-13),
    which would have logged a wear against the wrong garment — worse than no record.
    """
    cats = cats if cats is not None else _categories()
    want = tokens(phrase)
    if not want:
        return {"unknown": phrase}
    cat = named_category(phrase, cats)
    colours = named_colours(phrase)
    brand = brand_hint(phrase)
    active = [i for i in closet if i.get("lifecycle_state", "active") in ("active", "dirty")]
    scored = []
    for it in active:
        if cat and it.get("category") != cat:
            continue
        hay = item_text(it).lower() + " " + str(it.get("notes") or "").lower()[:200]
        if brand and brand not in hay:
            continue
        if colours and not any(re.search(rf"\b{c}\b", hay) for c in colours):
            continue
        score = (2 if cat else 0) + (2 * len(colours & set(re.findall(r"[a-z]+", hay)))) + (2 if brand else 0)
        # "quarter zip", "quarter-zip" and "quarterzip" are the same garment to him
        flat = re.sub(r"[^a-z0-9]", "", hay)
        named_sub = {w for w in want if w in SUBTYPE_WORDS}
        score += 2 * len({w for w in named_sub if w in flat})
        score += len(want & tokens(hay))
        if score:
            scored.append((score, it))
    if not scored:
        return {"unknown": phrase}
    top = max(s for s, _ in scored)
    best = [it for s, it in scored if s == top]
    # a bare category with nothing else is not an identification
    if not (cat or colours or brand):
        return {"ambiguous": [i["id"] for i in best][:6], "phrase": phrase}
    if len(best) > 1:
        return {"ambiguous": [i["id"] for i in best][:6], "phrase": phrase}
    return {"item": best[0]["id"], "label": f"{best[0].get('brand', '')} {best[0].get('color', '')} {best[0].get('category', '')}".strip(), "phrase": phrase}


def resolve(text: str, closet: list) -> dict:
    out = {"matched": [], "ambiguous": [], "unknown": []}
    cats = _categories()
    for p in phrases(text):
        r = match_phrase(p, closet, cats)
        if "item" in r:
            out["matched"].append(r)
        elif "ambiguous" in r:
            out["ambiguous"].append(r)
        else:
            out["unknown"].append(p)
    return out


def log(ids: list, occasion: str, activity: str, when: str, notes: str, run=subprocess.run):
    cmd = [sys.executable, str(SCRIPTS / "outfit_history.py"), "log", "--occasion", occasion,
           "--items", ",".join(ids), "--date", when, "--activity", activity]
    if notes:
        cmd += ["--notes", notes]
    r = run(cmd, capture_output=True, text=True)
    if getattr(r, "returncode", 0):
        sys.exit((r.stdout or "") + (r.stderr or ""))
    run([sys.executable, str(SCRIPTS / "mark_after_wear.py"), "--items", ",".join(ids)], capture_output=True, text=True)
    return r


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--text", help="his words: what he wore")
    ap.add_argument("--items", help="comma-separated closet ids, when already resolved")
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--activity", default="general")
    ap.add_argument("--occasion", default="casual")
    ap.add_argument("--notes", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if not (a.text or a.items):
        ap.error("--text or --items is required")
    closet = load_json(CLOSET, [])
    if a.items:
        ids = [x.strip() for x in a.items.split(",") if x.strip()]
        known = {i.get("id") for i in closet}
        missing = [i for i in ids if i not in known]
        if missing:
            sys.exit(f"Not closet ids: {', '.join(missing)}")
        res = {"matched": [{"item": i, "label": i, "phrase": i} for i in ids], "ambiguous": [], "unknown": []}
    else:
        res = resolve(a.text, closet)
        ids = [m["item"] for m in res["matched"]]
    res["date"], res["activity"], res["occasion"] = a.date, a.activity, a.occasion
    if ids and not a.dry_run:
        log(ids, a.occasion, a.activity, a.date, a.notes or (f"Typed: {a.text}" if a.text else ""))
        res["logged"] = True
    if a.json:
        print(json.dumps(res, indent=2)); return 0 if ids else 1
    if res["matched"]:
        print(("Would log" if a.dry_run else "Logged") + f" {len(ids)} item(s) for {a.date} ({a.activity}):")
        for m in res["matched"]:
            print(f"  • {m['label']} ({m['item']})")
    for amb in res["ambiguous"]:
        print(f"  ? \"{amb['phrase']}\" could be: {', '.join(amb['ambiguous'])} — say which, nothing logged for it")
    for u in res["unknown"]:
        print(f"  ? \"{u}\" is not in the closet — nothing logged for it")
    if not res["matched"]:
        print("Nothing matched; nothing logged.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
