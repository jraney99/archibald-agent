#!/usr/bin/env python3
"""Measuring a garment's colour from its product photo. Sandboxed, no network.

2026-10-13: catalogs publish colour NAMES, and `color_match.py --name` returns NO match for
real ones — "Heathered Navy Blue", "Cream" and "Tobacco Cowhide" all came back empty. So the
colour check was silently doing nothing for catalog products. The photo is measured instead.
"""
import io
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
import color_from_image as cfi  # noqa: E402
from PIL import Image  # noqa: E402


def shot(garment, background=(255, 255, 255), size=400, box=0.5):
    """A product photo: a garment-coloured rectangle centred on a backdrop."""
    im = Image.new("RGB", (size, size), background)
    a, b = int(size * (1 - box) / 2), int(size * (1 + box) / 2)
    for x in range(a, b):
        for y in range(a, b):
            im.putpixel((x, y), garment)
    buf = io.BytesIO(); im.save(buf, format="PNG")
    return buf.getvalue()


def near(hex_value, rgb, tol=14):
    got = tuple(int(hex_value[i:i + 2], 16) for i in (1, 3, 5))
    return all(abs(g - w) <= tol for g, w in zip(got, rgb))


class MeasureTests(unittest.TestCase):
    def test_navy_on_white(self):
        r = cfi.measure(shot((31, 36, 73)))
        self.assertTrue(near(r["hex"], (31, 36, 73)), r["hex"])
        self.assertEqual(r["background_hex"], "#FCFCFC", "white, quantised to the bucket step")

    def test_cream_on_white_is_not_mistaken_for_the_backdrop(self):
        """The hard case, and the reason the backdrop is read rather than assumed:
        cream is the user's best colour and sits a few shades from white."""
        r = cfi.measure(shot((210, 199, 187)))
        self.assertTrue(near(r["hex"], (210, 199, 187)), r["hex"])
        self.assertGreater(r["coverage"], 0.5)

    def test_a_dark_backdrop_is_handled_too(self):
        r = cfi.measure(shot((210, 199, 187), background=(20, 20, 20)))
        self.assertTrue(near(r["hex"], (210, 199, 187)), r["hex"])

    def test_a_garment_the_same_colour_as_the_backdrop_still_returns_it(self):
        r = cfi.measure(shot((245, 245, 245), background=(245, 245, 245)))
        self.assertTrue(near(r["hex"], (245, 245, 245)), r["hex"])
        self.assertEqual(r["coverage"], 0.0, "coverage says the reading is weak")

    def test_the_reading_is_remembered_per_product(self):
        calls = []
        cfi_fetch = cfi.fetch
        cfi.fetch = lambda url: (calls.append(url), shot((31, 36, 73)))[1]
        try:
            cache = {}
            a = cfi.for_product("p1", "https://example.test/a.png", cache=cache)
            b = cfi.for_product("p1", "https://example.test/a.png", cache=cache)
            self.assertEqual(a, b); self.assertEqual(len(calls), 1, "fetched once")
            self.assertEqual(cache["p1"]["url"], "https://example.test/a.png")
        finally:
            cfi.fetch = cfi_fetch


if __name__ == "__main__":
    unittest.main()
