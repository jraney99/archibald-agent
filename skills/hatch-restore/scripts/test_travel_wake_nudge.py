#!/usr/bin/env python3
"""
Tests for the travel backup wake nudge (added 2026-08-28): while the user's
away, the Hatch is disabled, so nightly_wake_and_workout_sync.py schedules a
one-shot cron at the computed wake time that fires travel_wake_nudge.py as a
failsafe behind his phone alarm.

No network/cron — subprocess.run and notify() are mocked.

    python3 test_travel_wake_nudge.py
"""
import datetime
import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

_SCRIPTS_DIR = Path(__file__).parent


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


nightly = _load("nightly_wake_and_workout_sync", _SCRIPTS_DIR / "nightly_wake_and_workout_sync.py")
wake_nudge = _load("travel_wake_nudge", _SCRIPTS_DIR / "travel_wake_nudge.py")


class TestNudgeLoop(unittest.TestCase):
    MSG = "⏰ Wake up, Master Wayne — 9:00 AM alarm. 🎩"

    def _run(self, repeats=3, replied_after=None):
        sent = []
        replied = {"flag": False}

        def fake_send(t):
            sent.append(t)
            if replied_after is not None and len(sent) == replied_after:
                replied["flag"] = True

        with patch.object(wake_nudge, "send", fake_send), \
             patch.object(wake_nudge, "owner_replied_since", lambda _s: replied["flag"]), \
             patch.object(wake_nudge.time, "sleep", lambda _s: None), \
             patch.object(wake_nudge.sys, "argv",
                          ["x", "--message", self.MSG, "--repeats", str(repeats),
                           "--interval-seconds", "0"]):
            wake_nudge.main()
        return sent

    def test_repeats_are_all_distinct(self):
        sent = self._run(repeats=3)
        self.assertEqual(len(sent), 3)
        self.assertEqual(len(set(sent)), 3, f"nudges not distinct: {sent}")

    def test_no_doubled_clock_on_repeats(self):
        for t in self._run(repeats=3)[1:]:
            self.assertNotIn("⏰ ⏰", t)
            self.assertNotIn("(still) ⏰", t)

    def test_stops_once_owner_replies(self):
        sent = self._run(repeats=4, replied_after=2)  # reply lands after 2nd send
        self.assertEqual(len(sent), 2)




TZ = ZoneInfo("America/Los_Angeles")


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


AWAY_EARLY = {
    "away": True, "is_default": False, "sync_failed": False,
    "wake_time_fmt": "6:45 AM", "event_title": "Early breakfast meeting",
    "event_start_fmt": "8:00 AM", "leave_time_fmt": "7:15 AM",
}
AWAY_DEFAULT = {
    "away": True, "is_default": True, "sync_failed": False,
    "wake_time_fmt": "7:30 AM", "event_title": None,
    "event_start_fmt": None, "leave_time_fmt": None,
}
# Night before the user flies out: still home (away=False) but a flight is on
# tomorrow's calendar, so the wake is travel-sensitive anyway.
FLIGHT_OUT = {
    "away": False, "flight_tomorrow": True, "is_default": False, "sync_failed": False,
    "wake_time_fmt": "3:27 AM", "event_title": "WN 100 DAL → XYZ",
    "event_start_fmt": "6:25 AM", "leave_time_fmt": "4:12 AM",
}


class TestScheduleTravelWakeNudge(unittest.TestCase):
    def test_noop_when_not_away(self):
        wake_dt = datetime.datetime(2026, 8, 29, 6, 45, tzinfo=TZ)
        with patch.object(nightly.subprocess, "run") as mock_run:
            nightly.schedule_travel_wake_nudge(wake_dt, {"away": False, "is_default": True})
        mock_run.assert_not_called()

    def test_noop_when_wake_dt_none(self):
        with patch.object(nightly.subprocess, "run") as mock_run:
            nightly.schedule_travel_wake_nudge(None, AWAY_EARLY)
        mock_run.assert_not_called()

    def test_schedules_oneshot_at_wake_time_with_event_context(self):
        wake_dt = datetime.datetime(2026, 8, 29, 6, 45, tzinfo=TZ)
        list_result = _completed(0, stdout=json.dumps({"jobs": []}))
        add_result = _completed(0, stdout=json.dumps({"id": "job123"}))

        with patch.object(nightly.subprocess, "run", side_effect=[list_result, add_result]) as mock_run:
            nightly.schedule_travel_wake_nudge(wake_dt, AWAY_EARLY)

        add_args = mock_run.call_args_list[1][0][0]
        self.assertEqual(add_args[:3], [nightly.OPENCLAW_BIN, "cron", "add"])
        self.assertTrue(add_args[3].startswith("travel-wake-nudge-2026-08-29"))
        at_idx = add_args.index("--at")
        self.assertEqual(add_args[at_idx + 1], "2026-08-29T06:45:00-07:00")
        self.assertIn("--no-deliver", add_args)
        self.assertIn("--delete-after-run", add_args)
        argv = json.loads(add_args[add_args.index("--command-argv") + 1])
        self.assertIn(str(nightly.TRAVEL_WAKE_NUDGE), argv)
        msg = argv[argv.index("--message") + 1]
        self.assertIn("Early breakfast meeting", msg)
        self.assertIn("7:15 AM", msg)

    def test_schedules_nudge_on_flight_day_even_when_not_away(self):
        # The 2026-09-01 regression: away=False the night before a flight,
        # so the old guard skipped the backup nudge entirely.
        wake_dt = datetime.datetime(2026, 8, 24, 3, 27, tzinfo=TZ)
        list_result = _completed(0, stdout=json.dumps({"jobs": []}))
        add_result = _completed(0, stdout=json.dumps({"id": "job123"}))

        with patch.object(nightly.subprocess, "run", side_effect=[list_result, add_result]) as mock_run:
            nightly.schedule_travel_wake_nudge(wake_dt, FLIGHT_OUT)

        add_args = mock_run.call_args_list[1][0][0]
        self.assertEqual(add_args[:3], [nightly.OPENCLAW_BIN, "cron", "add"])
        argv = json.loads(add_args[add_args.index("--command-argv") + 1])
        msg = argv[argv.index("--message") + 1]
        self.assertIn("WN 100 DAL", msg)

    def test_noop_on_default_day(self):
        # A backup alarm only earns its interruption when there's an actual
        # early commitment; with nothing on the calendar the morning ping is
        # just noise (the 9pm brief already said the day's clear).
        wake_dt = datetime.datetime(2026, 8, 29, 7, 30, tzinfo=TZ)
        with patch.object(nightly.subprocess, "run") as mock_run:
            nightly.schedule_travel_wake_nudge(wake_dt, AWAY_DEFAULT)
        mock_run.assert_not_called()

    def test_scheduling_failure_notifies_now(self):
        wake_dt = datetime.datetime(2026, 8, 29, 6, 45, tzinfo=TZ)
        list_result = _completed(0, stdout=json.dumps({"jobs": []}))
        add_result = _completed(1, stderr="gateway unreachable")

        with patch.object(nightly.subprocess, "run", side_effect=[list_result, add_result]), \
             patch.object(nightly, "notify") as mock_notify:
            nightly.schedule_travel_wake_nudge(wake_dt, AWAY_EARLY)

        mock_notify.assert_called_once()
        self.assertIn("phone alarm", mock_notify.call_args[0][0].lower())

    def test_clears_all_matching_nudge_jobs_before_readding(self):
        # `cron add <name>` duplicates rather than replaces, so every
        # travel-wake-nudge-* job (today's included) is cleared first; other
        # prefixes are untouched.
        wake_dt = datetime.datetime(2026, 8, 29, 6, 45, tzinfo=TZ)
        yesterday = {"name": "travel-wake-nudge-2026-08-27", "id": "old1"}
        today = {"name": "travel-wake-nudge-2026-08-29", "id": "dupe"}
        other = {"name": "weekly-weekly_checkin-reminder-2026-08-31", "id": "wi"}
        list_result = _completed(0, stdout=json.dumps({"jobs": [yesterday, today, other]}))
        rm_result = _completed(0, stdout="{}")
        add_result = _completed(0, stdout=json.dumps({"id": "job123"}))

        with patch.object(nightly.subprocess, "run",
                          side_effect=[list_result, rm_result, rm_result, add_result]) as mock_run:
            nightly.schedule_travel_wake_nudge(wake_dt, AWAY_EARLY)

        removed = {c[0][0][3] for c in mock_run.call_args_list if c[0][0][:3] == [nightly.OPENCLAW_BIN, "cron", "rm"]}
        self.assertEqual(removed, {"old1", "dupe"})  # both nudge jobs, not the weekly check-in one


class TestAwayMessageIsWakeOnly(unittest.TestCase):
    """While away, the nightly message must be ONLY the wake-up line — no
    workout line, no conflict lines, no 'backup nudge scheduled' tail."""

    WORKOUT = {"status": "success", "booked": [{"workout_start": "16:00", "already_booked": True}]}
    CONFLICTS = [{
        "to_event": "Legs Workout", "to_start_fmt": "4:00 PM", "from_event": "WN 200",
        "required_leave_fmt": "2:31 PM", "from_end_fmt": "3:55 PM", "shortfall_minutes": 84,
    }]

    def test_default_day(self):
        ar = {**AWAY_DEFAULT, "conflicts": self.CONFLICTS}
        msg = nightly.format_message(ar, self.WORKOUT)
        self.assertIn("phone alarm for 7:30 AM", msg)
        self.assertNotIn("🏋️", msg)
        self.assertNotIn("Can't make it", msg)
        self.assertNotIn("nudge", msg.lower())
        self.assertEqual(msg.count("\n"), 0)  # single line

    def test_early_event_day(self):
        ar = {**AWAY_EARLY, "conflicts": self.CONFLICTS}
        msg = nightly.format_message(ar, self.WORKOUT)
        self.assertIn("Early breakfast meeting", msg)
        self.assertIn("leave by 7:15 AM", msg)
        self.assertNotIn("🏋️", msg)
        self.assertNotIn("Can't make it", msg)

    def test_home_day_still_shows_everything(self):
        ar = {
            "sync_failed": False, "away": False, "is_default": True,
            "wake_time_fmt": "8:30 AM", "conflicts": self.CONFLICTS,
        }
        msg = nightly.format_message(ar, self.WORKOUT)
        self.assertIn("🏋️", msg)
        self.assertIn("Can't make it", msg)

    def test_flight_out_day_is_wake_only_with_phone_backup(self):
        # Home tonight, flying out tomorrow: Hatch is set, but the message
        # must still push a phone-alarm backup and drop the workout/conflict
        # noise (it's a travel day).
        ar = {**FLIGHT_OUT, "conflicts": self.CONFLICTS}
        msg = nightly.format_message(ar, self.WORKOUT)
        self.assertIn("phone alarm", msg.lower())
        self.assertIn("WN 100 DAL", msg)
        self.assertNotIn("🏋️", msg)
        self.assertNotIn("Can't make it", msg)


if __name__ == "__main__":
    unittest.main()
