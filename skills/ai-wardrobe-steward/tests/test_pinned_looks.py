#!/usr/bin/env python3
"""Pinned looks reaching the outfit scorer. Sandboxed, no network, no live profile.

2026-10-13: 59 described looks and a script that could match them to the closet existed,
but `outfit_score.py` ignored all of it — the thing that picks what he wears had never heard
of the only record of what he likes the look of.
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
import pinned_looks as pl  # noqa: E402

CLOSET = [
    {"id": "navy-polo", "category": "polo", "color": "navy", "lifecycle_state": "active"},
    {"id": "stone-pants", "category": "pants", "color": "stone", "lifecycle_state": "active"},
    {"id": "white-sneaks", "category": "sneakers", "color": "white", "lifecycle_state": "active"},
    {"id": "grey-sweater", "category": "sweater", "color": "grey", "lifecycle_state": "active"},
    {"id": "retired-polo", "category": "polo", "color": "navy", "lifecycle_state": "retired"},
]


def look(pin_id, pieces, kind="look", summary="a look"):
    return {"pin_id": pin_id, "link": f"https://pin/{pin_id}",
            "description": {"kind": kind, "summary": summary,
                            "pieces": [{"item": i, "colour": c} for i, c in pieces]}}


PINS = [
    look("1", [("polo", "navy"), ("chinos", "stone"), ("sneakers", "white")], summary="navy polo, stone chinos, white sneakers"),
    look("2", [("sweater", "grey"), ("chinos", "stone")], summary="grey sweater over stone chinos"),
    look("3", [("polo", "navy")], summary="just a polo"),
    look("4", [("polo", "navy"), ("chinos", "stone")], kind="piece", summary="a product shot"),
    look("5", [("cape", "purple"), ("kilt", "tartan")], summary="nothing he owns"),
]


class LookMappingTests(unittest.TestCase):
    def test_only_looks_that_map_to_two_or_more_pieces(self):
        got = pl.looks(PINS, CLOSET)
        self.assertEqual([x["pin_id"] for x in got], ["1", "2"],
                         "a single-piece look, a product shot and an unmatchable look are all dropped")
        self.assertEqual(got[0]["items"], ["navy-polo", "stone-pants", "white-sneaks"])
        self.assertEqual(got[0]["coverage"], 1.0)

    def test_retired_pieces_are_never_part_of_a_pinned_look(self):
        got = pl.looks(PINS, [CLOSET[-1]])
        self.assertEqual(got, [])


class BonusTests(unittest.TestCase):
    def setUp(self):
        self.pinned = pl.looks(PINS, CLOSET)

    def test_an_outfit_that_matches_a_pinned_look_scores_higher(self):
        bonus, why = pl.overlap_bonus(["navy-polo", "stone-pants", "white-sneaks"], self.pinned)
        self.assertEqual(bonus, 14, "three pieces, capped")
        self.assertEqual(why["source"], "pinned-look"); self.assertEqual(why["pin_id"], "1")
        self.assertIn("pinned", why["note"])

    def test_two_pieces_is_a_resemblance_one_is_not(self):
        self.assertEqual(pl.overlap_bonus(["grey-sweater", "stone-pants"], self.pinned)[0], 12)
        self.assertEqual(pl.overlap_bonus(["navy-polo", "grey-sweater"], self.pinned)[0], 0,
                         "one piece from each of two different pins is not a pinned pairing")
        bonus, why = pl.overlap_bonus(["navy-polo"], self.pinned)
        self.assertEqual((bonus, why), (0, None), "one shared piece is not a resemblance")

    def test_an_unrelated_outfit_gets_nothing(self):
        self.assertEqual(pl.overlap_bonus(["retired-polo"], self.pinned), (0, None))
        self.assertEqual(pl.overlap_bonus([], self.pinned), (0, None))

    def test_the_bonus_is_the_best_look_not_the_sum(self):
        """An item in many pins must not stack a large bonus on its own."""
        many = pl.looks([look(str(i), [("polo", "navy"), ("chinos", "stone")]) for i in range(10)], CLOSET)
        bonus, _ = pl.overlap_bonus(["navy-polo", "stone-pants"], many)
        self.assertEqual(bonus, 12)

    def test_no_pins_means_no_effect(self):
        self.assertEqual(pl.overlap_bonus(["navy-polo", "stone-pants"], []), (0, None))


class ScorerWiringTests(unittest.TestCase):
    def test_the_scorer_calls_it_and_survives_a_failure(self):
        src = (SCRIPTS / "outfit_score.py").read_text()
        self.assertIn("from pinned_looks import looks as pinned_looks, overlap_bonus", src)
        self.assertIn("pin_bonus,pin_why=overlap_bonus(ids,pinned)", src)
        self.assertIn("except Exception: pinned=[]", src, "inspiration must never block a proposal")


if __name__ == "__main__":
    unittest.main()
