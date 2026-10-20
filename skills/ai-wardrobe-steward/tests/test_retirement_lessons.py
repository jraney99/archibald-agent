#!/usr/bin/env python3
"""Retiring a piece records WHY, and repeats become a purchase lesson. Sandboxed.

2026-10-13: retiring captured where a piece went but never why, so half the notes said only
"didn't fit" or nothing — and seven entries for one brand were ONE sizing mistake written
down seven times, which would have taught the skill to stop buying a brand he likes. The
user chose to start fresh: the old retirements are not mined.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SANDBOX = tempfile.TemporaryDirectory()
os.environ["OPENCLAW_HOME"] = SANDBOX.name
SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS.parent.parent / "shared"))
sys.path.insert(0, str(SCRIPTS))
import retirement_lessons as rl  # noqa: E402

SB = Path(SANDBOX.name)
rl.PENDING, rl.LESSONS = SB / "pending.json", SB / "lessons.json"


def gone(item_id, brand, category, reason, note=None):
    return {"id": item_id, "brand": brand, "category": category, "lifecycle_state": "retired",
            "retirement": {"reason": reason, "date": "2026-10-13", **({"note": note} if note else {})}}


class PatternTests(unittest.TestCase):
    def test_two_of_the_same_reason_is_a_pattern_one_is_not(self):
        one = [gone("a", "Vuori", "polo", "doesnt-fit")]
        self.assertEqual(rl.patterns(one), [], "one retirement is an incident")
        two = one + [gone("b", "Vuori", "pants", "doesnt-fit")]
        p = rl.patterns(two)
        self.assertEqual(len(p), 1)
        self.assertEqual((p[0]["brand"], p[0]["reason"], p[0]["lesson_type"]), ("Vuori", "doesnt-fit", "fit"))
        self.assertIn("Vuori", p[0]["lesson"]); self.assertIn("sizing as a risk", p[0]["lesson"])

    def test_different_reasons_do_not_combine(self):
        mixed = [gone("a", "Vuori", "polo", "doesnt-fit"), gone("b", "Vuori", "pants", "worn-out")]
        self.assertEqual(rl.patterns(mixed), [], "a fit problem and a wear problem are different lessons")

    def test_never_wore_is_phrased_as_the_strongest_signal(self):
        p = rl.patterns([gone("a", "Brand X", "polo", "never-wore"), gone("b", "Brand X", "shirt", "never-wore")])
        self.assertIn("never worn", p[0]["lesson"])
        self.assertIn("strongest signal", p[0]["lesson"])
        self.assertEqual(p[0]["lesson_type"], "taste")

    def test_items_without_a_structured_reason_are_ignored(self):
        """The old free-text notes are deliberately not mined."""
        legacy = [{"id": "old", "brand": "Holderness & Bourne", "category": "shorts", "lifecycle_state": "retired",
                   "notes": "Didn't fit - given away. Never photographed."}] * 7
        self.assertEqual(rl.retirements(legacy), [])
        self.assertEqual(rl.patterns(legacy), [], "seven copies of one mistake must not become a lesson")

    def test_active_items_are_not_retirements(self):
        self.assertEqual(rl.retirements([{"id": "a", "brand": "X", "lifecycle_state": "active"}]), [])


class ProposeTests(unittest.TestCase):
    def setUp(self):
        rl.PENDING.write_text("[]"); rl.LESSONS.write_text("[]")

    def test_drafts_go_to_pending_not_straight_into_lessons(self):
        found = rl.patterns([gone("a", "Vuori", "polo", "doesnt-fit", "runs narrow"), gone("b", "Vuori", "pants", "doesnt-fit")])
        made = rl.propose(found)
        self.assertEqual(len(made), 1)
        self.assertEqual(json.load(open(rl.LESSONS)), [], "nothing reaches purchase_lessons unconfirmed")
        draft = json.load(open(rl.PENDING))[0]
        self.assertEqual(draft["lesson_type"], "fit")
        self.assertIn("2 retired for 'doesnt-fit'", draft["evidence"][0]["note"])

    def test_an_existing_lesson_is_not_drafted_again(self):
        found = rl.patterns([gone("a", "Vuori", "polo", "doesnt-fit"), gone("b", "Vuori", "pants", "doesnt-fit")])
        rl.LESSONS.write_text(json.dumps([{"id": found[0]["id"]}]))
        self.assertEqual(rl.propose(found), [])


class RetireCommandTests(unittest.TestCase):
    def run_it(self, *args):
        return subprocess.run([sys.executable, str(SCRIPTS / "closet_bulk_state.py"), "--ids", "x", "--set-state", "retired", *args],
                              capture_output=True, text=True)

    def test_retiring_without_a_reason_is_refused(self):
        r = self.run_it()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--reason is required", r.stdout + r.stderr)

    def test_an_invented_reason_is_refused(self):
        r = self.run_it("--reason", "bored-of-it")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("is not a retirement reason", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
