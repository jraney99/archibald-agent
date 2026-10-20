#!/usr/bin/env python3
"""The manual alarm path must obey the same calendar rule as the 9pm sync.

2026-10-13: an alarm set by hand for 12:00 PM sat on top of an 11:30 AM golf
booking made two hours earlier. sync_alarm.compute_and_apply has always applied
"the calendar wins when it needs him up EARLIER than the time he set"; alarm.py
went straight to the device and recorded an override that beat the calendar.

    python3 test_alarm_calendar_guard.py
"""
import importlib.util
import sys
import unittest
from datetime import date, datetime, time as dtime
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent.parent / "shared"))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


alarm = _load("alarm", _HERE / "alarm.py")

TZ = alarm.get_tz()
NOW = datetime(2026, 10, 13, 0, 53, tzinfo=TZ)
GOLF_START = datetime(2026, 10, 13, 11, 30, tzinfo=TZ)
REQUIRED = datetime(2026, 10, 13, 9, 55, tzinfo=TZ)


class CalendarRequiredWake(unittest.TestCase):
    def _patched(self, required_dt):
        req = (required_dt, "Golf <> the user @ Example Sim Bar", None, GOLF_START) \
            if required_dt else (None, None, None, None)
        import sync_alarm
        return (mock.patch.object(alarm, "events_on", return_value=[{"x": 1}]),
                mock.patch.object(sync_alarm, "earliest_requirement", return_value=req))

    def test_returns_the_calendars_requirement(self):
        a, b = self._patched(REQUIRED)
        with a, b:
            got, title, start = alarm.calendar_required_wake(dtime(12, 0), NOW, {}, TZ)
        self.assertEqual(got, dtime(9, 55))
        self.assertEqual(title, "Golf <> the user @ Example Sim Bar")
        self.assertEqual(start, GOLF_START)

    def test_an_empty_calendar_constrains_nothing(self):
        a, b = self._patched(None)
        with a, b:
            got, title, start = alarm.calendar_required_wake(dtime(12, 0), NOW, {}, TZ)
        self.assertEqual((got, title, start), (None, None, None))


class _Args:
    """Only what main() reads before the guard."""
    def __init__(self, wake_time, ignore_calendar=False):
        self.device = "restore"
        self.list = False
        self.enable = self.disable = False
        self.wake_time = wake_time
        self.weekdays = None
        self.no_follow = False
        self.ignore_calendar = ignore_calendar
        self.alarm_id = None


class TheDeviceIsNeverTouchedWhenTheTimeIsTooLate(unittest.TestCase):
    def _run_main(self, wake_time, ignore_calendar=False, required=REQUIRED):
        logins = []
        req = (required, "Golf <> the user @ Example Sim Bar", None, GOLF_START) \
            if required else (None, None, None, None)
        import sync_alarm
        with mock.patch.object(alarm, "parse_args_for_test", create=True), \
             mock.patch("argparse.ArgumentParser.parse_args",
                        return_value=_Args(wake_time, ignore_calendar)), \
             mock.patch.object(alarm, "events_on", return_value=[{"x": 1}]), \
             mock.patch.object(sync_alarm, "earliest_requirement", return_value=req), \
             mock.patch.object(alarm, "resolve_mac", return_value="mac"), \
             mock.patch.object(alarm.alarm_api, "login",
                               side_effect=lambda *a, **k: logins.append(1) or "tok"), \
             mock.patch.object(alarm.alarm_api, "fetch_alarms",
                               return_value=[{"id": "1", "enabled": True}]), \
             mock.patch.object(alarm, "resolve_alarm_id", return_value=("1", "")), \
             mock.patch.object(alarm.alarm_api, "set_alarm_wake_time",
                               return_value=[{"id": "1", "enabled": True}]), \
             mock.patch.object(alarm, "follow_wake_change", return_value=[]), \
             mock.patch.object(alarm.alarm_cache, "note_plan"), \
             mock.patch.object(alarm.alarm_cache, "note"), \
             mock.patch.object(alarm.wake_override, "record",
                               return_value={"for_date": "2026-10-13"}), \
             mock.patch.object(alarm.wake_override, "remember_baseline"):
            try:
                alarm.main()
                return None, logins
            except alarm.HatchError as e:
                return str(e), logins

    def test_a_too_late_time_is_refused_before_any_login(self):
        err, logins = self._run_main("12:00")
        self.assertIsNotNone(err, "12:00 PM was accepted over an 11:30 AM commitment")
        self.assertIn("9:55", err)
        self.assertIn("Example Sim Bar", err)
        self.assertEqual(logins, [], "it reached the Hatch before checking the calendar")

    def test_the_message_says_nothing_changed(self):
        err, _ = self._run_main("12:00")
        self.assertIn("Nothing was changed", err)

    def test_an_earlier_time_is_accepted(self):
        err, logins = self._run_main("08:00")
        self.assertIsNone(err, f"a safe time was refused: {err}")
        self.assertEqual(len(logins), 1)

    def test_exactly_the_required_time_is_accepted(self):
        err, _ = self._run_main("09:55")
        self.assertIsNone(err, f"the required time itself was refused: {err}")

    def test_ignore_calendar_is_an_explicit_escape_hatch(self):
        err, logins = self._run_main("12:00", ignore_calendar=True)
        self.assertIsNone(err)
        self.assertEqual(len(logins), 1)

    def test_an_empty_calendar_allows_a_late_alarm(self):
        err, _ = self._run_main("12:00", required=None)
        self.assertIsNone(err)


if __name__ == "__main__":
    unittest.main(verbosity=2)
