#!/usr/bin/env python3
"""Which golf events deserve a board. Regression for 2026-10-13.

The user got a smart-casual board for "Golf <> the user @ The Fairway Indoor Golf Club" with the
reason "a real round of golf". It was a TOUR of a simulator club he might join: "this is
not a real round of golf." The bare vocab alias golf -> golf-round made every event with
"golf" in the title a round, and only a one-entry venue file kept a named simulator venue quiet.
"""
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS.parent.parent / "shared"))
sys.path.insert(0, str(SCRIPTS))
import event_triage as et  # noqa: E402

TRIAGE_CLS = next(getattr(et, n) for n in dir(et) if n[0].isupper() and hasattr(getattr(et, n), "_verdict"))


class GolfTriageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.t = TRIAGE_CLS()

    def verdict(self, title, location=""):
        return self.t._verdict({"title": title, "location": location or title.split(" @ ")[-1],
                                "start_iso": "2026-10-20T15:00:00-05:00"})

    def test_real_round_still_gets_a_board(self):
        for title in ("Golf <> the user @ Example Hills Golf Club", "Tee time <> the user @ Riverside Municipal"):
            r = self.verdict(title)
            self.assertEqual(r["decision"], "propose", title)
            self.assertEqual(r["activity"], "golf-round")

    def test_indoor_venue_is_never_a_round(self):
        """The reported bug, plus the venues it would also have hit."""
        for title in ("Golf <> the user @ The Fairway Indoor Golf Club", "Golf <> the user @ Topgolf",
                      "Golf <> the user @ Five Iron Golf", "Golf <> the user @ X-Golf",
                      "Golf <> the user @ The Sim Lounge"):
            r = self.verdict(title)
            self.assertEqual(r["decision"], "skip", f"{title} -> {r['reason']}")
            self.assertIn("indoor golf", r["reason"])

    def test_a_named_sim_venue_stays_quiet_without_relying_on_the_venue_file(self):
        r = self.verdict("Golf <> the user @ The Birdie Bay")
        self.assertEqual(r["decision"], "skip")

    def test_a_tour_or_visit_is_not_an_occasion(self):
        for title in ("Tour of The Fairway <> the user @ The Fairway",
                      "Membership visit <> the user @ Example National"):
            self.assertEqual(self.verdict(title)["decision"], "skip", title)

    def test_workouts_and_remote_are_untouched(self):
        self.assertEqual(self.verdict("Swim <> the user @ Example Fitness")["decision"], "skip")
        self.assertEqual(self.verdict("a colleague <> the user Team Meeting @ Zoom", "Zoom")["decision"], "skip")


if __name__ == "__main__":
    unittest.main()
