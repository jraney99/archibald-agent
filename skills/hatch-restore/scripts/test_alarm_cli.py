#!/usr/bin/env python3
"""alarm.py works from "set it to 9am" alone, and never quietly does nothing.

    python3 test_alarm_cli.py          (offline: no Hatch login, no network)
"""
import atexit
import sys
import tempfile
import unittest
from datetime import time
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import alarm  # noqa: E402
from hatch_alarm_api import HatchAlarmError  # noqa: E402

ONE = [{"id": 100000003, "name": "Alarm_Default_Name", "enabled": True}]
TWO = ONE + [{"id": 111, "name": "Weekend", "enabled": False}]


# alarm.main() writes the shared alarm-time cache (alarm_cache.py). These tests drive it with
# made-up times — 9am, 9:30pm — so without this they would leave the user's REAL cache holding a
# time he never set: on 2026-10-08 a test run put 09:00 there and the morning-briefing and
# breakfast refreshes, run a minute later, scheduled his morning off it.
def _isolate_alarm_cache():
    tmp = tempfile.TemporaryDirectory()
    patcher = mock.patch.object(alarm.alarm_cache, "CACHE", Path(tmp.name) / "hatch_alarm_cache.json")
    patcher.start()
    return tmp, patcher


_CACHE_TMP, _CACHE_PATCHER = _isolate_alarm_cache()
atexit.register(_CACHE_PATCHER.stop)
atexit.register(_CACHE_TMP.cleanup)


class WakeTime(unittest.TestCase):
    def test_the_ways_it_gets_said(self):
        for said, want in [("09:00", time(9, 0)), ("9:00", time(9, 0)), ("9am", time(9, 0)), ("9 AM", time(9, 0)),
                           ("9:30am", time(9, 30)), ("9:30 pm", time(21, 30)), ("12am", time(0, 0)),
                           ("12pm", time(12, 0)), ("21:15", time(21, 15)), ("7:30 a.m.", time(7, 30))]:
            self.assertEqual(alarm.parse_wake_time(said), want, said)

    def test_nonsense_is_refused_not_guessed(self):
        for said in ("9", "25:00", "9:75", "13pm", "soon", "", "9:3"):
            with self.assertRaises(HatchAlarmError, msg=said):
                alarm.parse_wake_time(said)


class WhichAlarm(unittest.TestCase):
    def test_no_id_given_uses_the_configured_alarm(self):
        self.assertEqual(alarm.resolve_alarm_id(None, "100000003", TWO), ("100000003", ""))

    def test_the_2026_09_20_stale_id_falls_back_and_says_so(self):
        chosen, note = alarm.resolve_alarm_id("100000001", "100000003", TWO)
        self.assertEqual(chosen, "100000003")
        self.assertIn("100000001 not found", note)

    def test_stale_config_too_adopts_the_only_alarm(self):
        chosen, note = alarm.resolve_alarm_id(None, "999", ONE)
        self.assertEqual(chosen, "100000003")
        self.assertIn("only alarm", note)

    def test_an_explicit_valid_id_wins_silently(self):
        self.assertEqual(alarm.resolve_alarm_id("111", "100000003", TWO), ("111", ""))

    def test_ambiguous_is_an_error_that_lists_the_alarms(self):
        with self.assertRaises(HatchAlarmError) as e:
            alarm.resolve_alarm_id(None, "999", TWO)
        self.assertIn("100000003", str(e.exception))
        self.assertIn("111", str(e.exception))


class CommandLine(unittest.TestCase):
    def run_cli(self, *argv, alarms=ONE):
        calls = []
        # OFFLINE MEANS OFFLINE. 2026-09-20: alarm.py gained a follow-on step (record the time,
        # retime the lights) after this test was written; unmocked, this test wrote a REAL
        # "wake at 9:00 tomorrow" record that the 9pm sync would have obeyed, and ran the real
        # light retime. Both are stubbed here; test_the_tests_leave_no_record proves it.
        with mock.patch.object(alarm.wake_override, "record", return_value={"for_date": "2026-01-01"}), \
             mock.patch.object(alarm.wake_override, "remember_baseline"), \
             mock.patch.object(alarm.subprocess, "run", return_value=mock.Mock(returncode=0, stdout="", stderr="")), \
             mock.patch.object(sys, "argv", ["alarm.py", *argv]), \
             mock.patch.object(alarm, "load_alarm_config", return_value={"device": "restore", "alarm_id": "100000003"}), \
             mock.patch.object(alarm, "resolve_mac", return_value="AA:BB"), \
             mock.patch.object(alarm.alarm_api, "login", return_value="t"), \
             mock.patch.object(alarm.alarm_api, "fetch_alarms", return_value=alarms), \
             mock.patch.object(alarm.alarm_api, "set_alarm_wake_time",
                               side_effect=lambda tok, mac, a, t: calls.append((a["id"], t)) or alarms), \
             mock.patch.object(alarm, "format_alarm", side_effect=lambda a: f"[{a['id']}]"):
            alarm.main()
        return calls

    def test_just_a_time_is_enough(self):
        # 2026-09-19: "--time 09:00" failed for a missing --device; "--device restore
        # --wake-time 09:00" then LISTED the alarms and changed nothing, exit 0.
        self.assertEqual(self.run_cli("--time", "9am"), [(100000003, time(9, 0))])
        self.assertEqual(self.run_cli("--wake-time", "09:00"), [(100000003, time(9, 0))])

    def test_the_tests_leave_no_record(self):
        record = alarm.wake_override.path(alarm.wake_override.NAME)
        before = record.read_text() if record.exists() else None
        self.run_cli("--time", "9am")
        self.assertEqual(record.read_text() if record.exists() else None, before,
                         "a test wrote or changed the real manual-wake record")

    def test_no_change_flag_still_just_lists(self):
        self.assertEqual(self.run_cli(), [])
        self.assertEqual(self.run_cli("--list", "--time", "9am"), [])

    def test_a_bad_time_stops_before_any_network_call(self):
        with mock.patch.object(alarm.alarm_api, "login") as login, mock.patch.object(sys, "argv", ["alarm.py", "--time", "soon"]), \
             mock.patch.object(alarm, "load_alarm_config", return_value={"device": "restore"}):
            with self.assertRaises(HatchAlarmError):
                alarm.main()
            login.assert_not_called()


if __name__ == "__main__":
    unittest.main()
