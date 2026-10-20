#!/usr/bin/env python3
"""A wake time the user sets himself survives the 9pm sync and reaches the lights at once.

    python3 test_wake_override.py        (offline: no Hatch, no calendar, no cron CLI)

2026-09-20: the 9pm sync reset the alarm to the default every night and the bedroom sunrise
was retimed only at 9:15pm, so the light came on at 7:00 whatever the alarm said — and the user
unplugged the lamp at night, which is what 56 of 70 "light failures" in a month really were.
"""
import atexit
import json
import sys
import tempfile
import unittest
from datetime import date, datetime, time, timedelta
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import alarm  # noqa: E402
import nightly_wake_and_workout_sync as nightly  # noqa: E402
import sync_alarm  # noqa: E402
import wake_override  # noqa: E402

TZ = ZoneInfo("America/Chicago")


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


class Override(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.file = Path(self.tmp.name) / "state" / "manual_wake.json"
        self.patch = mock.patch.object(wake_override, "path", return_value=self.file)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_which_morning_a_new_time_is_for(self):
        at = lambda h, m: datetime(2026, 9, 20, h, m, tzinfo=TZ)  # noqa: E731
        self.assertEqual(wake_override.wake_date(time(9, 0), at(20, 0)), date(2026, 9, 21))   # 8pm -> tomorrow
        self.assertEqual(wake_override.wake_date(time(9, 30), at(1, 7)), date(2026, 9, 20))   # 1am -> this morning
        self.assertEqual(wake_override.wake_date(time(7, 0), at(7, 0)), date(2026, 9, 21))    # already that time -> tomorrow

    def test_it_applies_to_that_morning_only(self):
        wake_override.record(time(9, 30), datetime(2026, 9, 20, 20, 0, tzinfo=TZ))
        self.assertEqual(wake_override.active(date(2026, 9, 21)), time(9, 30))
        self.assertIsNone(wake_override.active(date(2026, 9, 22)))   # expired by itself
        self.assertIsNone(wake_override.active(date(2026, 9, 20)))

    def test_a_missing_or_broken_file_never_blocks_the_sync(self):
        self.assertIsNone(wake_override.active(date(2026, 9, 21)))
        self.file.parent.mkdir(parents=True)
        self.file.write_text("{not json")
        self.assertIsNone(wake_override.active(date(2026, 9, 21)))


class ChangedOnTheHatchItself(unittest.TestCase):
    """He sets the Hatch in its own app; nothing tells us. Device != baseline => his change."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        files = {wake_override.NAME: Path(self.tmp.name) / "manual_wake.json",
                 wake_override.BASELINE: Path(self.tmp.name) / "wake_sync_state.json"}
        self.patch = mock.patch.object(wake_override, "path", side_effect=lambda n: files[n])
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_no_baseline_yet_infers_nothing(self):
        self.assertIsNone(wake_override.changed_on_device(time(9, 30)))

    def test_app_change_in_the_evening_is_his_time_for_tomorrow(self):
        wake_override.remember_baseline(time(7, 30), "pre-dawn read", datetime(2026, 9, 21, 3, 5, tzinfo=TZ))
        self.assertEqual(wake_override.changed_on_device(time(9, 30)), time(9, 30))

    def test_a_1am_change_for_this_morning_is_not_carried_into_tomorrow(self):
        # 9pm sync applied 7:30; at 1am he set 9:30 in the app; 3:05am pre-dawn read sees 9:30.
        wake_override.remember_baseline(time(7, 30), "9pm-sync", datetime(2026, 9, 20, 21, 0, tzinfo=TZ))
        wake_override.remember_baseline(time(9, 30), "pre-dawn read", datetime(2026, 9, 21, 3, 5, tzinfo=TZ))
        self.assertIsNone(wake_override.changed_on_device(time(9, 30)))   # unchanged since this morning

    def test_untouched_device_and_unreadable_device_infer_nothing(self):
        wake_override.remember_baseline(time(7, 30), "9pm-sync", datetime(2026, 9, 20, 21, 0, tzinfo=TZ))
        self.assertIsNone(wake_override.changed_on_device(time(7, 30)))
        self.assertIsNone(wake_override.changed_on_device(None))


class SunriseAsksTheHatchWhenItFires(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "govee-lights" / "scripts"))
        import sunrise_ramp
        self.ramp = sunrise_ramp
        self.now = datetime(2026, 9, 21, 7, 0, 5, tzinfo=TZ)

    def plan(self, wake, enabled=True, stored_date=date(2026, 9, 18)):
        # the Hatch stores a STALE date on its alarm; only the clock time means anything
        wake_dt = datetime.combine(stored_date, wake)
        return lambda: {"enabled": enabled, "wake_dt": wake_dt, "ramp_start_dt": wake_dt - timedelta(minutes=30)}

    def test_alarm_unchanged_ramps_as_scheduled(self):
        self.assertIsNone(self.ramp.hatch_verdict(self.now, read_plan=self.plan(time(7, 30)), retime=lambda: True))

    def test_the_2026_09_20_morning_alarm_moved_to_930_so_no_7am_light(self):
        retimed = []
        v = self.ramp.hatch_verdict(self.now, read_plan=self.plan(time(9, 30)), retime=lambda: retimed.append(1) or True)
        self.assertEqual(v, {"deferred": "Hatch now wakes 09:30; sunrise moved to 09:00", "retimed": True})
        self.assertEqual(retimed, [1])

    def test_alarm_off_means_no_sunrise(self):
        v = self.ramp.hatch_verdict(self.now, read_plan=self.plan(time(7, 30), enabled=False), retime=lambda: True)
        self.assertEqual(v, {"skipped": "Hatch alarm is off — no sunrise"})

    def test_a_few_minutes_difference_still_ramps(self):
        self.assertIsNone(self.ramp.hatch_verdict(self.now, read_plan=self.plan(time(7, 38)), retime=lambda: True))

    def test_an_unreadable_hatch_never_blocks_the_sunrise(self):
        def boom():
            raise RuntimeError("Hatch API down")
        with mock.patch("builtins.print"):
            self.assertIsNone(self.ramp.hatch_verdict(self.now, read_plan=boom, retime=lambda: True))


class NightlySync(unittest.TestCase):
    """compute_and_apply with the Hatch, the calendar and the clock stubbed out."""

    def run_sync(self, manual, required, device=None, baseline=None):
        applied = []
        tomorrow = datetime.now(TZ).date() + timedelta(days=1)
        req = (datetime.combine(tomorrow, required, tzinfo=TZ), "Golf <> the user @ Club",
               datetime.combine(tomorrow, required, tzinfo=TZ) + timedelta(minutes=45),
               datetime.combine(tomorrow, required, tzinfo=TZ) + timedelta(minutes=90)) if required else (None, None, None, None)
        the_alarm = {"id": 1, "enabled": True}
        device_dt = datetime.combine(date(2026, 9, 18), device) if device else None   # stale stored date, as in life
        self.remembered = []
        with mock.patch.object(sync_alarm.wake_override, "active", return_value=manual), \
             mock.patch.object(sync_alarm.wake_override, "baseline", return_value=baseline), \
             mock.patch.object(sync_alarm.wake_override, "remember_baseline",
                               side_effect=lambda t, source, now: self.remembered.append((t, source))), \
             mock.patch.object(sync_alarm.alarm_api, "effective_wake_datetime", return_value=device_dt), \
             mock.patch.object(sync_alarm, "earliest_requirement", return_value=req), \
             mock.patch.object(sync_alarm, "is_away_from_home", return_value=False), \
             mock.patch.object(sync_alarm, "_device_and_token", return_value=("mac", "tok")), \
             mock.patch.object(sync_alarm.alarm_api, "fetch_alarms", return_value=[the_alarm]), \
             mock.patch.object(sync_alarm, "_find_alarm", return_value=the_alarm), \
             mock.patch.object(sync_alarm, "flight_tomorrow", return_value=False), \
             mock.patch.object(sync_alarm.alarm_api, "set_alarm_wake_time",
                               side_effect=lambda tok, mac, a, t: applied.append(t)):
            result = sync_alarm.compute_and_apply({"default_wake_time": "07:30"}, TZ, [])
        return applied, result

    def test_nothing_set_is_exactly_the_old_behaviour(self):
        applied, r = self.run_sync(None, None)
        self.assertEqual(applied, [time(7, 30)])
        self.assertTrue(r["is_default"])
        self.assertFalse(r["is_manual"])
        self.assertIsNone(r["manual_wake_time_fmt"])

    def test_the_time_owner_set_is_kept_not_reset_to_the_default(self):
        applied, r = self.run_sync(time(9, 30), None)
        self.assertEqual(applied, [time(9, 30)])
        self.assertTrue(r["is_manual"])
        self.assertEqual(r["wake_time_fmt"], "9:30 AM")

    def test_an_earlier_calendar_commitment_still_wins(self):
        applied, r = self.run_sync(time(9, 30), time(6, 15))
        self.assertEqual(applied, [time(6, 15)])
        self.assertFalse(r["is_manual"])
        self.assertFalse(r["is_default"])
        self.assertEqual(r["manual_wake_time_fmt"], "9:30 AM")
        self.assertEqual(r["event_title"], "Golf <> the user @ Club")

    def test_a_time_set_in_the_hatch_app_is_kept_too(self):
        # nothing recorded by alarm.py; the device says 9:30 and this morning it was 7:30
        applied, r = self.run_sync(None, None, device=time(9, 30), baseline=time(7, 30))
        self.assertEqual(applied, [time(9, 30)])
        self.assertTrue(r["is_manual"])
        self.assertEqual(self.remembered, [(time(9, 30), "9pm-sync")])

    def test_device_unchanged_since_this_morning_goes_back_to_the_default(self):
        applied, r = self.run_sync(None, None, device=time(9, 30), baseline=time(9, 30))
        self.assertEqual(applied, [time(7, 30)])
        self.assertFalse(r["is_manual"])
        self.assertEqual(self.remembered, [(time(7, 30), "9pm-sync")])

    def test_first_night_with_no_baseline_is_the_old_behaviour(self):
        applied, _ = self.run_sync(None, None, device=time(9, 30), baseline=None)
        self.assertEqual(applied, [time(7, 30)])

    def test_a_later_calendar_need_does_not_move_a_manual_time(self):
        applied, r = self.run_sync(time(8, 0), time(8, 45))   # calendar only needs 8:45; he set 8:00
        self.assertEqual(applied, [time(8, 0)])
        self.assertTrue(r["is_manual"])


class NinePmMessage(unittest.TestCase):
    BASE = {"sync_failed": False, "away": False, "flight_tomorrow": False, "error": None,
            "event_title": None, "event_start_fmt": None, "leave_time_fmt": None}

    def first_line(self, **alarm_result):
        text = nightly.format_message({**self.BASE, **alarm_result}, {"status": "skipped"})
        return text.splitlines()[0].removesuffix(" 🎩")  # a one-line message carries the sign-off

    def test_wording(self):
        self.assertEqual(self.first_line(wake_time_fmt="7:30 AM", is_default=True, is_manual=False),
                         "⏰ Alarm: 7:30 AM (default).")
        self.assertEqual(self.first_line(wake_time_fmt="9:30 AM", is_default=True, is_manual=True,
                                         manual_wake_time_fmt="9:30 AM"),
                         "⏰ Alarm: 9:30 AM (the time you set).")
        line = self.first_line(wake_time_fmt="6:15 AM", is_default=False, is_manual=False, manual_wake_time_fmt="9:30 AM",
                               event_title="Golf <> the user @ Club", event_start_fmt="7:45 AM", leave_time_fmt="7:00 AM")
        self.assertIn("'Golf <> the user @ Club' (actually 7:45 AM) means leaving by 7:00 AM.", line)
        self.assertTrue(line.endswith("That is earlier than the 9:30 AM you set."))
        self.assertNotIn("None", line)

    def test_old_callers_without_the_new_keys_still_work(self):
        self.assertEqual(self.first_line(wake_time_fmt="7:30 AM", is_default=True), "⏰ Alarm: 7:30 AM (default).")


class AlarmCommand(unittest.TestCase):
    def run_cli(self, *argv, followers_ok=True):
        recorded, ran = [], []

        def fake_run(cmd, **_):
            # A follower can now carry its own arguments (the breakfast one gets
            # --from-alarm-change), so the script is the .py in the command, not its last word.
            script = next(part for part in cmd if str(part).endswith(".py"))
            ran.append(" ".join([Path(script).name, *map(str, cmd[cmd.index(script) + 1:])]))
            out = json.dumps({"status": "ok", "ramp_start_local": "09:00", "morning_off_local": "10:00"}) \
                if "sunrise" in script else ""
            if not followers_ok:
                out = json.dumps({"status": "error", "error": "alarm 1 not found"})
            return mock.Mock(returncode=0, stdout=out, stderr="")

        printed = []
        with mock.patch.object(sys, "argv", ["alarm.py", *argv]), \
             mock.patch.object(alarm, "load_alarm_config", return_value={"device": "restore", "alarm_id": "1"}), \
             mock.patch.object(alarm, "resolve_mac", return_value="AA"), \
             mock.patch.object(alarm.alarm_api, "login", return_value="t"), \
             mock.patch.object(alarm.alarm_api, "fetch_alarms", return_value=[{"id": 1}]), \
             mock.patch.object(alarm.alarm_api, "set_alarm_wake_time", return_value=[{"id": 1}]), \
             mock.patch.object(alarm.alarm_api, "set_alarm_enabled", return_value=[{"id": 1}]), \
             mock.patch.object(alarm, "format_alarm", return_value="[1]"), \
             mock.patch.object(alarm.wake_override, "remember_baseline"), \
             mock.patch.object(alarm.wake_override, "record",
                               side_effect=lambda w, now: recorded.append(w) or {"for_date": "2026-09-21"}), \
             mock.patch.object(alarm.subprocess, "run", side_effect=fake_run), \
             mock.patch("builtins.print", side_effect=lambda *a, **k: printed.append(" ".join(map(str, a)))):
            alarm.main()
        return recorded, ran, printed

    def test_a_new_wake_time_is_recorded_and_the_lights_follow_at_once(self):
        recorded, ran, printed = self.run_cli("--time", "9:30am")
        self.assertEqual(recorded, [time(9, 30)])
        # 2026-09-27: the morning briefing now hangs off the alarm too (wake + 15).
        # 2026-10-06: so does the ChiliPad — warm-up before wake, off after.
        # 2026-10-06: the breakfast refresh is pre-dawn-guarded, so a follower run has to say
        # it is one or an evening alarm change cannot move the first meal reminder at all.
        self.assertEqual(ran, ["sync_bedroom_sunrise.py", "night_schedule.py",
                               "refresh_breakfast_anchor.py --from-alarm-change",
                               "refresh_morning_briefing.py"])
        self.assertIn("✓ bedroom sunrise lights: start 09:00, off 10:00", printed)
        self.assertTrue(any("9pm sync will not reset it" in p for p in printed))

    def test_switching_the_alarm_on_retimes_everything_too(self):
        # 2026-10-08: he asked for his alarm back on, it came on at 7:30, and nothing else
        # moved — the lights, the bed and the briefing had all planned around "no alarm",
        # because only a --time change ran the followers.
        _, ran, printed = self.run_cli("--enable")
        self.assertEqual([r.split()[0] for r in ran],
                         ["sync_bedroom_sunrise.py", "night_schedule.py",
                          "refresh_breakfast_anchor.py", "refresh_morning_briefing.py"])
        self.assertFalse(any("9pm sync will not reset it" in p for p in printed),
                         "an enable records no override: there is no new time he chose")

    def test_switching_it_off_retimes_everything_as_well(self):
        _, ran, _ = self.run_cli("--disable")
        self.assertIn("night_schedule.py", [r.split()[0] for r in ran],
                      "the bed must hear that there is no wake time to follow")

    def test_no_follow_still_means_no_follow(self):
        _, ran, _ = self.run_cli("--enable", "--no-follow")
        self.assertEqual(ran, [])

    def test_a_follower_that_fails_is_said_out_loud(self):
        _, _, printed = self.run_cli("--time", "9:30am", followers_ok=False)
        self.assertIn("⚠️ bedroom sunrise lights NOT retimed — alarm 1 not found", printed)

    def test_no_follow_and_plain_listing_touch_nothing(self):
        self.assertEqual(self.run_cli("--time", "9:30am", "--no-follow")[:2], ([], []))
        self.assertEqual(self.run_cli()[:2], ([], []))


if __name__ == "__main__":
    unittest.main()
