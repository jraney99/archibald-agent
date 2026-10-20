#!/usr/bin/env python3
"""log_wear.py matching: logs only when sure, refuses otherwise. Sandboxed, no live writes."""
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
import log_wear as lw  # noqa: E402

CATS = ["polo", "shorts", "pants", "jeans", "sweater", "boots", "t-shirt", "shirt", "sneakers"]
CLOSET = [
    {"id": "hb-navy-polo", "brand": "Holderness & Bourne", "color": "navy", "category": "polo", "lifecycle_state": "active"},
    {"id": "hb-white-polo", "brand": "Holderness & Bourne", "color": "white", "category": "polo", "lifecycle_state": "active"},
    {"id": "hb-grey-qz", "brand": "Holderness & Bourne", "color": "light grey", "category": "sweater", "model": "quarter-zip", "lifecycle_state": "active"},
    {"id": "prl-navy-qz", "brand": "Polo Ralph Lauren", "color": "navy", "category": "sweater", "model": "estate rib quarterzip", "lifecycle_state": "active"},
    {"id": "wrangler-light-jeans", "brand": "Wrangler", "color": "light wash blue", "category": "jeans", "lifecycle_state": "active"},
    {"id": "tecovas-cognac-boots", "brand": "Tecovas", "color": "cognac", "category": "boots", "lifecycle_state": "active"},
    {"id": "jo-khaki-shorts", "brand": "johnnie-O", "color": "khaki", "category": "shorts", "lifecycle_state": "active"},
    {"id": "retired-navy-polo", "brand": "Holderness & Bourne", "color": "navy", "category": "polo", "lifecycle_state": "retired"},
]


class MatchTests(unittest.TestCase):
    def m(self, phrase):
        return lw.match_phrase(phrase, CLOSET, CATS)

    def test_colour_brand_category_together_identify_one(self):
        self.assertEqual(self.m("navy H&B polo")["item"], "hb-navy-polo")
        self.assertEqual(self.m("the cognac Tecovas boots")["item"], "tecovas-cognac-boots")
        self.assertEqual(self.m("wrangler light wash jeans")["item"], "wrangler-light-jeans")

    def test_colour_must_agree(self):
        """The 2026-10-13 bug: 'grey quarter zip' matched a NAVY quarter-zip."""
        self.assertEqual(self.m("my grey quarter zip")["item"], "hb-grey-qz")
        self.assertEqual(self.m("navy quarter zip")["item"], "prl-navy-qz")

    def test_brand_word_in_a_brand_name_is_not_a_category(self):
        """'Polo Ralph Lauren' must not make every phrase mean the polo category."""
        self.assertEqual(lw.named_category("polo ralph lauren quarter zip", CATS), "")
        self.assertEqual(lw.named_category("navy polo", CATS), "polo")

    def test_refuses_rather_than_guesses(self):
        bare = self.m("a polo")
        self.assertIn("ambiguous", bare); self.assertNotIn("item", bare)
        self.assertIn("unknown", self.m("the stone johnnie-O shorts"), "he owns no stone johnnie-O shorts")
        self.assertIn("unknown", self.m("my cape"))

    def test_retired_items_are_never_matched(self):
        self.assertEqual(self.m("navy H&B polo")["item"], "hb-navy-polo")
        self.assertIn("unknown", lw.match_phrase("navy H&B polo", [CLOSET[-1]], CATS))

    def test_resolve_splits_a_sentence(self):
        res = lw.resolve("wore my grey quarter zip with the wrangler light wash jeans and a polo", CLOSET)
        self.assertEqual([m["item"] for m in res["matched"]], ["hb-grey-qz", "wrangler-light-jeans"])
        self.assertEqual(len(res["ambiguous"]), 1)


class LogTests(unittest.TestCase):
    def test_log_calls_history_then_marks_dirty(self):
        calls = []

        class R:
            returncode = 0; stdout = ""; stderr = ""
        lw.log(["a", "b"], "casual", "general", "2026-10-13", "note", run=lambda *a, **k: (calls.append(a[0]), R())[1])
        self.assertTrue(str(calls[0][1]).endswith("outfit_history.py"))
        self.assertEqual(calls[0][calls[0].index("--items") + 1], "a,b")
        self.assertEqual(calls[0][calls[0].index("--activity") + 1], "general")
        self.assertTrue(str(calls[1][1]).endswith("mark_after_wear.py"))


if __name__ == "__main__":
    unittest.main()
