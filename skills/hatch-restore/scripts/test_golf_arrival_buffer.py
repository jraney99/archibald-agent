#!/usr/bin/env python3
"""The 45-minute golf buffer is for courses, not for walk-in venues.

2026-10-14, the user: "why is it telling me to leave so early?" — one flat
golf_arrival_buffer_minutes: 45 applied to anything with "golf" in the title, so
a 15-minute drive to an indoor bay became "leave an hour early", and
wake_before_leave_minutes: 45 stacked on top (an 11:30 tee time wanted him up at
9:55). His rule: "doesn't apply for the municipal course's night holes or the
simulators" — you walk in and start. A daytime round at that course still does.

    python3 test_golf_arrival_buffer.py
"""
import json
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent.parent / "shared"))
import sync_alarm as sa  # noqa: E402

CFG = json.loads(sa.CONFIG_PATH.read_text())
COURSE = CFG["golf_arrival_buffer_minutes"]
QUICK = CFG["golf_quick_entry_buffer_minutes"]
PLAIN = CFG["default_arrival_buffer_minutes"]


def buf(title, location=""):
    return sa.arrival_buffer_for(title, location, CFG)


class WalkInVenues(unittest.TestCase):
    def test_indoor_lounge_is_quick_entry(self):
        self.assertEqual(buf("Golf <> the user @ Example Golf Lounge",
                             "100 Example St, Suite 100, Example City"), QUICK)

    def test_sim_bar_is_quick_entry(self):
        self.assertEqual(buf("Golf <> the user @ Example Sim Bar",
                             "100 Example St, Example City"), QUICK)

    def test_night_holes_are_quick_entry(self):
        self.assertEqual(buf("Night Holes <> the user @ Municipal Course",
                             "Example Municipal Golf Course, Example City"), QUICK)

    def test_a_simulator_anywhere_is_quick_entry(self):
        self.assertEqual(buf("Golf <> the user @ Some Simulator Place", "anywhere"), QUICK)

    def test_the_venue_may_be_named_only_in_the_location(self):
        # "Golf <> the user @ Example Sim Bar" is the house style, but a bare title must
        # still match on address.
        self.assertEqual(buf("Golf <> the user", "Example Golf Lounge, 100 Example St"), QUICK)


class RealCourses(unittest.TestCase):
    def test_a_daytime_round_at_the_municipal_course_keeps_the_full_buffer(self):
        """The distinction the user drew: night holes yes, the course itself no."""
        self.assertEqual(buf("Golf <> the user @ Municipal Course",
                             "Example Municipal Golf Course, Example City"), COURSE)

    def test_an_ordinary_course_keeps_the_full_buffer(self):
        self.assertEqual(buf("Golf <> the user @ Example Country Club",
                             "200 Example Rd, Example City"), COURSE)


class NotGolfAtAll(unittest.TestCase):
    def test_a_zoom_call_gets_the_ordinary_buffer(self):
        self.assertEqual(buf("a colleague <> the user Team Meeting", "Zoom"), PLAIN)

    def test_quick_entry_cannot_leak_onto_a_non_golf_event(self):
        # "Example Golf Lounge" could appear in some unrelated venue name.
        self.assertEqual(buf("Dinner <> the user @ Example Golf Lounge", "Example Golf Lounge"), PLAIN)


class TheBuffersAreOrdered(unittest.TestCase):
    def test_quick_entry_is_smaller_than_a_course(self):
        self.assertLess(QUICK, COURSE)

    def test_quick_entry_is_not_smaller_than_an_ordinary_event(self):
        self.assertGreaterEqual(QUICK, PLAIN)

    def test_night_holes_is_recognised_as_golf(self):
        """Without this it is not golf at all and silently gets PLAIN, which is
        how the first cut of this fix behaved."""
        self.assertTrue(sa.is_golf("Night Holes <> the user @ Municipal Course",
                                   CFG["golf_title_keywords"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
