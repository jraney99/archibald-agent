#!/usr/bin/env python3
"""store_catalog.py + source_health alias handling, against fixtures only.

OPENCLAW_HOME is pointed at a temp dir BEFORE the scripts import data_paths, so the
store registry and the catalog cache the tests touch are sandbox copies. No network:
the Shopify opener is a fake returning fixture pages.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

SANDBOX = tempfile.TemporaryDirectory()
os.environ["OPENCLAW_HOME"] = SANDBOX.name
SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS.parent.parent / "shared"))
sys.path.insert(0, str(SCRIPTS))
import data_paths as dp  # noqa: E402

STORES = {"stores": [
    {"slug": "holderness-bourne", "name": "Holderness & Bourne", "domain": "hb.test", "catalog": "shopify",
     "aliases": ["holderness-bourne", "Holderness & Bourne"]},
    {"slug": "peter-millar", "name": "Peter Millar", "domain": "pm.test", "catalog": "page",
     "aliases": ["peter-millar", "Peter Millar", "peter-millar-retail", "peter-millar-us"]},
]}
p = dp.path("stores.json"); p.parent.mkdir(parents=True, exist_ok=True); p.write_text(json.dumps(STORES))

import store_catalog as sc  # noqa: E402
import source_health as sh  # noqa: E402

assert str(sc.CACHE_DIR).startswith(SANDBOX.name), sc.CACHE_DIR
assert str(sh.PATH).startswith(str(SCRIPTS.parent)), sh.PATH  # live path: tests never call its writers


def product(pid, title, ptype, colors, sizes, price, compare=None, avail=True, tags=()):
    variants = []
    for c in colors:
        for s in sizes:
            variants.append({"id": pid * 100 + len(variants), "sku": f"{pid}-{c}-{s}", "option1": c, "option2": s,
                             "price": str(price), "compare_at_price": str(compare) if compare else None,
                             "available": avail if not callable(avail) else avail(c, s)})
    return {"id": pid, "title": title, "handle": title.lower().replace(" ", "-"), "product_type": ptype,
            "vendor": "HB", "tags": list(tags), "images": [{"src": "img"}],
            "options": [{"name": "Color", "position": 1, "values": colors}, {"name": "Size", "position": 2, "values": sizes}],
            "variants": variants}


PAGE1 = {"products": [
    product(1, "The Mills Jogger", "Mens Pants", ["Heathered Navy", "Light Gray"], ["M", "L", "XL"], 175),
    product(2, "Short-Sleeve Cullinane Shirt", "Mens Shirts Button Downs", ["Vista Blue"], ["L"], 178, tags=["Short Sleeve"]),
    product(3, "The Saunders Short", "Mens Shorts", ["Navy"], ["M", "L"], 115, compare=135, avail=lambda c, s: s == "L"),
    product(4, "Womens Polo", "Womens Polos", ["Pink"], ["S"], 90),
    product(5, "The Anderson Polo", "Mens Polos", ["California Spruce"], ["L", "XL"], 125, avail=lambda c, s: s == "XL"),
]}


class AttributeTests(unittest.TestCase):
    """What a store publishes about a product. 2026-10-13: these were all discarded, so the
    scan could not see FABRIC — his first criterion — and mining only a 180-char blurb left
    fit on 6% of products, because the detail sits past the first sentence of marketing copy."""

    RAW = {"id": 1, "title": "The Ward Sweater", "handle": "ward", "product_type": "Mens Sweaters",
           "tags": ["Material:Merino Wool", "Fit:Classic", "Sleeve Length:Long Sleeve", "Color:Navy", "ssgroup:x", "sale"],
           "body_html": "<p>Knit from <b>merino wool</b> in a ribbed texture. A midweight classic-fit "
                        "crewneck, made in Scotland, cut for on-course comfort.</p>",
           "options": [{"name": "Color", "position": 1, "values": ["Navy"]}, {"name": "Size", "position": 2, "values": ["L"]}],
           "variants": [{"id": 9, "option1": "Navy", "option2": "L", "price": "150.00", "compare_at_price": None, "available": True}],
           "images": [{"src": "img"}]}

    def test_structured_tags_are_normalised(self):
        a = sc.attributes(self.RAW)
        self.assertEqual(a["material"], ["Merino Wool"])
        self.assertEqual(a["sleeve"], ["Long Sleeve"])
        self.assertIn("Classic", a["fit"], "the Fit tag")

    def test_the_full_description_is_mined_not_just_the_blurb(self):
        a = sc.attributes(self.RAW)
        self.assertEqual(a["fibres"], ["merino", "wool"])
        self.assertIn("ribbed", a["texture"])
        self.assertEqual(a["weight"], ["midweight"])
        self.assertIn("classic-fit", " ".join(a["fit"]).lower())
        self.assertTrue(any("scotland" in x.lower() for x in a["origin"]), a["origin"])
        self.assertEqual(a["intent"], ["on-course"], "golf intent read from the copy")
        self.assertLessEqual(len(a["blurb"]), 180, "the text itself is not kept, only a short blurb")

    def test_a_product_with_no_description_still_normalises(self):
        bare = {**self.RAW, "body_html": "", "tags": []}
        self.assertEqual(sc.attributes(bare), {})
        self.assertEqual(sc.normalise(bare, "x.test")["attrs"], {})

    def test_filters_use_them(self):
        p = sc.normalise(self.RAW, "x.test")
        self.assertTrue(sc.matches(p, category="sweater", material="merino"))
        self.assertTrue(sc.matches(p, category="sweater", texture="ribbed"))
        self.assertTrue(sc.matches(p, category="sweater", fit="classic"))
        self.assertEqual(sc.matches(p, category="sweater", material="linen"), [])
        self.assertEqual(sc.matches(p, category="sweater", texture="waffle"), [])


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.store = STORES["stores"][0]
        self.cache = {}
        self.calls = []

        def opener(url):
            self.calls.append(url)
            return PAGE1
        self.opener = opener

    def test_fetch_normalises_and_caches(self):
        r = sc.refresh(self.store, self.cache, opener=self.opener)
        self.assertEqual(r["status"], "ok"); self.assertEqual(r["products"], 5)
        self.assertEqual(len(self.calls), 1, "one page under PAGE_SIZE stops pagination")
        again = sc.refresh(self.store, self.cache, opener=self.opener)
        self.assertEqual(again["status"], "cached"); self.assertEqual(len(self.calls), 1)
        jogger = self.cache["holderness-bourne"]["products"][0]
        self.assertEqual(jogger["url"], "https://hb.test/products/the-mills-jogger")
        self.assertEqual(jogger["variants"][0], {"id": 100, "sku": "1-Heathered Navy-M", "color": "Heathered Navy",
                                                 "size": "M", "price": 175.0, "compare_at": None, "available": True})

    def test_search_filters(self):
        sc.refresh(self.store, self.cache, opener=self.opener)
        rows = sc.search(self.cache, [self.store], category="pants", size="L", color="navy", in_stock=True)
        self.assertEqual([(r["title"], r["color"], r["sizes_in_stock"]) for r in rows], [("The Mills Jogger", "Heathered Navy", ["L"])])
        # "Short Sleeve" tag must not make a shirt a pair of shorts; womens excluded; sale detected
        rows = sc.search(self.cache, [self.store], category="shorts")
        self.assertEqual([r["title"] for r in rows], ["The Saunders Short"])
        self.assertEqual((rows[0]["sale_pct"], rows[0]["sizes_in_stock"], rows[0]["sizes_out"]), (15, ["L"], ["M"]))
        self.assertEqual(sc.search(self.cache, [self.store], category="polo", on_sale=True), [])
        self.assertEqual([r["title"] for r in sc.search(self.cache, [self.store], category="polo")], ["The Anderson Polo"])
        self.assertEqual(sc.search(self.cache, [self.store], category="polo", size="L", in_stock=True), [])
        self.assertEqual(sc.search(self.cache, [self.store], category="pants", max_price=100), [])

    def test_changes_events(self):
        sc.refresh(self.store, self.cache, opener=self.opener)
        # first mark: nothing to compare against, no noise
        self.assertEqual(sc.changes(self.cache, [self.store], mark=True), [])
        # now: polo L comes back, jogger price drops, a new polo appears
        page2 = json.loads(json.dumps(PAGE1))
        for v in page2["products"][4]["variants"]:
            v["available"] = True
        for v in page2["products"][0]["variants"]:
            v["price"] = "122.00"; v["compare_at_price"] = "175.00"
        page2["products"].append(product(6, "The Hadley Polo", "Mens Polos", ["Navy"], ["L"], 110))
        self.cache["holderness-bourne"]["fetched_at"] = "2000-01-01T00:00:00+00:00"
        sc.refresh(self.store, self.cache, opener=lambda url: page2)
        ev = sc.changes(self.cache, [self.store], category="polo", size="L")
        self.assertEqual(sorted((e["event"], e["title"]) for e in ev), [("back_in_stock", "The Anderson Polo"), ("new_product", "The Hadley Polo")])
        ev = sc.changes(self.cache, [self.store], category="pants", mark=True)
        self.assertEqual([(e["event"], e["title"], e["was"], e["price"], sorted(e["sizes"])) for e in ev if e["color"] == "Heathered Navy"],
                         [("price_drop", "The Mills Jogger", 175.0, 122.0, ["L", "M", "XL"])])
        self.assertEqual(sc.changes(self.cache, [self.store], category="pants"), [], "marked: no repeat alert")

    def test_blocked_store_reports_not_raises(self):
        import urllib.error
        def blocked(url):
            raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
        self.assertEqual(sc.refresh(self.store, self.cache, opener=blocked)["status"], "blocked")

    def test_cache_round_trip_through_store(self):
        sc.refresh(self.store, self.cache, opener=self.opener)
        sc.save_cache(self.cache)
        self.assertEqual(len(sc.load_cache(["holderness-bourne"])["holderness-bourne"]["products"]), 5)
        self.assertTrue(sc.load_cache(["holderness-bourne"])["holderness-bourne"]["products"][3]["excluded"], "womens polo flagged at fetch time")


class SourceHealthAliasTests(unittest.TestCase):
    def test_canonical(self):
        from _stores import canonical
        for name in ("peter-millar", "Peter Millar", "peter-millar-retail", "peter-millar-us", "PETER MILLAR"):
            self.assertEqual(canonical(name), "peter-millar")
        self.assertEqual(canonical("Example Men's Clothier"), "example-men-s-clothier")

    def test_merge_rows_sums_failures_and_keeps_latest(self):
        data = {"peter-millar": {"failures": 0, "last_checked": "2026-09-24", "last_status": "ok", "note": ""},
                "Peter Millar": {"failures": 1, "last_checked": "2026-10-01", "last_status": "blocked", "note": "a"},
                "peter-millar-retail": {"failures": 1, "last_checked": "2026-10-05", "last_status": "blocked", "note": "b"},
                "peter-millar-us": {"failures": 1, "last_checked": "2026-10-08", "last_status": "blocked", "note": "c"},
                "holderness-bourne": {"failures": 0, "last_checked": "2026-10-08", "last_status": "ok", "note": ""}}
        out = sh.merge_rows(data)
        self.assertEqual(sorted(out), ["holderness-bourne", "peter-millar"])
        self.assertEqual(out["peter-millar"], {"failures": 3, "last_checked": "2026-10-08", "last_status": "blocked", "note": "c"})

    def test_record_uses_canonical_slug(self):
        data = {}
        sh.record("Peter Millar", "blocked", "x", data=data)
        sh.record("peter-millar-us", "blocked", "y", data=data)
        self.assertEqual(list(data), ["peter-millar"]); self.assertEqual(data["peter-millar"]["failures"], 2)


if __name__ == "__main__":
    unittest.main()
