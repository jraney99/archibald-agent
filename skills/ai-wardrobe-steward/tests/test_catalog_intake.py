#!/usr/bin/env python3
"""catalog_lookup.py, and the closet intake using it for the photo. Sandboxed, no network.

2026-10-13: intake attached a photo only when an order email carried a usable image URL —
in practice never: all 145 closet items came from the user's own photos or receipts, none from
an order email. The wardrobe now holds catalogs for 25 of his stores, each product with the
store's own studio image, so the item is looked up instead of scraped out of an email.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

SANDBOX = tempfile.TemporaryDirectory()
os.environ["OPENCLAW_HOME"] = SANDBOX.name
os.environ["WARDROBE_STATE_DIR"] = SANDBOX.name
os.environ["WARDROBE_NO_SEND"] = "1"
os.environ["TELEGRAM_SEND_DISABLED"] = "1"
SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS.parent.parent / "shared"))
sys.path.insert(0, str(SCRIPTS))
import catalog_lookup as cl  # noqa: E402

STORES = [
    {"slug": "holderness-bourne", "name": "Holderness & Bourne", "domain": "hb.test", "catalog": "shopify",
     "aliases": ["Holderness & Bourne", "H&B", "HB"]},
    {"slug": "peter-millar", "name": "Peter Millar", "domain": "pm.test", "catalog": "page", "aliases": ["Peter Millar"]},
]


def product(title, colour, price, image, available=True):
    return {"id": abs(hash(title)) % 10**6, "title": title, "handle": title.lower().replace(" ", "-"),
            "product_type": "Mens Pants", "excluded": False, "image": image,
            "url": f"https://hb.test/products/{title.lower().replace(' ', '-')}",
            "variants": [{"id": 1, "sku": "", "color": colour, "size": "L", "price": price, "compare_at": None, "available": available}]}


CACHE = {"holderness-bourne": {"products": [
    product("University of Example Jackson Hoodie: Liberty Red", "Liberty Red", 148.0, "https://cdn.test/licensed.png"),
    product("The Fischer Belt: Navy & Slate Microstripe", "Navy & Slate Microstripe", 98.0, "https://cdn.test/slate.png"),
    product("The Mills Jogger: Heathered Navy Blue", "Heathered Navy Blue", 175.0, "https://cdn.test/navy.png"),
    product("The Mills Jogger: Heathered California Spruce", "Heathered California Spruce", 175.0, "https://cdn.test/spruce.png"),
    product("The Baxter 5 Pocket Corduroy Pant: Navy", "Navy", 178.0, "https://cdn.test/baxter.png"),
]}}


class LookupTests(unittest.TestCase):
    def find(self, retailer, item, colour=""):
        return cl.find(retailer, item, colour, stores=STORES, cache=CACHE)

    def test_colourway_is_matched_not_the_shade_word(self):
        """'Heathered' is in both jogger titles; it must not decide which one (2026-10-13)."""
        self.assertEqual(self.find("Holderness & Bourne", "The Mills Jogger", "Heathered Navy Blue")["image_url"], "https://cdn.test/navy.png")
        self.assertEqual(self.find("Holderness & Bourne", "The Mills Jogger", "Heathered California Spruce")["image_url"], "https://cdn.test/spruce.png")

    def test_short_brand_names_resolve(self):
        for name in ("H&B", "HB", "holderness-bourne"):
            self.assertTrue(self.find(name, "Mills Jogger", "Navy")["found"], name)

    def test_price_and_url_come_back(self):
        r = self.find("H&B", "Baxter 5 Pocket Corduroy Pant", "Navy")
        self.assertEqual((r["price"], r["title"]), (178.0, "The Baxter 5 Pocket Corduroy Pant: Navy"))
        self.assertTrue(r["url"].startswith("https://hb.test/products/"))

    def test_it_refuses_rather_than_returning_a_near_miss(self):
        """A wrong photo in the closet is worse than no photo."""
        self.assertFalse(self.find("H&B", "Completely Made Up Garment")["found"])
        self.assertFalse(self.find("Amazon", "Mills Jogger")["found"])
        self.assertIn("publishes no catalog", self.find("Peter Millar", "Anything")["why"])
        one_word = self.find("H&B", "Jogger")
        self.assertFalse(one_word["found"], "one shared word is not an identification")

    def test_a_licensed_edition_is_not_the_plain_garment(self):
        """The university-licensed Jackson Hoodie has a crest on it; his is plain (2026-10-13)."""
        r = self.find("H&B", "The Jackson Hoodie", "Liberty Red")
        self.assertFalse(r["found"], r.get("title"))

    def test_a_named_colourway_must_match(self):
        """'Navy & Whiskey Dogleg' and 'Navy & Slate Microstripe' are both navy Fischer belts."""
        r = self.find("H&B", "The Fischer Belt", "navy with whiskey-brown dogleg stripe")
        self.assertFalse(r["found"])
        self.assertIn("colourway", r["why"])
        self.assertTrue(self.find("H&B", "The Fischer Belt", "Navy & Slate Microstripe")["found"])

    def test_empty_cache_says_so(self):
        self.assertIn("no catalog pulled", cl.find("H&B", "Mills Jogger", stores=STORES, cache={})["why"])


class IntakePhotoTests(unittest.TestCase):
    """The intake prefers the catalog image over whatever the email had."""

    def test_catalog_is_preferred_and_recorded(self):
        src = (SCRIPTS / "propose_closet_addition.py").read_text()
        block = src[src.index('image_url, photo_source = a.image_url'):src.index('duplicate = likely_duplicate')]
        self.assertIn('photo_source = catalog["image_url"], "store catalog"', block)
        self.assertIn('record["product_url"] = catalog["url"]', block)
        self.assertIn("WARDROBE_NO_NETWORK", block, "tests and offline runs must not reach the network")
        self.assertIn("except Exception", block, "a lookup failure must never block the intake")

    def test_price_from_catalog_clears_the_flag(self):
        src = (SCRIPTS / "propose_closet_addition.py").read_text()
        self.assertIn('flagged = [f for f in flagged if f != "purchase_price"]', src)


if __name__ == "__main__":
    unittest.main()
