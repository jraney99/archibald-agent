#!/usr/bin/env python3
"""
Regression tests for the 2026-09-19 change: the Breakfast Anchor reminder
follows the alarm that's actually on the Hatch. On 2026-09-19 it fired at
8:00 (config default 7:30 + 30) while the Hatch was set to 9:00 — the nightly
sync had failed, and nothing re-read the device afterwards.

Covers the plan file nightly_wake_and_workout_sync.py now writes, and
refresh_breakfast_anchor.py's pre-dawn re-check (which must never send a
Telegram message).

No network/cron access. subprocess.run and the Hatch read are mocked; notify() is
additionally fenced by TELEGRAM_SEND_DISABLED, set below before the import, so a
test that reaches it raises instead of messaging the user.

Run manually after touching either script:

    python3 test_breakfast_anchor_refresh.py
"""
import datetime as real_datetime
import importlib.util
import json
import os

# 2026-10-06: set BEFORE the module under test is imported. test-all.mjs exports this,
# but a file run directly (`python3 test_breakfast_anchor_refresh.py`) does not inherit
# it -- and on 10-06 two tests here that do not stub notify() reached its error branch
# after a code change and messaged the user four times for real. A test file must not depend
# on how it was invoked to stay silent.
os.environ.setdefault("TELEGRAM_SEND_DISABLED", "1")

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
refresh = _load("refresh_breakfast_anchor", _SCRIPTS_DIR / "refresh_breakfast_anchor.py")

TZ = ZoneInfo("America/Chicago")
CONFIG = {"default_wake_time": "07:30"}
LIST_EMPTY = subprocess.CompletedProcess([], 0, stdout=json.dumps({"jobs": []}), stderr="")
ADD_OK = subprocess.CompletedProcess([], 0, stdout=json.dumps({"id": "abc123"}), stderr="")
ADD_FAIL = subprocess.CompletedProcess([], 1, stdout="", stderr="gateway unreachable")

# 2026-10-06: the five eating windows are the five STANDING crons, retimed nightly —
# there are no one-shots any more, so there is nothing for a backstop to race. A
# responder that reads the command is used instead of a fixed call sequence, because the
# sequence is an implementation detail and pinning it made these tests brittle.
MEAL_JOBS = [{"id": f"job-slot{n}", "name": f"nutrition-meal-reminder-slot{n}", "enabled": True}
             for n in range(1, 6)]


def cron_responder(disabled: set = frozenset(), edit_fails: set = frozenset(),
                   missing: set = frozenset()):
    def run(argv, *a, **k):
        if "list" in argv:
            jobs = [{**j, "enabled": j["name"] not in disabled}
                    for j in MEAL_JOBS if j["name"] not in missing]
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps({"jobs": jobs}), stderr="")
        if "edit" in argv:
            job_id = argv[argv.index("edit") + 1]
            if job_id in edit_fails:
                return subprocess.CompletedProcess(argv, 1, stdout="", stderr="gateway unreachable")
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps({"id": job_id}), stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="{}", stderr="")
    return run


def edits(mock_run):
    """Every `cron edit` the call made, as (job_id, cron expression)."""
    out = []
    for call in mock_run.call_args_list:
        argv = call[0][0]
        if isinstance(argv, list) and "edit" in argv and "--cron" in argv:
            out.append((argv[argv.index("edit") + 1], argv[argv.index("--cron") + 1]))
    return out


class _PlanFileCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.plan_file = Path(tmp.name) / "breakfast_anchor_plan.json"
        for target, attr, value in [
            (nightly, "BREAKFAST_PLAN_FILE", self.plan_file),
            (nightly, "is_away_from_home", lambda *a, **k: False),
            (refresh, "is_away_from_home", lambda *a, **k: False),
        ]:
            p = patch.object(target, attr, value)
            p.start()
            self.addCleanup(p.stop)


class TestSchedulingWritesThePlan(_PlanFileCase):
    def test_plan_records_the_alarm_and_every_slot(self):
        # 2026-10-05: all five windows are scheduled off the alarm, not just breakfast.
        # A 9:00 alarm puts breakfast at 9:30, and the rest squeeze to land dinner by 8pm.
        wake_dt = real_datetime.datetime(2026, 9, 20, 9, 0, tzinfo=TZ)
        with patch.object(nightly.subprocess, "run", side_effect=cron_responder()) as mock_run:
            nightly.schedule_breakfast_anchor_reminder(wake_dt, TZ, CONFIG)

        made = edits(mock_run)
        self.assertEqual(len(made), 5, "one retime per eating window, and no job added")
        self.assertEqual([j for j, _ in made], [f"job-slot{n}" for n in range(1, 6)])
        self.assertEqual(made[0][1], "30 9 * * *", "breakfast follows the 9:00 alarm")
        self.assertNotIn("add", [c[0][0][1] if len(c[0][0]) > 1 else "" for c in mock_run.call_args_list],
                         "no one-shot is created any more")
        plan = json.loads(self.plan_file.read_text())
        self.assertEqual(plan["wake_at"], "2026-09-20T09:00:00-05:00")
        self.assertEqual(plan["fire_at"], "2026-09-20T09:30:00-05:00")
        self.assertEqual(sorted(plan["slots"]), ["1", "2", "3", "4", "5"])
        self.assertEqual(plan["slots"]["1"], "2026-09-20T09:30:00-05:00")
        self.assertTrue(plan["slots"]["5"].startswith("2026-09-20T20:00"), plan["slots"]["5"])
        self.assertTrue(plan["squeezed"])

    def test_a_normal_alarm_reproduces_the_old_fixed_times(self):
        # The user chose "keep what I have now": on his 07:30 default nothing moves.
        wake_dt = real_datetime.datetime(2026, 9, 21, 7, 30, tzinfo=TZ)
        with patch.object(nightly.subprocess, "run", side_effect=cron_responder()) as mock_run:
            nightly.schedule_breakfast_anchor_reminder(wake_dt, TZ, CONFIG)
        plan = json.loads(self.plan_file.read_text())
        self.assertEqual([plan["slots"][str(n)][11:16] for n in range(1, 6)],
                         ["08:00", "10:00", "13:00", "16:00", "19:00"])
        self.assertEqual([expr for _, expr in edits(mock_run)],
                         ["0 8 * * *", "0 10 * * *", "0 13 * * *", "0 16 * * *", "0 19 * * *"],
                         "the standing crons carry the plan's times")
        self.assertFalse(plan["squeezed"])

    def test_a_failed_retime_clears_the_plan_and_says_so(self):
        """The crons keep yesterday's times and still fire — close to right, and loud."""
        self.plan_file.write_text(json.dumps({"date": "2026-09-20"}))
        wake_dt = real_datetime.datetime(2026, 9, 20, 9, 0, tzinfo=TZ)
        with patch.object(nightly.subprocess, "run",
                          side_effect=cron_responder(edit_fails={"job-slot3"})), \
             patch.object(nightly, "notify") as mock_notify:
            nightly.schedule_breakfast_anchor_reminder(wake_dt, TZ, CONFIG)
        self.assertFalse(self.plan_file.exists())
        mock_notify.assert_called_once()

    def test_a_disabled_meal_cron_is_not_reported_as_scheduled(self):
        """`cron edit` succeeds on a disabled job, so its exit code proves nothing —
        the bedroom sunrise missed two mornings exactly this way (2026-10-06)."""
        wake_dt = real_datetime.datetime(2026, 9, 20, 9, 0, tzinfo=TZ)
        with patch.object(nightly.subprocess, "run",
                          side_effect=cron_responder(disabled={"nutrition-meal-reminder-slot4"})), \
             patch.object(nightly, "notify") as mock_notify:
            nightly.schedule_breakfast_anchor_reminder(wake_dt, TZ, CONFIG)
        self.assertFalse(self.plan_file.exists(), "a dead job is not a scheduled meal")
        self.assertIn("slot4", str(mock_notify.call_args))

    def test_a_missing_meal_cron_is_refused_rather_than_guessed(self):
        wake_dt = real_datetime.datetime(2026, 9, 20, 9, 0, tzinfo=TZ)
        with patch.object(nightly.subprocess, "run",
                          side_effect=cron_responder(missing={"nutrition-meal-reminder-slot2"})), \
             patch.object(nightly, "notify") as mock_notify:
            nightly.schedule_breakfast_anchor_reminder(wake_dt, TZ, CONFIG)
        mock_notify.assert_called_once()

    def test_pre_dawn_failure_never_sends_anything(self):
        wake_dt = real_datetime.datetime(2026, 9, 20, 9, 0, tzinfo=TZ)
        with patch.object(nightly.subprocess, "run",
                          side_effect=cron_responder(edit_fails={"job-slot1"})) as mock_run, \
             patch.object(nightly, "notify") as mock_notify:
            nightly.schedule_breakfast_anchor_reminder(wake_dt, TZ, CONFIG, send_now_on_failure=False)
        ran_sender = [c for c in mock_run.call_args_list
                      if "send_meal_reminder.py" in " ".join(map(str, c[0][0]))]
        self.assertEqual(ran_sender, [], "a 3am run must never send a meal reminder")
        mock_notify.assert_not_called()

    def test_away_from_home_clears_the_plan(self):
        self.plan_file.write_text(json.dumps({"date": "2026-09-20"}))
        with patch.object(nightly, "is_away_from_home", return_value=True), \
             patch.object(nightly.subprocess, "run", side_effect=cron_responder()):
            nightly.schedule_breakfast_anchor_reminder(None, TZ, CONFIG)
        self.assertFalse(self.plan_file.exists())


class TestPreDawnRefresh(_PlanFileCase):
    NOW = real_datetime.datetime(2026, 9, 20, 3, 0, tzinfo=TZ)

    def run_refresh(self, live_alarm, planned_wake="2026-09-20T07:30:00-05:00", argv=()):
        if planned_wake:
            self.plan_file.write_text(json.dumps({
                "date": "2026-09-20", "wake_at": planned_wake, "fire_at": "2026-09-20T08:00:00-05:00"}))
        read = {"side_effect": live_alarm} if isinstance(live_alarm, Exception) else {"return_value": live_alarm}
        with patch.object(sys, "argv", ["refresh_breakfast_anchor.py", *argv]), \
             patch.object(refresh, "within_slot", return_value=(True, "")), \
             patch.object(refresh, "get_tz", return_value=TZ), \
             patch.object(refresh.sync_alarm, "load_config", return_value=CONFIG), \
             patch.object(refresh.sync_alarm, "read_current_wake_time", **read), \
             patch.object(refresh, "datetime") as mock_dt, \
             patch.object(nightly, "schedule_breakfast_anchor_reminder") as mock_schedule:
            mock_dt.now.return_value = self.NOW
            mock_dt.combine = real_datetime.datetime.combine
            mock_dt.fromisoformat = real_datetime.datetime.fromisoformat
            refresh.main()
        return mock_schedule

    def test_unchanged_alarm_touches_nothing(self):
        self.run_refresh(real_datetime.time(7, 30)).assert_not_called()

    def test_alarm_moved_at_bedtime_reschedules_off_the_live_time(self):
        mock_schedule = self.run_refresh(real_datetime.time(9, 0))
        mock_schedule.assert_called_once()
        wake_dt, _tz, _config = mock_schedule.call_args[0]
        self.assertEqual(wake_dt, real_datetime.datetime(2026, 9, 20, 9, 0, tzinfo=TZ))
        self.assertEqual(mock_schedule.call_args[1], {"send_now_on_failure": False})

    def test_no_plan_on_file_schedules_from_the_live_alarm(self):
        self.run_refresh(real_datetime.time(7, 30), planned_wake=None).assert_called_once()

    def test_alarm_switched_off_leaves_the_schedule_alone(self):
        self.run_refresh(None).assert_not_called()

    def test_hatch_unreachable_leaves_the_schedule_alone(self):
        self.run_refresh(RuntimeError("login failed")).assert_not_called()

    def test_away_from_home_does_nothing(self):
        with patch.object(refresh, "is_away_from_home", return_value=True):
            self.run_refresh(real_datetime.time(9, 0)).assert_not_called()

    def test_catch_up_run_outside_the_pre_dawn_slot_does_nothing(self):
        with patch.object(sys, "argv", ["refresh_breakfast_anchor.py"]), \
             patch.object(refresh, "within_slot", return_value=(False, "skipped misfire")), \
             patch.object(refresh.sync_alarm, "read_current_wake_time") as mock_read, \
             patch.object(nightly, "schedule_breakfast_anchor_reminder") as mock_schedule:
            refresh.main()
        mock_read.assert_not_called()
        mock_schedule.assert_not_called()


class TestAlarmChangeFollower(_PlanFileCase):
    """2026-10-06: an evening alarm change could not move the first meal reminder at all — the
    pre-dawn guard blocked the follower run, so it sat on the old wake time until 3am."""

    NOW = real_datetime.datetime(2026, 9, 20, 23, 54, tzinfo=TZ)

    def run_refresh(self, live_alarm, argv=(), planned_wake="2026-09-20T07:30:00-05:00"):
        self.plan_file.write_text(json.dumps({
            "date": "2026-09-20", "wake_at": planned_wake, "fire_at": "2026-09-20T08:00:00-05:00"}))
        with patch.object(sys, "argv", ["refresh_breakfast_anchor.py", *argv]), \
             patch.object(refresh, "within_slot", return_value=(False, "outside 01:00-06:00")), \
             patch.object(refresh, "get_tz", return_value=TZ), \
             patch.object(refresh.sync_alarm, "load_config", return_value=CONFIG), \
             patch.object(refresh.sync_alarm, "read_current_wake_time", return_value=live_alarm), \
             patch.object(refresh, "datetime") as mock_dt, \
             patch.object(nightly, "schedule_breakfast_anchor_reminder") as mock_schedule:
            mock_dt.now.return_value = self.NOW
            mock_dt.combine = real_datetime.datetime.combine
            mock_dt.fromisoformat = real_datetime.datetime.fromisoformat
            refresh.main()
        return mock_schedule

    def test_the_follower_flag_runs_despite_the_hour(self):
        mock_schedule = self.run_refresh(real_datetime.time(8, 30), argv=("--from-alarm-change",))
        mock_schedule.assert_called_once()

    def test_an_evening_change_schedules_TOMORROW_morning(self):
        mock_schedule = self.run_refresh(real_datetime.time(8, 30), argv=("--from-alarm-change",))
        wake_dt, _tz, _config = mock_schedule.call_args[0]
        self.assertEqual(wake_dt, real_datetime.datetime(2026, 9, 21, 8, 30, tzinfo=TZ))

    def test_without_the_flag_the_hour_still_guards_catch_up_runs(self):
        self.run_refresh(real_datetime.time(8, 30)).assert_not_called()


if __name__ == "__main__":
    unittest.main()
