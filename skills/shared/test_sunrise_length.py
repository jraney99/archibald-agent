#!/usr/bin/env python3
"""The bedroom sunrise lasts exactly as long as the user set it, and ends at his wake time.

    python3 test_sunrise_length.py      (offline: no Hatch login, no cron edits)

2026-09-21: the first morning the sunrise actually ran, its 30 minutes woke him too early
and he asked for 7.5. The length is his setting (govee-lights/config/schedule_config.json →
sunrise_minutes), no longer a code constant or max(Hatch's own sunrise, floor).
"""
import json
import sys
import tempfile
import unittest
from datetime import datetime, time
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sync_bedroom_sunrise as s  # noqa: E402


class Setting(unittest.TestCase):
    def with_config(self, body):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        tmp.write(body)
        tmp.close()
        return mock.patch.object(s, "GOVEE_SCHEDULE_CONFIG", Path(tmp.name))

    def test_the_live_setting_is_seven_and_a_half_minutes(self):
        self.assertEqual(s.sunrise_seconds(), 450)

    def test_it_follows_the_config(self):
        with self.with_config(json.dumps({"sunrise_minutes": 10})):
            self.assertEqual(s.sunrise_seconds(), 600)

    def test_a_missing_or_broken_config_falls_back_instead_of_cancelling_the_morning(self):
        with self.with_config("{not json"):
            self.assertEqual(s.sunrise_seconds(), 450)
        with self.with_config(json.dumps({})):
            self.assertEqual(s.sunrise_seconds(), 450)

    def test_nonsense_values_are_clamped(self):
        with self.with_config(json.dumps({"sunrise_minutes": 0})):
            self.assertEqual(s.sunrise_seconds(), 60)
        with self.with_config(json.dumps({"sunrise_minutes": 600})):
            self.assertEqual(s.sunrise_seconds(), 3600)


class Plan(unittest.TestCase):
    def plan(self, hatch_sunrise_seconds):
        alarm = {"id": 1, "enabled": True}
        wake = datetime(2026, 9, 22, 7, 30)
        with mock.patch.object(s, "load_device_map", return_value={"restore": {"mac": "AA"}}), \
             mock.patch.object(s.HATCH_CONFIG.__class__, "read_text",
                               return_value=json.dumps({"device": "restore", "alarm_id": "1"})), \
             mock.patch.object(s.alarm_api, "login", return_value="t"), \
             mock.patch.object(s.alarm_api, "fetch_alarms", return_value=[alarm]), \
             mock.patch.object(s, "effective_wake_datetime", return_value=wake), \
             mock.patch.object(s, "_alarm_sunrise_duration_seconds", return_value=hatch_sunrise_seconds):
            return s.read_plan()

    def test_it_starts_seven_and_a_half_minutes_before_a_730_wake(self):
        p = self.plan(300)
        self.assertEqual(p["ramp_duration"], 450)
        self.assertEqual(p["ramp_start_dt"].time(), time(7, 22, 30))
        self.assertEqual(p["wake_dt"].time(), time(7, 30))

    def test_the_hatch_apps_own_sunrise_length_no_longer_stretches_it(self):
        # was max(hatch, 30 min): a 20-minute Hatch sunrise would have made this 20 minutes
        self.assertEqual(self.plan(1200)["ramp_duration"], 450)
        self.assertEqual(self.plan(60)["ramp_duration"], 450)


class MorningOff(unittest.TestCase):
    def test_argv_carries_its_own_time_for_the_misfire_guard(self):
        # the nightly `cron edit --command-argv` replaces the whole argv: the guard's time
        # must be the new off time, or tomorrow's real run would be skipped as a misfire
        argv = s.morning_off_argv("08:00")
        self.assertTrue(argv[1].endswith("govee-lights/scripts/control.py"))
        self.assertTrue(Path(argv[1]).is_absolute())
        self.assertEqual(argv[2:], ["--light", "bedroom", "--off",
                                    "--expected-start", "08:00", "--late-grace-min", "120"])


if __name__ == "__main__":
    unittest.main()
