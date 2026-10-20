#!/usr/bin/env python3
"""
Regression test for the 2026-08-23 stale-title bug: sync_alarm.py's
earliest_requirement() computed the correct wake/leave time from an event's
real `dateTime`, but the nightly notification quoted the event's raw title
verbatim. When an organizer moves a meeting without renaming it (e.g. a
colleague's "Breakfast ... - 9am" invite actually running 8-9am), the message
read as self-contradictory: "means leaving by 7:45 AM" right next to a
title that says "9am". Fixed by threading the event's actual computed
start time through as its own field so callers can show it explicitly
instead of trusting the title string.

No network access — drive_time.leave_by_minutes is mocked.

Run manually after touching sync_alarm.py, nightly_wake_and_workout_sync.py,
or sync_alarm_and_notify.py:

    python3 test_sync_alarm.py
"""
import importlib.util
import json
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


sync_alarm = _load("sync_alarm", _SCRIPTS_DIR / "sync_alarm.py")

# compute_and_apply now reads and writes wake state (wake_override: the time the user set, and the
# baseline for spotting a change made on the Hatch itself). 2026-09-20: run unsandboxed, these
# tests wrote a REAL baseline ("05:25") that the live 9pm sync would have compared against.
# Every test in this file gets a throwaway state folder instead.
_STATE_SANDBOX = tempfile.TemporaryDirectory()
_STATE_PATCH = patch.object(sync_alarm.wake_override, "path",
                            side_effect=lambda name: Path(_STATE_SANDBOX.name) / name)


def setUpModule():
    _STATE_PATCH.start()


def tearDownModule():
    _STATE_PATCH.stop()
    _STATE_SANDBOX.cleanup()


TZ = ZoneInfo("America/Chicago")

CONFIG = {
    "golf_title_keywords": ["golf", "tee time"],
    "golf_arrival_buffer_minutes": 45,
    "default_arrival_buffer_minutes": 5,
    "wake_before_leave_minutes": 45,
    "default_wake_time": "08:30",
    "device": "bedroom",
    "alarm_id": "1",
}

# Title says "9am" but the real dateTime (like the colleague's rescheduled
# breakfast invite) is 8:00 AM — the exact shape of the real incident.
STALE_TITLE_EVENT = {
    "summary": "Breakfast w. a colleague - Example Cafe - 9am",
    "location": "Example Cafe, 100 Example St, Example City",
    "start": {"dateTime": "2026-08-24T08:00:00-05:00"},
    "end": {"dateTime": "2026-08-24T09:00:00-05:00"},
}


class TestEarliestRequirementReportsRealStartTime(unittest.TestCase):
    def test_event_start_dt_matches_actual_dateTime_not_title_text(self):
        with patch.object(sync_alarm, "leave_by_minutes", return_value=15):
            wake_dt, title, leave_dt, start_dt = sync_alarm.earliest_requirement(
                [STALE_TITLE_EVENT], TZ, CONFIG
            )

        # The title still contains the stale "9am" text (untouched, since
        # we don't try to parse/rewrite free-text titles) ...
        self.assertIn("9am", title)
        # ... but the returned start_dt must reflect the real 8:00 AM
        # dateTime regardless of what the title claims.
        self.assertEqual(start_dt.hour, 8)
        self.assertEqual(start_dt.minute, 0)
        self.assertEqual(leave_dt.hour, 7)
        self.assertEqual(leave_dt.minute, 45)

    def test_compute_and_apply_result_includes_event_start_fmt(self):
        with patch.object(sync_alarm, "leave_by_minutes", return_value=15), \
             patch.object(sync_alarm, "is_away_from_home", return_value=False), \
             patch.object(sync_alarm, "_device_and_token", return_value=("aa:bb", "tok")), \
             patch.object(sync_alarm.alarm_api, "fetch_alarms", return_value=[{"id": "1", "enabled": True}]), \
             patch.object(sync_alarm.alarm_api, "set_alarm_wake_time"):
            result = sync_alarm.compute_and_apply(CONFIG, TZ, [STALE_TITLE_EVENT])

        self.assertFalse(result["is_default"])
        self.assertIn("9am", result["event_title"])
        # This is the field the notification messages now use so the
        # message never asserts a leave-by time next to a contradictory
        # title-only time reference.
        self.assertEqual(result["event_start_fmt"], "8:00 AM")


# Real shape of the 2026-09-08 miss: the email-monitor daemon's
# "Leave for <APT>" drive-time block. Its start (7:00 AM) is ALREADY the
# leave-home time computed backward from a 9:45 AM flight, and its location
# is the origin the user leaves from. Feeding it through the normal drive-time
# subtraction double-counted the commute — a correct 7:00 AM block became a
# 5:37 AM alarm + a self-contradictory "leave by 6:22 AM".
LEAVE_FOR_BLOCK = {
    "summary": "Leave for XYZ <> the user @ Hotel",
    "location": "100 Example St, Example City",  # origin, not XYZ
    "start": {"dateTime": "2026-08-24T07:00:00-05:00"},
    "end": {"dateTime": "2026-08-24T07:30:00-05:00"},
}


class TestLeaveForBlockNotDoubleCounted(unittest.TestCase):
    def test_block_start_is_the_leave_time(self):
        # leave_by_minutes must NOT be consulted for a "Leave for" block.
        with patch.object(sync_alarm, "leave_by_minutes",
                          side_effect=AssertionError("drive time should not be computed for a Leave-for block")):
            wake_dt, title, leave_dt, start_dt = sync_alarm.earliest_requirement(
                [LEAVE_FOR_BLOCK], TZ, CONFIG
            )
        self.assertEqual(leave_dt, start_dt)          # leave == block start (7:00 AM)
        self.assertEqual(leave_dt.hour, 7)
        self.assertEqual(leave_dt.minute, 0)
        self.assertEqual(wake_dt.hour, 6)             # 7:00 − 45 min
        self.assertEqual(wake_dt.minute, 15)

    def test_compute_and_apply_uses_block_start(self):
        with patch.object(sync_alarm, "is_away_from_home", return_value=True), \
             patch.object(sync_alarm, "_device_and_token", return_value=("aa:bb", "tok")), \
             patch.object(sync_alarm.alarm_api, "fetch_alarms", return_value=[{"id": "1", "enabled": True}]), \
             patch.object(sync_alarm.alarm_api, "set_alarm_enabled"), \
             patch.object(sync_alarm.alarm_api, "set_alarm_wake_time"):
            result = sync_alarm.compute_and_apply(CONFIG, TZ, [LEAVE_FOR_BLOCK])
        self.assertFalse(result["is_default"])
        self.assertEqual(result["wake_time_fmt"], "6:15 AM")
        self.assertEqual(result["leave_time_fmt"], "7:00 AM")
        self.assertEqual(result["event_start_fmt"], "7:00 AM")


# Real shape of the 2026-09-01 miss: a flight on tomorrow's calendar the
# night before the user flies out, while is_away_from_home() still reads False.
FLIGHT_EVENT = {
    "summary": "WN 100 DAL → XYZ <> the user @ Dallas Love Field",
    "location": "Dallas Love Field, Dallas, TX",
    "start": {"dateTime": "2026-08-24T06:25:00-05:00"},
    "end": {"dateTime": "2026-08-24T09:40:00-05:00"},
}


class TestFlightTomorrowSignal(unittest.TestCase):
    """A flight on tomorrow's calendar makes the wake travel-sensitive on
    its own — no dependence on is_away_from_home(), which doesn't flip until
    detect_travel.py runs mid-trip. See feedback-travel-detection-lag."""

    def test_flight_event_detected(self):
        self.assertTrue(sync_alarm.flight_tomorrow([FLIGHT_EVENT]))

    def test_non_flight_event_not_detected(self):
        self.assertFalse(sync_alarm.flight_tomorrow([STALE_TITLE_EVENT]))

    def test_all_day_flight_placeholder_ignored(self):
        all_day = {"summary": "DAL → XYZ trip", "start": {"date": "2026-08-24"}}
        self.assertFalse(sync_alarm.flight_tomorrow([all_day]))

    def test_compute_and_apply_flags_flight_and_keeps_hatch_when_home(self):
        with patch.object(sync_alarm, "leave_by_minutes", return_value=15), \
             patch.object(sync_alarm, "is_away_from_home", return_value=False), \
             patch.object(sync_alarm, "_device_and_token", return_value=("aa:bb", "tok")), \
             patch.object(sync_alarm.alarm_api, "fetch_alarms", return_value=[{"id": "1", "enabled": True}]), \
             patch.object(sync_alarm.alarm_api, "set_alarm_enabled") as mock_toggle, \
             patch.object(sync_alarm.alarm_api, "set_alarm_wake_time") as mock_retime:
            result = sync_alarm.compute_and_apply(CONFIG, TZ, [FLIGHT_EVENT])

        self.assertTrue(result["flight_tomorrow"])
        self.assertFalse(result["away"])
        mock_toggle.assert_not_called()      # home tonight — Hatch stays on
        mock_retime.assert_called_once()     # and gets the early flight wake time


class TestAlarmDisabledWhileAwayFromHome(unittest.TestCase):
    """When the user's travelling, the nightly sync must turn the Hatch alarm
    OFF (it's sitting in an empty house) but STILL compute tomorrow's wake
    time and hand it back with away=True, so the caller can tell him what to
    set his phone alarm for + schedule a backup nudge. See the away branch
    in compute_and_apply."""

    def test_enabled_alarm_disabled_wake_still_computed(self):
        with patch.object(sync_alarm, "leave_by_minutes", return_value=15), \
             patch.object(sync_alarm, "is_away_from_home", return_value=True), \
             patch.object(sync_alarm, "_device_and_token", return_value=("aa:bb", "tok")), \
             patch.object(sync_alarm.alarm_api, "fetch_alarms", return_value=[{"id": "1", "enabled": True}]), \
             patch.object(sync_alarm.alarm_api, "set_alarm_enabled") as mock_disable, \
             patch.object(sync_alarm.alarm_api, "set_alarm_wake_time") as mock_retime:
            result = sync_alarm.compute_and_apply(CONFIG, TZ, [STALE_TITLE_EVENT])

        mock_disable.assert_called_once()
        self.assertIs(mock_disable.call_args[0][3], False)
        mock_retime.assert_not_called()          # never write a wake time to a device the user isn't near
        self.assertTrue(result["away"])
        self.assertFalse(result["sync_failed"])
        self.assertIsNotNone(result["wake_dt_iso"])   # computed anyway, for the phone-alarm brief
        self.assertFalse(result["is_default"])        # STALE_TITLE_EVENT forces an early wake
        self.assertEqual(result["event_start_fmt"], "8:00 AM")

    def test_already_disabled_alarm_is_left_alone(self):
        with patch.object(sync_alarm, "leave_by_minutes", return_value=15), \
             patch.object(sync_alarm, "is_away_from_home", return_value=True), \
             patch.object(sync_alarm, "_device_and_token", return_value=("aa:bb", "tok")), \
             patch.object(sync_alarm.alarm_api, "fetch_alarms", return_value=[{"id": "1", "enabled": False}]), \
             patch.object(sync_alarm.alarm_api, "set_alarm_enabled") as mock_toggle, \
             patch.object(sync_alarm.alarm_api, "set_alarm_wake_time") as mock_retime:
            result = sync_alarm.compute_and_apply(CONFIG, TZ, [STALE_TITLE_EVENT])

        mock_toggle.assert_not_called()
        mock_retime.assert_not_called()
        self.assertTrue(result["away"])

    def test_back_home_re_enables_disabled_alarm(self):
        with patch.object(sync_alarm, "leave_by_minutes", return_value=15), \
             patch.object(sync_alarm, "is_away_from_home", return_value=False), \
             patch.object(sync_alarm, "_device_and_token", return_value=("aa:bb", "tok")), \
             patch.object(sync_alarm.alarm_api, "fetch_alarms", return_value=[{"id": "1", "enabled": False}]), \
             patch.object(sync_alarm.alarm_api, "set_alarm_enabled") as mock_toggle, \
             patch.object(sync_alarm.alarm_api, "set_alarm_wake_time") as mock_retime:
            result = sync_alarm.compute_and_apply(CONFIG, TZ, [])

        mock_toggle.assert_called_once()
        self.assertIs(mock_toggle.call_args[0][3], True)
        mock_retime.assert_called_once()
        self.assertFalse(result["away"])


class TestFindAlarmAdoptsRecreatedAlarm(unittest.TestCase):
    """2026-09-18: the alarm was recreated in the Hatch app, got a new id, and
    the pinned alarm_id failed the nightly sync ("Alarm 100000002 not found")."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.config_path = Path(self._tmp.name) / "alarm_config.json"
        self.config_path.write_text('{\n  "device": "restore",\n  "alarm_id": "1",\n  "golf_title_keywords": ["golf"]\n}\n')
        self._patch = patch.object(sync_alarm, "CONFIG_PATH", self.config_path)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def test_pinned_id_present_is_used_and_config_untouched(self):
        before = self.config_path.read_text()
        alarm = sync_alarm._find_alarm([{"id": 2}, {"id": 1}], dict(CONFIG))
        self.assertEqual(alarm["id"], 1)
        self.assertEqual(self.config_path.read_text(), before)

    def test_missing_id_adopts_only_alarm_and_persists(self):
        config = dict(CONFIG)
        alarm = sync_alarm._find_alarm([{"id": 100000003}], config)
        self.assertEqual(alarm["id"], 100000003)
        self.assertEqual(config["alarm_id"], "100000003")
        saved = self.config_path.read_text()
        self.assertEqual(json.loads(saved)["alarm_id"], "100000003")
        self.assertIn('"golf_title_keywords": ["golf"]', saved)  # formatting preserved

    def test_missing_id_with_several_alarms_still_raises(self):
        before = self.config_path.read_text()
        with self.assertRaises(sync_alarm.HatchError):
            sync_alarm._find_alarm([{"id": 2}, {"id": 3}], dict(CONFIG))
        self.assertEqual(self.config_path.read_text(), before)

    def test_missing_id_with_no_alarms_raises(self):
        with self.assertRaises(sync_alarm.HatchError):
            sync_alarm._find_alarm([], dict(CONFIG))


if __name__ == "__main__":
    unittest.main()
