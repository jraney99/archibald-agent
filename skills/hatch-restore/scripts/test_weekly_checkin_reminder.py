#!/usr/bin/env python3
"""
Regression test for the 2026-08-23 fix: the Monday weekly check-in reminder used to
fire on a fixed "0 10 * * 1" cron regardless of the user's actual schedule, so on
a Monday with an early out-of-house commitment (e.g. an 8am breakfast) it
landed hours after he'd already left home — when the check-in made no sense. Fixed
by anchoring the reminder to tomorrow's actual computed wake time (already
known nightly via sync_alarm's calendar-aware compute) instead of a static
clock time, mirroring how the workout booking was already wake-anchored
(see feedback memory "Workout/Wake Buffer").

No network/cron access — subprocess.run and notify() are mocked.

Run manually after touching nightly_wake_and_workout_sync.py:

    python3 test_weekly_checkin_reminder.py
"""
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

TZ = ZoneInfo("America/Chicago")
CONFIG = {"default_wake_time": "08:30"}


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class Testweekly_checkinReminderScheduling(unittest.TestCase):
    def setUp(self):
        # Default: at home. Individual tests override as needed.
        p = patch.object(nightly, "is_away_from_home", return_value=False)
        p.start()
        self.addCleanup(p.stop)

    def test_noop_on_non_monday(self):
        with patch.object(nightly.subprocess, "run") as mock_run:
            nightly.schedule_weekly_checkin_reminder("tuesday", None, TZ, CONFIG)
        mock_run.assert_not_called()

    def test_noop_when_away_from_home(self):
        with patch.object(nightly, "is_away_from_home", return_value=True), \
             patch.object(nightly.subprocess, "run") as mock_run:
            nightly.schedule_weekly_checkin_reminder("monday", None, TZ, CONFIG)
        mock_run.assert_not_called()

    def test_monday_anchors_to_wake_time_plus_buffer(self):
        import datetime
        wake_dt = datetime.datetime(2026, 8, 24, 7, 0, tzinfo=TZ)  # early wake for a real commitment
        list_result = _completed(0, stdout=json.dumps({"jobs": []}))
        add_result = _completed(0, stdout=json.dumps({"id": "abc123"}))

        with patch.object(nightly.subprocess, "run", side_effect=[list_result, add_result]) as mock_run:
            nightly.schedule_weekly_checkin_reminder("monday", wake_dt, TZ, CONFIG)

        add_call_args = mock_run.call_args_list[1][0][0]
        at_idx = add_call_args.index("--at")
        # 7:00 AM wake + 5 min buffer, NOT the old fixed 10:00 AM
        self.assertEqual(add_call_args[at_idx + 1], "2026-08-24T07:05:00-05:00")
        self.assertIn("--no-deliver", add_call_args)
        argv_idx = add_call_args.index("--command-argv")
        argv = json.loads(add_call_args[argv_idx + 1])
        self.assertIn("Weekly check-in time", argv[-1])

    def test_monday_falls_back_to_default_wake_time_when_compute_failed(self):
        list_result = _completed(0, stdout=json.dumps({"jobs": []}))
        add_result = _completed(0, stdout=json.dumps({"id": "abc123"}))

        with patch.object(nightly.subprocess, "run", side_effect=[list_result, add_result]) as mock_run, \
             patch.object(nightly, "datetime") as mock_dt:
            import datetime as real_datetime
            mock_dt.now.return_value = real_datetime.datetime(2026, 8, 23, 21, 0, tzinfo=TZ)
            mock_dt.combine = real_datetime.datetime.combine
            nightly.schedule_weekly_checkin_reminder("monday", None, TZ, CONFIG)

        add_call_args = mock_run.call_args_list[1][0][0]
        at_idx = add_call_args.index("--at")
        # default_wake_time 08:30 + 5 min buffer
        self.assertEqual(add_call_args[at_idx + 1], "2026-08-24T08:35:00-05:00")

    def test_scheduling_failure_falls_back_to_notify_now(self):
        import datetime
        wake_dt = datetime.datetime(2026, 8, 24, 7, 0, tzinfo=TZ)
        list_result = _completed(0, stdout=json.dumps({"jobs": []}))
        add_result = _completed(1, stderr="gateway unreachable")

        with patch.object(nightly.subprocess, "run", side_effect=[list_result, add_result]), \
             patch.object(nightly, "notify") as mock_notify:
            nightly.schedule_weekly_checkin_reminder("monday", wake_dt, TZ, CONFIG)

        mock_notify.assert_called_once()
        self.assertIn("Weekly check-in time", mock_notify.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
