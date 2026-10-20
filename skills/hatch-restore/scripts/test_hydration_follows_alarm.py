#!/usr/bin/env python3
"""The six hydration nudges follow the alarm, like the meals do.

2026-10-13: they were six fixed crons (8:15–18:15) and nothing in the hydration
skill ever read the alarm. On the noon alarm the user tried, half fired before he was
awake — leaving him to chase 96oz in half the waking hours with the same number
of reminders. The user: "are there any other items that should drive off the alarm
that aren't?"

    python3 test_hydration_follows_alarm.py
"""
import importlib.util
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent.parent / "shared"))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


nw = _load("nightly_wake_and_workout_sync", _HERE / "nightly_wake_and_workout_sync.py")
TZ = ZoneInfo("America/Chicago")


def times(hhmm):
    h, m = (int(x) for x in hhmm.split(":"))
    return nw.hydration_times(datetime(2026, 10, 14, h, m, tzinfo=TZ), TZ)


def hhmm(ts):
    return [t.strftime("%H:%M") for t in ts]


class MatchesWhatItReplaced(unittest.TestCase):
    def test_a_730_wake_reproduces_the_old_fixed_schedule(self):
        """The old times were right for a 7:30 start — only the anchor was missing.
        If this ever drifts, the change was not a pure re-anchoring."""
        self.assertEqual(hhmm(times("07:30")),
                         ["08:15", "10:15", "12:15", "14:15", "16:15", "18:15"])


class FollowsTheAlarm(unittest.TestCase):
    def test_a_later_wake_moves_every_nudge(self):
        self.assertEqual(hhmm(times("08:30")),
                         ["09:15", "11:15", "13:15", "15:15", "17:15", "19:15"])

    def test_nothing_fires_before_he_is_awake(self):
        for wake in ("06:00", "07:30", "08:30", "09:30", "11:00", "12:00"):
            first = times(wake)[0]
            h, m = (int(x) for x in wake.split(":"))
            self.assertGreater(first, datetime(2026, 10, 14, h, m, tzinfo=TZ), wake)

    def test_the_first_lands_45_minutes_after_waking(self):
        self.assertEqual(hhmm(times("09:30"))[0], "10:15")

    def test_there_are_always_six(self):
        for wake in ("06:00", "09:30", "12:00", "17:00"):
            self.assertEqual(len(times(wake)), 6, wake)


class StaysOutOfTheEvening(unittest.TestCase):
    def test_a_late_wake_still_finishes_by_8pm(self):
        for wake in ("09:30", "11:00", "12:00"):
            self.assertLessEqual(times(wake)[-1].strftime("%H:%M"), "20:00", wake)

    def test_a_late_wake_compresses_rather_than_dropping_nudges(self):
        t = times("12:00")
        self.assertEqual(len(t), 6)
        gap = (t[1] - t[0]).total_seconds() / 60
        self.assertLess(gap, nw.HYDRATION_SPACING_MIN)


class NeverStacks(unittest.TestCase):
    def test_six_nudges_never_share_a_minute(self):
        # The first cut collapsed a 19:30 wake onto one minute — six messages at once.
        for wake in ("06:00", "09:30", "12:00", "17:00", "19:30", "22:00"):
            self.assertEqual(len(set(hhmm(times(wake)))), 6, wake)

    def test_spacing_never_drops_below_the_minimum(self):
        for wake in ("17:00", "19:30", "22:00"):
            t = times(wake)
            for i in range(5):
                self.assertGreaterEqual(t[i + 1] - t[i], nw.HYDRATION_MIN_SPACING, wake)

    def test_times_never_run_backwards(self):
        for wake in ("06:00", "12:00", "19:30"):
            t = times(wake)
            self.assertEqual(t, sorted(t), wake)


class WiredIntoTheNightlySync(unittest.TestCase):
    SRC = (_HERE / "nightly_wake_and_workout_sync.py").read_text()

    def test_it_runs_only_once_the_meal_plan_is_safely_written(self):
        after = self.SRC.split("if plan_written:")[1]
        self.assertIn("_retime_hydration_nudges(base, tz)", after)

    def test_a_failure_cannot_cascade(self):
        # The meal-plan except clause deletes the plan and messages the user; a
        # hydration problem must never reach it.
        block = self.SRC.split("_retime_hydration_nudges(base, tz)")[1][:300]
        self.assertIn("except Exception", block)
        self.assertIn("stderr", block)

    def test_a_disabled_job_is_reported_not_assumed(self):
        self.assertIn("_cron_enabled(job_id)", self.SRC.split("def _retime_hydration_nudges")[1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
