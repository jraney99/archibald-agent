#!/usr/bin/env python3
"""Store catalogs as data instead of page reads.

Every Shopify store publishes its catalog at /products.json: no login, no key, no
scraping, the same endpoint the storefront itself calls. It carries exact colourway
names, per-size stock and compare_at_price, so "a navy polo in L just went on sale" is
a fact the scan can state rather than "page loaded, colourway not verified".

  stores                                   registered stores and how each is read
  probe <domain>                           does this domain expose a public catalog?
  fetch  [--store SLUG | --all] [--refresh] refresh the cache (reused for 6 h otherwise)
  search --category CAT [--store SLUG] [--size L] [--color navy] [--max-price N]
         [--on-sale] [--in-stock] [--limit 20] [--json]
  changes [--store SLUG | --all] [--category CAT] [--size L] [--max-price N] [--mark] [--json]
         price drops, back-in-stock and new products since the last --mark

Reads config/stores.json (The user's stores) and keeps a throwaway cache in
state/store_catalog/<slug>.json (one file per store, via data_paths.new_file). Never buys anything.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent / "shared"))
sys.path.insert(0, str(ROOT / "scripts"))
from data_paths import new_file  # noqa: E402
from _store import load_json, save_json  # noqa: E402
from _stores import by_slug, load_stores  # noqa: E402

CACHE_DIR = new_file("ai-wardrobe-steward", "state", "store_catalog").parent / "store_catalog"   # one small file per store
# A nightly job (wardrobe-catalog-refresh, 04:20) keeps these fresh, so a scan should always
# find them cached. 30h means one missed night still does not push a Mon/Thu scan into
# downloading 25 catalogs inside its 240s budget — which is what the 6h window did (2026-10-13).
CACHE_MAX_AGE = timedelta(hours=30)
USER_AGENT = "Mozilla/5.0 (Macintosh) ArchibaldWardrobe/1.0 (personal shopping list; read-only)"
PAGE_SIZE = 250
MAX_PAGES = 20
PAGE_PAUSE_S = 0.8       # Shopify throttles /products.json per IP burst; 4 stores back-to-back hit 429 on 2026-10-12
STORE_PAUSE_S = 4.0      # and a 25-store --refresh hit 429 on most of them on 2026-10-13: the
                         # per-IP budget is shared across stores, so the gap BETWEEN stores matters more
RETRY_AFTER_DEFAULT_S = 5
RETRY_BACKOFF_S = (5, 20, 60)   # a throttled store is worth waiting for; losing it costs a whole catalog

# vocab.yaml category -> words that identify it in product_type / title / tags.
CATEGORY_WORDS = {
    "polo": [r"\bpolos?\b"],
    "shirt": [r"(?<![tT]-)(?<![tT] )\bshirts?\b", r"\bbutton[- ]?downs?\b", r"\boxford\b(?! ?(shoes?|brogues?))", r"\bovershirt\b"],
    "t-shirt": [r"\btees?\b", r"\bt-?shirts?\b"],
    "sweater": [r"\bsweaters?\b", r"\bpullovers?\b", r"\b(quarter|half|1/4|1/2)[- ]zip\b", r"\bcardigans?\b", r"\bcrewnecks?\b", r"\bknits?\b"],
    "vest": [r"\bvests?\b", r"\bgilets?\b"],
    "jacket": [r"\bjackets?\b", r"\bbombers?\b", r"\bwindbreakers?\b", r"\bshackets?\b"],
    "coat": [r"\bcoats?\b", r"\bparkas?\b", r"\btrench\b"],
    "blazer": [r"\bblazers?\b", r"\bsport ?coats?\b"],
    "suit": [r"\bsuits?\b"],
    "pants": [r"\bpants?\b", r"\btrousers?\b", r"\bchinos?\b", r"\bjoggers?\b"],
    "jeans": [r"\bjeans?\b", r"\bdenim\b"],
    "shorts": [r"\bshorts?\b(?![\s-]*sleeves?)"],   # "Short-Sleeve Henley" is not a pair of shorts
    "activewear": [r"\btraining\b", r"\bperformance\b", r"\bworkout\b", r"\bathletic\b"],
    "sneakers": [r"\bsneakers?\b", r"\btrainers?\b"],
    "boots": [r"\bboots?\b"],
    "loafers": [r"\bloafers?\b"],
    "dress_shoes": [r"\boxford shoes?\b", r"\bbrogues?\b", r"\bderby\b", r"\bdress shoes?\b"],
    "athletic_shoes": [r"\brunning shoes?\b", r"\bgolf shoes?\b"],
    "belt": [r"\bbelts?\b"], "hat": [r"\bhats?\b", r"\bcaps?\b"], "socks": [r"\bsocks?\b"],
    "wallet": [r"\bwallets?\b"], "bag": [r"\bbags?\b", r"\bduffle\b"], "scarf": [r"\bscarf\b", r"\bscarves\b"],
    "tie": [r"\bties?\b"], "sunglasses": [r"\bsunglasses\b"], "watch": [r"\bwatch(es)?\b"],
    "underwear": [r"\bunderwear\b", r"\bboxers?\b", r"\bbriefs?\b"], "undershirt": [r"\bundershirts?\b"],
    "sleepwear": [r"\bsleep\b", r"\bpajamas?\b", r"\blounge\b"],
}
EXCLUDE = re.compile(r"\b(women|womens|women's|ladies|girls?|boys?|kids?|youth|gift card)\b", re.I)

# Every store publishes structured "Key:Value" tags and a description, and until 2026-10-13
# both were discarded — so the scan could not see FABRIC, which is the user's first criterion
# ("quality fabrics, premium craftsmanship"). These are the keys worth keeping, normalised
# across stores: H&B says Material, Todd Snyder says Fabrication, Tecovas says filter-material.
ATTR_KEYS = {
    "material": "material", "fabrication": "material", "filter-material": "material", "fabric": "material",
    "fabric type": "material", "fit": "fit", "filter-fit": "fit",
    "sleeve length": "sleeve", "sleeve": "sleeve",
    "color": "colour_name", "colour": "colour_name", "filter-color": "colour_name", "color-family": "colour_family",
    "style": "style", "subtype": "subtype", "type": "kind", "fabric pattern": "pattern",
}
FABRIC_WORDS = re.compile(
    r"\b(merino|cashmere|lambswool|shetland|wool|pima|supima|organic cotton|cotton|linen|silk|cuprammonium|"
    r"tencel|lyocell|modal|bamboo|hemp|leather|suede|cowhide|calfskin|shearling|down|alpaca|mohair|"
    r"polyester|nylon|elastane|spandex|acrylic|rayon|viscose|poly)\b", re.I)
TAG_SPLIT = re.compile(r"\s*:\s*")
# Mined from the FULL description, not the 180-char blurb: on 2026-10-13 only 6% of products
# showed a fit word and 10% a texture word, because the detail sits past the first sentence
# of marketing copy. The matches are stored, the text is not, so the cache stays small.
BODY_SIGNALS = {
    "fit": re.compile(r"\b(slim|classic|athletic|relaxed|trim|tailored|standard|regular|straight|oversized|"
                      r"true[- ]to[- ]size|roomy|boxy|slim-straight)[- ]?fit(ting)?\b|\bfits?\s+(slim|relaxed|true to size|large|small)\b", re.I),
    "weight": re.compile(r"\b(lightweight|light-weight|midweight|mid-weight|heavyweight|heavy-weight|featherweight|substantial)\b", re.I),
    "texture": re.compile(r"\b(ribbed|rib-knit|cable[- ]?knit|cable|waffle|brushed|garment[- ]dyed|textured|herringbone|"
                          r"pique|jacquard|tipping|tipped|marled|boucle|corduroy|seersucker|oxford cloth|flannel)\b", re.I),
    "origin": re.compile(r"\bmade in ([A-Z][a-z]+(?: [A-Z][a-z]+)?)\b|\b(hand[- ]?sewn|hand[- ]?finished|goodyear[- ]?welt\w*)\b", re.I),
    "intent": re.compile(r"\b(golf|course|fairway|on-course|performance|moisture[- ]wicking|four[- ]way stretch|upf)\b", re.I),
}


def attributes(raw: dict) -> dict:
    """Structured facts a store publishes about a product: material, fit, sleeve, colour family.

    Tag keys differ per store, so they are normalised through ATTR_KEYS. Fabric words found in
    the description are kept too, because a tag often says "Cotton-Blend" while the copy says
    which fibres."""
    out = {}
    for t in raw.get("tags", []) or []:
        if ":" not in t:
            continue
        key, _, value = t.partition(":")
        field = ATTR_KEYS.get(key.strip().lower())
        if field and value.strip():
            out.setdefault(field, []).append(value.strip()[:40])
    body = re.sub(r"<[^>]+>", " ", raw.get("body_html") or "")
    body = " ".join(body.split())
    fibres = {m.group(0).lower() for m in FABRIC_WORDS.finditer(body)}
    if fibres:
        out["fibres"] = sorted(fibres)[:8]
    for field, pattern in BODY_SIGNALS.items():
        found = sorted({(m.group(0) or "").strip().lower() for m in pattern.finditer(body)})
        if found:
            out.setdefault(field, [])
            out[field] = sorted(set(out[field]) | set(found))[:6]
    if body:
        out["blurb"] = body[:180]
    return {k: (v if not isinstance(v, list) or k == "fibres" else sorted(set(v))[:4]) for k, v in out.items()}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def product_url(domain: str, handle: str) -> str:
    return f"https://{domain}/products/{handle}"


def cart_url(domain: str, variant_id, qty: int = 1) -> str:
    """Shopify cart permalink: lands on checkout with this variant in the cart, no API.
    Verified 2026-10-12 on H&B (302 to the checkout). The agent never pays; the user does."""
    return f"https://{domain}/cart/{variant_id}:{qty}"


def normalise(raw: dict, domain: str) -> dict:
    """One Shopify product -> the compact record the cache keeps."""
    opts = {o["name"].strip().lower(): o["position"] for o in raw.get("options", [])}
    color_pos = next((p for n, p in opts.items() if n in ("color", "colour")), None)
    size_pos = next((p for n, p in opts.items() if n == "size"), None)
    width_pos = next((p for n, p in opts.items() if n == "width"), None)   # boots: Size 10 + Width D -> "10 D"
    length_pos = next((p for n, p in opts.items() if n in ("length", "inseam")), None)   # pants: 34 + 32 -> "34x32"
    variants = []
    for v in raw.get("variants", []):
        def opt(pos):
            return (v.get(f"option{pos}") or "").strip() if pos else ""
        price = float(v["price"]) if v.get("price") not in (None, "") else None
        cmp = float(v["compare_at_price"]) if v.get("compare_at_price") not in (None, "") else None
        size = opt(size_pos) + (f" {opt(width_pos)}" if width_pos and opt(width_pos) else "") \
            + (f"x{opt(length_pos)}" if length_pos and opt(length_pos) else "")
        variants.append({"id": v.get("id"), "sku": v.get("sku") or "", "color": opt(color_pos),
                         "size": size, "price": price,
                         "compare_at": cmp if cmp and price is not None and cmp > price else None,
                         "available": bool(v.get("available"))})
    image = (raw.get("images") or [{}])[0].get("src", "")
    # a "womens"/"kids" tag is reliable, so exclusion is decided here once and the
    # tags themselves are not kept (they were a third of the cache)
    haystack = " ".join([raw.get("product_type", ""), raw.get("title", "")] + list(raw.get("tags", [])))
    return {"id": raw.get("id"), "handle": raw.get("handle", ""), "title": raw.get("title", ""),
            "product_type": raw.get("product_type", ""), "excluded": bool(EXCLUDE.search(haystack)),
            "url": product_url(domain, raw.get("handle", "")), "image": image, "variants": variants,
            "attrs": attributes(raw)}


def fetch_products(domain: str, opener=None) -> list:
    """All products of a Shopify store, normalised. Raises urllib errors on failure."""
    opener = opener or _http_json
    out = []
    for page in range(1, MAX_PAGES + 1):
        if page > 1 and opener is _http_json:
            time.sleep(PAGE_PAUSE_S)
        url = f"https://{domain}/products.json?limit={PAGE_SIZE}&page={page}"
        try:
            data = opener(url)
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or opener is not _http_json:
                raise
            # the store said "slow down": wait what it asked for, once, then give up loudly
            wait = exc.headers.get("Retry-After") if exc.headers else None
            data = None
            for pause in RETRY_BACKOFF_S:
                time.sleep(float(wait) if wait and str(wait).isdigit() else pause)
                try:
                    data = opener(url)
                    break
                except urllib.error.HTTPError as again:
                    if again.code != 429:
                        raise
                    wait = None
            if data is None:
                raise
        products = data.get("products", [])
        out.extend(normalise(p, domain) for p in products)
        if len(products) < PAGE_SIZE:
            break
    return out


def _http_json(url: str) -> dict:
    """GET a JSON document, via curl when it is installed.

    Cloudflare in front of Faherty, State & Liberty and Todd Snyder answers Python's
    own HTTP client with a 429 "challenge" page while the same request from curl, same
    headers, gets the catalog (2026-10-12). We identify ourselves the same way either
    route and never solve a challenge: a challenge or block is recorded as such and the
    store is left alone until the next scheduled scan.
    """
    curl = shutil.which("curl")
    if curl:
        r = subprocess.run([curl, "-sSL", "--max-time", "25", "-A", USER_AGENT, "-H", "Accept: application/json",
                            "-w", "\n%{http_code}", url], capture_output=True, text=True)
        if r.returncode != 0:
            raise urllib.error.URLError(r.stderr.strip()[:120] or f"curl exit {r.returncode}")
        body, _, code = r.stdout.rpartition("\n")
        if code != "200":
            raise urllib.error.HTTPError(url, int(code or 0), "curl", {}, None)
        return json.loads(body)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=25) as resp:
        body = resp.read().decode("utf-8", "replace")
    return json.loads(body)


def probe(domain: str, opener=None) -> dict:
    try:
        data = (opener or _http_json)(f"https://{domain}/products.json?limit=1")
        ok = isinstance(data, dict) and "products" in data
        return {"domain": domain, "shopify_catalog": ok, "sample": (data.get("products") or [{}])[0].get("title", "") if ok else ""}
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, OSError) as exc:
        return {"domain": domain, "shopify_catalog": False, "error": str(exc)[:120]}


# ---------- cache ----------

def _cache_file(slug: str) -> Path:
    return CACHE_DIR / f"{slug}.json"


def load_cache(slugs=None) -> dict:
    """Per-store cache files; only the stores asked for are read."""
    if slugs is None:
        slugs = [f.stem for f in CACHE_DIR.glob("*.json")] if CACHE_DIR.exists() else []
    return {slug: load_json(_cache_file(slug), {}) for slug in slugs}


def save_cache(cache: dict) -> None:
    for slug, entry in cache.items():
        save_json(_cache_file(slug), entry, indent=None)


def refresh(store: dict, cache: dict, force: bool = False, opener=None) -> dict:
    """Fetch one store into the cache when stale. Returns {slug, status, products, note}."""
    slug = store["slug"]
    entry = cache.setdefault(slug, {})
    fetched = entry.get("fetched_at")
    if not force and fetched and _now() - datetime.fromisoformat(fetched) < CACHE_MAX_AGE:
        return {"slug": slug, "status": "cached", "products": len(entry.get("products", []))}
    try:
        products = fetch_products(store["domain"], opener)
    except urllib.error.HTTPError as exc:
        return {"slug": slug, "status": "blocked" if exc.code in (401, 403, 429) else "parse-error", "note": f"HTTP {exc.code}"}
    except (urllib.error.URLError, OSError) as exc:
        return {"slug": slug, "status": "timeout", "note": str(exc)[:120]}
    except ValueError as exc:
        return {"slug": slug, "status": "parse-error", "note": str(exc)[:120]}
    entry.update({"fetched_at": _now().isoformat(), "domain": store["domain"], "products": products})
    entry.setdefault("baseline", {})
    record_prices(entry, products)
    return {"slug": slug, "status": "ok", "products": len(products)}


HISTORY_CAP = 40


def record_prices(entry: dict, products: list, today: str = None) -> int:
    """Append today's price for every variant whose price changed since the last record.
    One [date, price] pair per change, so a stable price costs nothing."""
    today = today or _now().date().isoformat()
    hist = entry.setdefault("price_history", {})
    changed = 0
    for p in products:
        for v in p["variants"]:
            if v["price"] is None:
                continue
            h = hist.setdefault(str(v["id"]), [])
            if not h or h[-1][1] != v["price"]:
                if h and h[-1][0] == today:
                    h[-1] = [today, v["price"]]
                else:
                    h.append([today, v["price"]])
                    changed += 1
                del h[:-HISTORY_CAP]
    return changed


def _history_prices(entry: dict, group: list, days: int = 90, today=None) -> list:
    """Prices seen for these variants in the last `days`, plus the price in force at the window start."""
    from datetime import date as _date
    today = today or _now().date()
    cutoff = (today - timedelta(days=days)).isoformat()
    out = []
    for v in group:
        h = (entry.get("price_history") or {}).get(str(v["id"])) or []
        before = [x for x in h if x[0] < cutoff]
        if before:
            out.append(before[-1][1])
        out += [x[1] for x in h if x[0] >= cutoff]
    return out


def low_90d(entry: dict, group: list):
    prices = _history_prices(entry, group)
    return min(prices) if prices else None


def is_90d_low(entry: dict, group: list) -> bool:
    """True when today's price is the lowest seen in 90 days AND there is at least a
    week of history (otherwise every first fetch would be a "low")."""
    cur = [v["price"] for v in group if v["price"] is not None]
    if not cur:
        return False
    hist = entry.get("price_history") or {}
    oldest = min((h[0][0] for v in group for h in [hist.get(str(v["id"])) or []] if h), default=None)
    if not oldest or (_now().date() - datetime.fromisoformat(oldest).date()).days < 7:
        return False
    prices = _history_prices(entry, group)
    return bool(prices) and min(cur) <= min(prices)


# ---------- search ----------

def category_re(category: str):
    words = CATEGORY_WORDS.get(category)
    if not words:
        sys.exit(f"No catalog words for category '{category}'. Known: {sorted(CATEGORY_WORDS)}")
    return re.compile("|".join(words), re.I)


def _attr_text(product: dict) -> str:
    a = product.get("attrs") or {}
    return " ".join(str(v) for v in a.values()).lower()


def matches(product: dict, category: str = None, size: str = None, color: str = None,
            max_price: float = None, on_sale: bool = False, in_stock: bool = False,
            material: str = None, fit: str = None, texture: str = None) -> list:
    """Variants of `product` that satisfy the filters (empty list = no match)."""
    # exclusion was decided from the tags at fetch time; the category is read from the
    # type and title only, since merchandising tags like "knit" sit on shorts too
    if product.get("excluded"):
        return []
    if category and not category_re(category).search(f"{product.get('product_type', '')} {product.get('title', '')}"):
        return []
    if material and material.lower() not in _attr_text(product) + " " + product.get("title", "").lower():
        return []
    if fit and fit.lower() not in _attr_text(product):
        return []
    if texture and texture.lower() not in _attr_text(product) + " " + product.get("title", "").lower():
        return []
    out = []
    for v in product["variants"]:
        if size and v["size"].lower() != size.lower() and not v["size"].lower().startswith(size.lower() + "x"):
            continue
        if color and color.lower() not in v["color"].lower():
            continue
        if max_price is not None and (v["price"] is None or v["price"] > max_price):
            continue
        if on_sale and not v["compare_at"]:
            continue
        if in_stock and not v["available"]:
            continue
        out.append(v)
    return out


def search(cache: dict, stores: list, **filters) -> list:
    """Matching products grouped per product + colourway."""
    rows = []
    for store in stores:
        entry = cache.get(store["slug"]) or {}
        for p in entry.get("products", []):
            vs = matches(p, **filters)
            if not vs:
                continue
            by_color = {}
            for v in vs:
                by_color.setdefault(v["color"], []).append(v)
            for color, group in by_color.items():
                price = min(v["price"] for v in group if v["price"] is not None) if any(v["price"] is not None for v in group) else None
                cmp = max((v["compare_at"] or 0) for v in group) or None
                domain = (cache.get(store["slug"]) or {}).get("domain") or store.get("domain", "")
                wanted = next((v for v in group if v["available"] and filters.get("size") and
                               (v["size"].lower() == filters["size"].lower() or v["size"].lower().startswith(filters["size"].lower() + "x"))), None)
                rows.append({"store": store["slug"], "retailer": store["name"], "title": p["title"],
                             "product_type": p["product_type"], "color": color,
                             "variant_ids": {v["size"]: v["id"] for v in group if v["available"]},
                             "cart_url": cart_url(domain, wanted["id"]) if wanted else None,
                             "low_90d": low_90d(entry, group), "is_90d_low": is_90d_low(entry, group),
                             "sizes_in_stock": list(dict.fromkeys(v["size"] for v in group if v["available"])),
                             "sizes_out": list(dict.fromkeys(v["size"] for v in group if not v["available"])),
                             "price": price, "compare_at": cmp,
                             "sale_pct": round(100 * (1 - price / cmp)) if cmp and price else None,
                             "material": ", ".join((p.get("attrs") or {}).get("fibres", [])[:3]) or ", ".join((p.get("attrs") or {}).get("material", [])[:2]),
                             "fit": ", ".join((p.get("attrs") or {}).get("fit", [])[:2]),
                             "texture": ", ".join((p.get("attrs") or {}).get("texture", [])[:3]),
                             "weight": ", ".join((p.get("attrs") or {}).get("weight", [])[:1]),
                             "origin": ", ".join((p.get("attrs") or {}).get("origin", [])[:1]),
                             "url": p["url"], "image": p["image"], "product_id": p["id"],
                             "sku": next((v["sku"] for v in group if v["sku"]), "")})
    rows.sort(key=lambda r: (-(r["sale_pct"] or 0), r["price"] or 1e9))
    return rows


# ---------- changes ----------

def variant_key(store_slug: str, v: dict) -> str:
    return f"{store_slug}:{v['id']}"


def changes(cache: dict, stores: list, category=None, size=None, max_price=None, mark=False) -> list:
    """Events since the last --mark: price_drop, back_in_stock, new_product.

    A product never seen in the baseline is `new_product`; a variant whose price fell
    below the baseline price is `price_drop`; one that was unavailable and is now
    available is `back_in_stock`. --mark records the current state as the new baseline.
    Only variants matching the filters are considered, so a size can be pinned.
    """
    events = []
    for store in stores:
        entry = cache.get(store["slug"]) or {}
        baseline = entry.get("baseline") or {}
        seen_products = set(baseline.get("_products", []))
        current = {}
        for p in entry.get("products", []):
            vs = matches(p, category=category, size=size, max_price=max_price)
            for v in vs:
                k = variant_key(store["slug"], v)
                current[k] = {"price": v["price"], "available": v["available"]}
                prior = baseline.get(k)
                base = {"store": store["slug"], "retailer": store["name"], "title": p["title"], "color": v["color"],
                        "size": v["size"], "price": v["price"], "compare_at": v["compare_at"], "url": p["url"], "image": p["image"]}
                if p["id"] not in seen_products and seen_products:
                    if v["available"]:
                        events.append({"event": "new_product", **base})
                elif prior:
                    if v["price"] is not None and prior.get("price") is not None and v["price"] < prior["price"]:
                        events.append({"event": "price_drop", "was": prior["price"], **base})
                    if v["available"] and not prior.get("available"):
                        events.append({"event": "back_in_stock", **base})
                    if v["available"] and is_90d_low(entry, [v]) and v["price"] < (prior.get("price") or 1e9):
                        events.append({"event": "ninety_day_low", "was": prior.get("price"), **base})
        if mark:
            new_base = dict(baseline)
            new_base.update(current)
            new_base["_products"] = sorted({p["id"] for p in entry.get("products", []) if p.get("id") is not None} | seen_products)
            entry["baseline"] = new_base
    # one line per product+colour+event; collapse sizes
    merged = {}
    for e in events:
        k = (e["event"], e["store"], e["title"], e["color"], e.get("was"))
        m = merged.setdefault(k, {**e, "sizes": []})
        m["sizes"].append(e["size"])
    out = []
    for m in merged.values():
        m.pop("size", None)
        out.append(m)
    return out


# ---------- CLI ----------

def _print_rows(rows: list, limit: int) -> None:
    if not rows:
        print("No matches.")
        return
    for r in rows[:limit]:
        sale = f"  SALE {r['sale_pct']}% (was ${r['compare_at']:.0f})" if r.get("sale_pct") else ""
        sizes = ",".join(r["sizes_in_stock"]) or "none"
        low = "  90-DAY LOW" if r.get("is_90d_low") else ""
        cart = f"\n   cart: {r['cart_url']}" if r.get("cart_url") else ""
        spec = " · ".join(x for x in (r.get("material"), r.get("fit"), r.get("texture"), r.get("weight"), r.get("origin")) if x)
        print(f"{r['retailer']}: {r['title']} — {r['color'] or 'n/a'} — ${r['price']:.0f}{sale}{low} — in stock: {sizes}"
              + (f"\n   {spec}" if spec else "") + f"\n   {r['url']}{cart}")
    if len(rows) > limit:
        print(f"... {len(rows) - limit} more")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("stores")
    p = sub.add_parser("probe"); p.add_argument("domain")
    p = sub.add_parser("fetch"); p.add_argument("--store"); p.add_argument("--all", action="store_true"); p.add_argument("--refresh", action="store_true")
    p.add_argument("--stalest", type=int, metavar="N",
                   help="refresh only the N least recently fetched stores. Refreshing all 25 in one burst is "
                        "rate-limited whatever the spacing (12 of 25 were blocked on 2026-10-13): the per-IP budget "
                        "is shared, so the fix is fewer stores more often, not a longer pause.")
    p = sub.add_parser("search"); p.add_argument("--category", required=True); p.add_argument("--store"); p.add_argument("--size")
    p.add_argument("--color"); p.add_argument("--max-price", type=float); p.add_argument("--on-sale", action="store_true")
    p.add_argument("--in-stock", action="store_true"); p.add_argument("--limit", type=int, default=20); p.add_argument("--json", action="store_true")
    p.add_argument("--material", help="fibre or fabric word, e.g. merino, cashmere, linen")
    p.add_argument("--fit", help="fit word the store publishes, e.g. slim, classic, athletic")
    p.add_argument("--texture", help="construction word, e.g. ribbed, cable, waffle, brushed — his golf rule is texture over pattern")
    p = sub.add_parser("changes"); p.add_argument("--store"); p.add_argument("--all", action="store_true"); p.add_argument("--category")
    p.add_argument("--size"); p.add_argument("--max-price", type=float); p.add_argument("--mark", action="store_true"); p.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    stores = load_stores()
    shopify = [s for s in stores if s.get("catalog") == "shopify" and s.get("enabled", True)]
    if a.cmd == "stores":
        for s in stores:
            print(f"{s['slug']:<20} {s.get('catalog','page'):<8} {s.get('domain') or '-':<28} {s['name']}")
        return
    if a.cmd == "probe":
        print(json.dumps(probe(a.domain.replace('https://', '').replace('http://', '').strip('/')), indent=2)); return

    def chosen():
        if getattr(a, "store", None):
            s = by_slug(a.store, stores)
            if s.get("catalog") != "shopify":
                sys.exit(f"'{a.store}' is a '{s.get('catalog')}' source; only shopify stores have a catalog.")
            return [s]
        return shopify

    cache = load_cache([s["slug"] for s in chosen()])
    if a.cmd == "fetch" and a.stalest:
        def age(store):
            e = cache.get(store["slug"]) or {}
            f = e.get("fetched_at")
            return datetime.min.replace(tzinfo=timezone.utc) if not f else datetime.fromisoformat(f)
        picked = sorted(chosen(), key=age)[:a.stalest]
        results = []
        for i, st in enumerate(picked):
            if i:
                time.sleep(STORE_PAUSE_S)
            results.append(refresh(st, cache, force=True))
        save_cache(cache)
        print(json.dumps(results, indent=2)); return
    if a.cmd == "fetch":
        results = []
        for i, s in enumerate(chosen()):
            if i:
                time.sleep(STORE_PAUSE_S)
            results.append(refresh(s, cache, force=a.refresh))
        save_cache(cache)
        print(json.dumps(results, indent=2)); return
    if a.cmd == "search":
        sel = chosen()
        for s in sel:
            refresh(s, cache)
        save_cache(cache)
        rows = search(cache, sel, category=a.category, size=a.size, color=a.color, max_price=a.max_price,
                      on_sale=a.on_sale, in_stock=a.in_stock, material=a.material, fit=a.fit, texture=a.texture)
        print(json.dumps(rows[:a.limit], indent=2)) if a.json else _print_rows(rows, a.limit); return
    if a.cmd == "changes":
        sel = chosen()
        for s in sel:
            refresh(s, cache)
        ev = changes(cache, sel, category=a.category, size=a.size, max_price=a.max_price, mark=a.mark)
        save_cache(cache)
        if a.json:
            print(json.dumps(ev, indent=2))
        elif not ev:
            print("No changes since last mark.")
        else:
            for e in ev:
                was = f" (was ${e['was']:.0f})" if e.get("was") else ""
                print(f"{e['event']}: {e['retailer']} {e['title']} — {e['color']} — ${e['price']:.0f}{was} — sizes {','.join(e['sizes'])}\n   {e['url']}")


if __name__ == "__main__":
    main()
