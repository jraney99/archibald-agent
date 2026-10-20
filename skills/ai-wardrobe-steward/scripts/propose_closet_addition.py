#!/usr/bin/env python3
"""A delivered clothing order becomes a one-tap closet question. Deterministic sender; no model text.

The user 2026-09-28: "new purchases cataloged from order emails, each confirmed with one tap".
The purchase watcher wakes a model only to READ the order email; the model hands the
fields here and this script drafts the closet record (pending_closet_additions.json,
same shape and checkpoint as any other intake), fetches the product photo when a URL
is given, and asks:

  📦 Delivered: {Brand} {item}, {colour}, size {L} ({Retailer}, ${price}). Add it to your closet?
  [✅ Add] [↩️ Returning it] [❌ Not clothing]

Asked once per order line; the answer edits this message in place (closet_addition_callback.py).

Usage:
  python3 propose_closet_addition.py --retailer "Holderness & Bourne" --brand "Holderness & Bourne" \
      --item "The Mason T-Shirt" --category t-shirt --color "heathered gray" --size L --price 55 \
      --order-id 12345 [--material "cotton"] [--sleeve short] [--image-url URL] [--purchase-date YYYY-MM-DD] [--email-id ID]
"""
import argparse
import hashlib
import json
import os
import re
import sys
import urllib.request
from datetime import date, datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
CLOSET = ROOT / "profile" / "closet.json"
PENDING = ROOT / "profile" / "pending_closet_additions.json"
PURCHASES = ROOT / "profile" / "purchase_lifecycle.json"
PHOTOS = ROOT / "profile" / "photos" / "closet"
VOCAB = ROOT / "vocab.yaml"
CHAT = "1000000001"
sys.path.insert(0, str(ROOT.parent / "shared"))
sys.path.insert(0, str(SCRIPTS))
from data_paths import path as data_path  # noqa: E402

STATE = Path(os.environ["WARDROBE_STATE_DIR"]) / "closet_purchase_prompts.json" if os.environ.get("WARDROBE_STATE_DIR") else data_path("closet_purchase_prompts.json")
# Sensible starting formality per category; flagged so a later conversation can correct it.
FORMALITY = {"t-shirt": 2, "polo": 3, "shirt": 3, "sweater": 3, "vest": 3, "jacket": 3, "coat": 3, "blazer": 4, "suit": 4,
             "pants": 3, "jeans": 2, "shorts": 2, "activewear": 1, "sneakers": 2, "boots": 3, "loafers": 3, "dress_shoes": 4,
             "athletic_shoes": 1, "specialty_footwear": 2, "belt": 3, "watch": 3, "sunglasses": 2, "wallet": 3, "bag": 3,
             "jewelry": 3, "hat": 2, "tie": 4, "scarf": 3, "socks": 2, "underwear": 1, "undershirt": 1, "sleepwear": 1}
TAGS = {1: ["athletic"], 2: ["casual"], 3: ["smart-casual"], 4: ["business-formal"], 5: ["black-tie"]}


def load(p, default):
    return json.loads(Path(p).read_text()) if Path(p).exists() else default


def slug(text):
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", text.lower())).strip("-")[:48]


def unique_id(base, taken):
    candidate, n = base, 2
    while candidate in taken:
        candidate, n = f"{base}-{n}", n + 1
    return candidate


def likely_duplicate(record, closet):
    """Same category and brand and a shared colour word: probably already catalogued."""
    words = set(re.findall(r"[a-z]+", (record.get("color") or "").lower())) - {"and", "with"}
    for item in closet:
        if item.get("category") == record["category"] and (item.get("brand") or "").lower() == (record.get("brand") or "").lower() \
                and words & set(re.findall(r"[a-z]+", (item.get("color") or "").lower())):
            return item.get("id")
    return None


def fetch_photo(url, item_id):
    if not url or os.environ.get("WARDROBE_NO_NETWORK") == "1":
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as response:
            kind = response.headers.get("Content-Type", "")
            data = response.read(8 * 1024 * 1024 + 1)
        if not kind.startswith("image/") or len(data) > 8 * 1024 * 1024:
            return None
        ext = ".png" if "png" in kind else ".webp" if "webp" in kind else ".jpg"
        PHOTOS.mkdir(parents=True, exist_ok=True)
        (PHOTOS / f"{item_id}{ext}").write_bytes(data)
        return f"profile/photos/closet/{item_id}{ext}"
    except Exception:  # noqa: BLE001 - a photo is a bonus, never a blocker
        return None


def question_text(a):
    parts = [" ".join(x for x in (a.brand, a.item) if x), a.color]
    if a.size:
        parts.append(f"size {a.size}")
    where = a.retailer + (f", ${a.price:,.0f}" if a.price else "")
    return f"📦 Delivered: {', '.join(p for p in parts if p)} ({where}). Add it to your closet?"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    for flag in ("--retailer", "--brand", "--item", "--category", "--color"):
        ap.add_argument(flag, required=True)
    ap.add_argument("--size", default="")
    ap.add_argument("--price", type=float, default=None)
    ap.add_argument("--order-id", default="")
    ap.add_argument("--material", default="")
    ap.add_argument("--sleeve", choices=["short", "long"], default=None)
    ap.add_argument("--image-url", default="")
    ap.add_argument("--purchase-date", default=None)
    ap.add_argument("--email-id", default="")
    a = ap.parse_args()
    vocab = yaml.safe_load(VOCAB.read_text())
    categories = {c for group in vocab["category_groups"].values() for c in group}
    if a.category not in categories:
        sys.exit(f"Unknown category '{a.category}'. Valid: {sorted(categories)}")
    closet, pending, state = load(CLOSET, []), load(PENDING, []), load(STATE, {})
    prompt_id = hashlib.sha256(f"{a.retailer}|{a.order_id}|{a.item}|{a.color}|{a.size}".encode()).hexdigest()[:12]
    if prompt_id in state:
        print(json.dumps({"already_asked": prompt_id, "status": state[prompt_id].get("status")})); return
    item_id = unique_id(slug(f"{a.brand} {a.item} {a.color}") or slug(f"{a.retailer} {a.category}"), {x.get("id") for x in closet + pending})
    formality = FORMALITY.get(a.category, 2)
    flagged = ["formality", "occasion_tags"] + [f for f, v in (("size", a.size), ("material", a.material), ("purchase_price", a.price)) if not v]
    record = {"id": item_id, "category": a.category, "brand": a.brand, "model": a.item, "color": a.color, "material": a.material or None,
              "size": a.size or None, "purchase_date": a.purchase_date or date.today().isoformat(), "purchase_price": a.price,
              "condition": "excellent", "season": "all-season", "formality": formality, "occasion_tags": TAGS[formality],
              "lifecycle_state": "active", "photo": None, "wear_count": 0, "estimated_frequency": None, "palette_match": None,
              "notes": f"From a {a.retailer} order email" + (f" (order {a.order_id})" if a.order_id else "") + ".",
              "added_via": "receipt", "added_date": date.today().isoformat()}
    if a.category in ("shirt", "t-shirt", "polo"):
        record["sleeve"] = a.sleeve or ("long" if a.category == "shirt" else "short")
        if not a.sleeve:
            flagged.append("sleeve")
    # 2026-10-13: an order email's image link is often a tracking pixel, a thumbnail or
    # behind a login — in practice none of the closet arrived that way. The store's own
    # catalog carries a studio photo, so it is the better source when we hold one.
    image_url, photo_source = a.image_url, "order email" if a.image_url else None
    catalog = {"found": False}
    if not os.environ.get("WARDROBE_NO_NETWORK"):
        try:
            from catalog_lookup import find as catalog_find
            catalog = catalog_find(a.retailer, f"{a.brand} {a.item}", a.color)
            if catalog.get("found") and catalog.get("image_url"):
                image_url, photo_source = catalog["image_url"], "store catalog"
        except Exception:  # noqa: BLE001 - a photo is a bonus, never a blocker
            catalog = {"found": False}
    record["photo"] = fetch_photo(image_url, item_id)
    if record["photo"] and photo_source:
        record["photo_source"] = photo_source
    if catalog.get("found"):
        record["product_url"] = catalog["url"]
        if record.get("purchase_price") is None and catalog.get("price"):
            record["purchase_price"] = catalog["price"]
            flagged = [f for f in flagged if f != "purchase_price"]
    duplicate = likely_duplicate(record, closet)
    record["review_summary"] = f"{a.brand} {a.item}, {a.color}" + (f" — might already be catalogued as {duplicate}" if duplicate else "")
    record["flagged_fields"] = flagged
    pending.append(record)
    PENDING.write_text(json.dumps(pending, indent=2))
    text = question_text(a) + (f"\nMight already be in your closet as {duplicate}." if duplicate else "")
    markup = {"inline_keyboard": [[{"text": "✅ Add", "callback_data": f"confirm:closetadd-{prompt_id}"},
                                   {"text": "↩️ Returning it", "callback_data": f"skip:closetadd-{prompt_id}-return"},
                                   {"text": "❌ Not clothing", "callback_data": f"skip:closetadd-{prompt_id}-notclothing"}]]}
    message_id = None
    if os.environ.get("WARDROBE_NO_SEND") != "1":
        from send_outfit_proposal import api
        receipt = api("sendMessage", {"chat_id": CHAT, "text": text, "reply_markup": markup})
        if not receipt.get("ok"):
            sys.exit("Telegram did not confirm delivery; the draft is queued but nothing was asked.")
        message_id = receipt.get("result", {}).get("message_id")
    purchase = next((p.get("id") for p in load(PURCHASES, []) if a.order_id and p.get("order_id") == a.order_id), None)
    state[prompt_id] = {"item_id": item_id, "label": f"{a.brand} {a.item}".strip(), "purchase_id": purchase, "order_id": a.order_id,
                        "email_id": a.email_id, "has_photo": bool(record["photo"]), "status": "asked", "text": text,
                        "source_tg_msg_id": message_id, "asked_at": datetime.now().astimezone().isoformat(timespec="seconds")}
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    print(json.dumps({"asked": prompt_id, "item_id": item_id, "text": text, "photo": record["photo"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
