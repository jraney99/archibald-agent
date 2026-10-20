#!/usr/bin/env python3
"""belt_pick.py — the belt the Outfit Engine never picked.

2026-10-13, the user on a golf board: "if i am playing a round of golf, should i not be
wearing a belt? There is no belt listed on this board." The engine fills base_top,
bottoms and footwear only, so no board had ever named one of his 13 belts.
"""
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
import belt_pick as bp  # noqa: E402

CLOSET = [
    {"id": "polo", "category": "polo", "color": "grey", "lifecycle_state": "active"},
    {"id": "shorts", "category": "shorts", "color": "grey", "notes": "johnnie-O Mulligan, flat front", "lifecycle_state": "active"},
    {"id": "joggers", "category": "pants", "color": "black", "notes": "elastic waist jogger", "lifecycle_state": "active"},
    {"id": "white-golf-shoes", "category": "specialty_footwear", "brand": "G/FORE", "color": "white", "lifecycle_state": "active"},
    {"id": "brown-loafers", "category": "loafers", "brand": "Magnanni", "color": "tabaco brown", "lifecycle_state": "active"},
    {"id": "white-sneaks", "category": "sneakers", "brand": "Nike", "color": "white with black swoosh", "lifecycle_state": "active"},
    {"id": "belt-woven-navy", "category": "belt", "brand": "H&B", "color": "navy with whiskey-brown stripe", "notes": "Fischer woven stretch belt", "lifecycle_state": "active"},
    {"id": "belt-woven-black", "category": "belt", "brand": "H&B", "color": "black", "notes": "Fischer woven stretch belt", "lifecycle_state": "active"},
    {"id": "belt-brown-leather", "category": "belt", "brand": "J.Crew", "color": "medium brown cognac", "notes": "leather dress belt", "lifecycle_state": "active"},
    {"id": "belt-retired", "category": "belt", "brand": "Old", "color": "brown", "notes": "woven", "lifecycle_state": "retired"},
]


class BeltTests(unittest.TestCase):
    def pick(self, items, activity="general", occasion="casual"):
        return bp.pick(items, CLOSET, activity, occasion)

    def test_golf_round_gets_a_woven_belt(self):
        r = self.pick(["polo", "shorts", "white-golf-shoes"], "golf-round", "smart-casual")
        self.assertEqual(r["belt"], "belt-woven-navy")
        self.assertIn("woven", r["why"])

    def test_black_belt_is_not_chosen_under_light_shoes(self):
        """A white sneaker with a black swoosh is a white shoe; the trim is not the match."""
        r = self.pick(["polo", "shorts", "white-sneaks"], "general", "casual")
        self.assertNotEqual(r["belt"], "belt-woven-black")

    def test_business_belt_matches_the_leather_shoe(self):
        r = self.pick(["polo", "shorts", "brown-loafers"], "work", "business-casual")
        self.assertEqual(r["belt"], "belt-brown-leather")
        self.assertEqual(r["why"], "matches the shoes")

    def test_no_belt_when_the_bottoms_have_no_loops(self):
        self.assertIsNone(self.pick(["polo", "joggers", "white-sneaks"])["belt"])
        self.assertIn("no belt loops", self.pick(["polo", "joggers", "white-sneaks"])["why"])

    def test_no_belt_for_workout_activities(self):
        """Simulator and the range are workout clothes; the user: "this doesnt even need this"."""
        for act in ("golf-simulator", "driving-range", "gym", "running"):
            r = self.pick(["polo", "shorts", "white-sneaks"], act, "smart-casual")
            self.assertIsNone(r["belt"], act); self.assertIn("workout clothes", r["why"])
        self.assertEqual(self.pick(["polo", "shorts", "white-golf-shoes"], "golf-round", "smart-casual")["belt"],
                         "belt-woven-navy", "a real round still gets one")

    def test_no_belt_for_athletic_or_lounge(self):
        for occ in ("athletic", "loungewear"):
            self.assertIsNone(self.pick(["polo", "shorts", "white-sneaks"], "gym", occ)["belt"])

    def test_retired_belts_are_never_chosen(self):
        r = self.pick(["polo", "shorts", "white-golf-shoes"], "golf-round", "smart-casual")
        self.assertNotEqual(r["belt"], "belt-retired")

    def test_an_outfit_that_already_has_a_belt_is_left_alone(self):
        r = self.pick(["polo", "shorts", "white-golf-shoes", "belt-woven-black"], "golf-round", "smart-casual")
        self.assertIsNone(r["belt"]); self.assertIn("already", r["why"])

    def test_no_bottoms_means_no_belt(self):
        self.assertIsNone(self.pick(["polo", "white-sneaks"])["belt"])


if __name__ == "__main__":
    unittest.main()
