#!/usr/bin/env python3
"""
Tests for the 2026-09-27 change: the morning briefing fires at the alarm + 15
instead of a fixed 8am ("I just want it to fire when I actually get up ... If I
choose to sleep in I want it to be dynamic").

Covers the one-shot the nightly sync schedules, the plan file it writes, and
refresh_morning_briefing.py's re-point when the alarm moves (which must never
send a Telegram message).

No network/cron access — subprocess.run, notify() and the Hatch read are mocked.

    python3 test_morning_briefing_schedule.py
"""
import datetime as real_datetime
import importlib.util
import json
import subprocess
import sys
import tempfile
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
refresh = _load("refresh_morning_briefing", _SCRIPTS_DIR / "refresh_morning_briefing.py")

TZ = ZoneInfo("America/Chicago")
PACIFIC = ZoneInfo("America/Los_Angeles")
CONFIG = {"default_wake_time": "07:30"}
LIST_EMPTY = subprocess.CompletedProcess([], 0, stdout=json.dumps({"jobs": []}), stderr="")
ADD_OK = subprocess.CompletedProcess([], 0, stdout=json.dumps({"id": "abc123"}), stderr="")
ADD_FAIL = subprocess.CompletedProcess([], 1, stdout="", stderr="gateway unreachable")


class _PlanFileCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.plan_file = Path(tmp.name) / "briefing_plan.json"
        for target, attr, value in [
            (nightly, "BRIEFING_PLAN_FILE", self.plan_file),
            (nightly, "is_away_from_home", lambda *a, **k: False),
            (refresh, "is_away_from_home", lambda *a, **k: False),
        ]:
            p = patch.object(target, attr, value)
            p.start()
            self.addCleanup(p.stop)


class TestNightlySchedule(_PlanFileCase):
    def _schedule(self, wake_dt, tz=TZ, runs=(LIST_EMPTY, ADD_OK), **kw):
        with patch.object(nightly.subprocess, "run", side_effect=list(runs)) as mock_run, \
             patch.object(nightly, "notify") as mock_notify:
            nightly.schedule_morning_briefing(wake_dt, tz, CONFIG, **kw)
        return mock_run, mock_notify

    def test_fires_fifteen_minutes_after_the_alarm(self):
        mock_run, _ = self._schedule(real_datetime.datetime(2026, 9, 28, 9, 0, tzinfo=TZ))
        add = mock_run.call_args_list[1][0][0]
        self.assertEqual(add[add.index("--at") + 1], "2026-09-28T09:15:00-05:00")
        self.assertEqual(add[add.index("cron") + 2], "morning-briefing-at-wake-2026-09-28")
        self.assertIn("run_morning_briefing.py", add[add.index("--command-argv") + 1])
        for flag in ("--delete-after-run", "--no-deliver"):
            self.assertIn(flag, add)

    def test_plan_records_the_alarm_and_the_send_time(self):
        self._schedule(real_datetime.datetime(2026, 9, 28, 9, 0, tzinfo=TZ))
        self.assertEqual(json.loads(self.plan_file.read_text()), {
            "date": "2026-09-28",
            "wake_at": "2026-09-28T09:00:00-05:00",
            "fire_at": "2026-09-28T09:15:00-05:00",
        })

    def test_prior_one_shot_is_cancelled_first(self):
        mock_run, _ = self._schedule(real_datetime.datetime(2026, 9, 28, 9, 0, tzinfo=TZ))
        self.assertIn("list", mock_run.call_args_list[0][0][0])

    def test_sleeping_in_moves_it_with_no_fixed_8am_floor(self):
        mock_run, _ = self._schedule(real_datetime.datetime(2026, 9, 28, 11, 30, tzinfo=TZ))
        add = mock_run.call_args_list[1][0][0]
        self.assertEqual(add[add.index("--at") + 1], "2026-09-28T11:45:00-05:00")

    def test_travelling_still_schedules_and_keeps_the_local_timezone(self):
        # Unlike the Breakfast Anchor, no away-skip: the wake time is computed, not read
        # off the Hatch, so it survives an empty house.
        with patch.object(nightly, "is_away_from_home", return_value=True):
            mock_run, _ = self._schedule(real_datetime.datetime(2026, 9, 28, 7, 30, tzinfo=PACIFIC), tz=PACIFIC)
        add = mock_run.call_args_list[1][0][0]
        self.assertEqual(add[add.index("--at") + 1], "2026-09-28T07:45:00-07:00")

    def test_failed_wake_compute_falls_back_to_the_default(self):
        now = real_datetime.datetime(2026, 9, 27, 21, 0, tzinfo=TZ)
        with patch.object(nightly, "datetime", wraps=real_datetime.datetime) as dt:
            dt.now.return_value = now
            dt.combine = real_datetime.datetime.combine
            mock_run, _ = self._schedule(None)
        add = mock_run.call_args_list[1][0][0]
        self.assertEqual(add[add.index("--at") + 1], "2026-09-28T07:45:00-05:00")

    def test_failure_tells_him_and_never_sends_a_briefing_at_night(self):
        mock_run, mock_notify = self._schedule(
            real_datetime.datetime(2026, 9, 28, 9, 0, tzinfo=TZ), runs=(LIST_EMPTY, ADD_FAIL))
        self.assertEqual(mock_run.call_count, 2)  # list + failed add; no briefing run
        self.assertIn("morning briefing", mock_notify.call_args[0][0].lower())
        self.assertFalse(self.plan_file.exists())

    def test_pre_dawn_failure_is_silent(self):
        _, mock_notify = self._schedule(real_datetime.datetime(2026, 9, 28, 9, 0, tzinfo=TZ),
                                        runs=(LIST_EMPTY, ADD_FAIL), send_now_on_failure=False)
        mock_notify.assert_not_called()


class TestRefreshFollowsTheAlarm(_PlanFileCase):
    NOW = real_datetime.datetime(2026, 9, 28, 3, 5, tzinfo=TZ)

    def run_refresh(self, live_alarm, planned_wake="2026-09-28T07:30:00-05:00"):
        if planned_wake:
            self.plan_file.write_text(json.dumps({
                "date": "2026-09-28", "wake_at": planned_wake, "fire_at": "2026-09-28T07:45:00-05:00"}))
        read = {"side_effect": live_alarm} if isinstance(live_alarm, Exception) else {"return_value": live_alarm}
        with patch.object(refresh.sync_alarm, "read_current_wake_time", **read), \
             patch.object(refresh.sync_alarm, "load_config", return_value=CONFIG), \
             patch.object(refresh, "get_tz", return_value=TZ), \
             patch.object(refresh, "datetime", wraps=real_datetime.datetime) as dt, \
             patch.object(nightly, "schedule_morning_briefing") as mock_sched:
            dt.now.return_value = self.NOW
            dt.combine = real_datetime.datetime.combine
            refresh.main()
        return mock_sched

    def test_alarm_moved_later_reschedules_the_briefing(self):
        mock_sched = self.run_refresh(real_datetime.time(9, 30))
        wake_dt = mock_sched.call_args[0][0]
        self.assertEqual(wake_dt.isoformat(), "2026-09-28T09:30:00-05:00")
        self.assertIs(mock_sched.call_args.kwargs["send_now_on_failure"], False)

    def test_unchanged_alarm_leaves_it_alone(self):
        self.run_refresh(real_datetime.time(7, 30)).assert_not_called()

    def test_alarm_off_leaves_it_alone(self):
        self.run_refresh(None).assert_not_called()

    def test_unreadable_hatch_leaves_it_alone(self):
        self.run_refresh(RuntimeError("hatch unreachable")).assert_not_called()

    def test_an_alarm_time_already_past_today_means_tomorrow_morning(self):
        mock_sched = self.run_refresh(real_datetime.time(2, 0))
        self.assertEqual(mock_sched.call_args[0][0].isoformat(), "2026-09-29T02:00:00-05:00")

    def test_an_evening_change_retimes_tomorrows_briefing(self):
        # The normal case, and the one that used to be dropped: he sets tomorrow's alarm tonight,
        # after the 9pm sync has already scheduled the briefing from the old wake time.
        self.NOW = real_datetime.datetime(2026, 9, 28, 20, 40, tzinfo=TZ)
        mock_sched = self.run_refresh(real_datetime.time(8, 30))
        self.assertEqual(mock_sched.call_args[0][0].isoformat(), "2026-09-29T08:30:00-05:00")

    def test_away_from_home_leaves_the_computed_schedule_alone(self):
        with patch.object(refresh, "is_away_from_home", return_value=True), \
             patch.object(nightly, "schedule_morning_briefing") as mock_sched:
            refresh.main()
        mock_sched.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
