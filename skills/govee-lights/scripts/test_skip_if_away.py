#!/usr/bin/env python3
"""
Travel gate for the scheduled Govee jobs (added 2026-08-28): control.py and
sunrise_ramp.py, when invoked with --skip-if-away, must no-op (exit 0,
print {"skipped": ...}) while shared/current_location.is_away_from_home() is
true — so the evening winddown / morning energize / sunrise ramp / focus
scene don't drive lights in an empty house. Manual/chat calls omit the flag
and are unaffected. The "off" jobs (focus-end, winddown-fade-off) never get
the flag, so anything left on still goes dark on schedule.

No network — GoveeClient is never reached on the skip path; on the
non-skip path we stop it at load_devices/load_device.

    python3 test_skip_if_away.py
"""
import importlib.util
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

_SCRIPTS_DIR = Path(__file__).parent


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


control = _load("control", _SCRIPTS_DIR / "control.py")
sunrise_ramp = _load("sunrise_ramp", _SCRIPTS_DIR / "sunrise_ramp.py")


def _run(mod, argv):
    out = io.StringIO()
    code = 0
    with patch.object(sys, "argv", argv), redirect_stdout(out):
        try:
            mod.main()
        except SystemExit as e:
            code = e.code or 0
        except Exception:
            code = "raised"  # got past the --skip-if-away gate, then hit a mocked stop
    return code, out.getvalue()


class TestControlSkipIfAway(unittest.TestCase):
    def test_skips_when_flag_set_and_away(self):
        with patch.object(control, "is_away_from_home", return_value=True), \
             patch.object(control, "GoveeClient", side_effect=AssertionError("must not construct client")):
            code, out = _run(control, ["control.py", "--light", "all", "--on", "--scene", "Study", "--skip-if-away"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"skipped": "away from home"})

    def test_runs_when_away_but_flag_absent(self):
        # No --skip-if-away → the guard is bypassed entirely (manual/chat call).
        with patch.object(control, "is_away_from_home", return_value=True), \
             patch.object(control, "load_devices", return_value={}):
            code, out = _run(control, ["control.py", "--light", "all", "--off"])
        self.assertNotIn("skipped", out)  # got past the gate, hit the empty-device guard

    def test_runs_when_flag_set_but_home(self):
        with patch.object(control, "is_away_from_home", return_value=False), \
             patch.object(control, "load_devices", return_value={}):
            code, out = _run(control, ["control.py", "--light", "all", "--off", "--skip-if-away"])
        self.assertNotIn("skipped", out)


class TestSunriseRampSkipIfAway(unittest.TestCase):
    def test_skips_when_flag_set_and_away(self):
        with patch.object(sunrise_ramp, "is_away_from_home", return_value=True), \
             patch.object(sunrise_ramp, "GoveeClient", side_effect=AssertionError("must not construct client")):
            code, out = _run(sunrise_ramp, ["sunrise_ramp.py", "--light", "bedroom", "--duration-seconds", "300", "--skip-if-away"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"skipped": "away from home"})

    def test_runs_when_flag_set_but_home(self):
        with patch.object(sunrise_ramp, "is_away_from_home", return_value=False), \
             patch.object(sunrise_ramp, "load_device", side_effect=RuntimeError("stop before network")):
            code, out = _run(sunrise_ramp, ["sunrise_ramp.py", "--light", "bedroom", "--duration-seconds", "300", "--skip-if-away"])
        self.assertNotIn("skipped", out)


class TestMisfireGuard(unittest.TestCase):
    """--skip-if-misfire no-ops a gateway catch-up run that fires the
    fixed-clock cron job at a wall-clock time it was never scheduled for
    (root cause: a westward --tz shift on travel making a completed run
    look missed). Added 2026-08-30 after the 7:15am bedroom sunrise ramp
    misfired at 4:37pm and left the light stuck on dim amber."""

    def _at(self, mod, hour):
        from datetime import datetime
        import zoneinfo
        fake = datetime(2026, 8, 30, hour, 15, tzinfo=zoneinfo.ZoneInfo("America/Chicago"))
        real = mod.within_slot

        def stub(weekday, lo, hi):
            return real(weekday, lo, hi, now=fake)
        return patch.object(mod, "within_slot", side_effect=stub)

    def test_sunrise_skips_outside_window(self):
        with self._at(sunrise_ramp, 16), \
             patch.object(sunrise_ramp, "GoveeClient", side_effect=AssertionError("must not construct client")):
            code, out = _run(sunrise_ramp, ["sunrise_ramp.py", "--light", "bedroom",
                                            "--duration-seconds", "900", "--skip-if-misfire", "--skip-if-away"])
        self.assertEqual(code, 0)
        self.assertIn("skipped misfire", json.loads(out)["skipped"])

    def test_sunrise_runs_inside_window(self):
        with self._at(sunrise_ramp, 7), \
             patch.object(sunrise_ramp, "is_away_from_home", return_value=False), \
             patch.object(sunrise_ramp, "load_device", side_effect=RuntimeError("stop before network")):
            code, out = _run(sunrise_ramp, ["sunrise_ramp.py", "--light", "bedroom",
                                            "--duration-seconds", "900", "--skip-if-misfire", "--skip-if-away"])
        self.assertNotIn("skipped", out)



def _clock(mod, hh, mm):
    """Run `mod`'s near_slot as if the wall clock read hh:mm, 2026-09-21 Chicago."""
    from datetime import datetime
    import zoneinfo
    fake = datetime(2026, 9, 21, hh, mm, tzinfo=zoneinfo.ZoneInfo("America/Chicago"))
    real = mod.near_slot
    return patch.object(mod, "near_slot", side_effect=lambda *a, **k: real(*a, **{**k, "now": fake}))


class TestControlExpectedStart(unittest.TestCase):
    """control.py --expected-start: every scheduled light job knows its own time and
    skips a gateway catch-up replay at some other hour (2026-09-21: the evening jobs
    had no guard, so a restart could turn the 8pm gold on in the morning)."""

    WINDDOWN = ["control.py", "--light", "bedroom", "--on", "--brightness", "50",
                "--color", "255,170,90", "--skip-if-away",
                "--expected-start", "20:00", "--late-grace-min", "120"]

    def run_at(self, hh, mm, argv):
        with _clock(control, hh, mm), \
             patch.object(control, "is_away_from_home", return_value=False), \
             patch.object(control, "load_devices", return_value={}), \
             patch.object(control, "GoveeClient", side_effect=AssertionError("must not construct client")):
            return _run(control, argv)

    def test_on_time_runs(self):
        code, out = self.run_at(20, 0, self.WINDDOWN)
        self.assertNotIn("skipped", out)  # got past the guard, stopped at the empty-device check

    def test_late_but_before_the_fade_still_runs(self):
        code, out = self.run_at(21, 45, self.WINDDOWN)
        self.assertNotIn("skipped", out)

    def test_morning_replay_is_skipped(self):
        code, out = self.run_at(7, 10, self.WINDDOWN)
        self.assertEqual(code, 0)
        self.assertIn("skipped misfire", json.loads(out)["skipped"])

    def test_after_the_fade_started_is_skipped(self):
        code, out = self.run_at(22, 30, self.WINDDOWN)
        self.assertIn("skipped misfire", json.loads(out)["skipped"])

    def test_early_is_skipped(self):
        code, out = self.run_at(19, 0, self.WINDDOWN)
        self.assertIn("skipped misfire", json.loads(out)["skipped"])

    def test_default_late_grace_is_an_hour(self):
        argv = ["control.py", "--light", "console_light", "--on", "--expected-start", "11:00"]
        self.assertNotIn("skipped", self.run_at(11, 59, argv)[1])
        self.assertIn("skipped", self.run_at(12, 1, argv)[1])

    def test_manual_call_without_the_flag_is_never_skipped(self):
        code, out = self.run_at(3, 0, ["control.py", "--light", "bedroom", "--on"])
        self.assertNotIn("skipped", out)


winddown_fade = _load("winddown_fade", _SCRIPTS_DIR / "winddown_fade.py")


class TestFadeOffExpectedStart(unittest.TestCase):
    """winddown_fade.py --expected-start: on time → fade; late the same night → plain
    power-off (the fade opens at 50% gold, which would relight a lamp the user switched off);
    any other hour → nothing."""

    def mode_at(self, hh, mm):
        with _clock(winddown_fade, hh, mm):
            return winddown_fade.misfire_mode("22:00")[0]

    def test_modes(self):
        self.assertEqual(self.mode_at(22, 0), "fade")
        self.assertEqual(self.mode_at(22, 25), "fade")
        self.assertEqual(self.mode_at(23, 30), "off")
        self.assertEqual(self.mode_at(2, 30), "off")
        self.assertEqual(self.mode_at(3, 30), "skip")   # sunrise territory
        self.assertEqual(self.mode_at(7, 22), "skip")
        self.assertEqual(self.mode_at(14, 0), "skip")   # next-afternoon replay
        self.assertEqual(self.mode_at(21, 0), "skip")   # early

    def run_at(self, hh, mm, client):
        # held() reads the real state file, so a night light the user has on RIGHT NOW would
        # otherwise make these fade tests pass or fail depending on the hour they're run.
        # The hold's own behaviour is covered in test_evening_hold.py.
        evening_hold = _load("evening_hold", _SCRIPTS_DIR / "evening_hold.py")
        with _clock(winddown_fade, hh, mm), \
             patch.object(evening_hold, "held", return_value=False), \
             patch.object(winddown_fade, "load_targets", return_value=[("bedroom", {"sku": "S", "device": "D"})]), \
             patch.object(winddown_fade, "GoveeClient", return_value=client), \
             patch.object(winddown_fade.time, "sleep"):
            return _run(winddown_fade, ["winddown_fade.py", "--light", "bedroom",
                                        "--duration-seconds", "2700", "--expected-start", "22:00"])

    def test_late_run_only_powers_off(self):
        from unittest.mock import MagicMock
        client = MagicMock()
        code, out = self.run_at(1, 0, client)
        self.assertEqual(code, 0)
        client.power.assert_called_once_with("S", "D", False)
        client.color_rgb.assert_not_called()
        client.brightness.assert_not_called()

    def test_afternoon_replay_touches_nothing(self):
        from unittest.mock import MagicMock
        client = MagicMock()
        code, out = self.run_at(14, 0, client)
        self.assertIn("skipped misfire", json.loads(out)["skipped"])
        self.assertEqual(client.method_calls, [])

    def test_on_time_fades(self):
        from unittest.mock import MagicMock
        client = MagicMock()
        self.run_at(22, 0, client)
        client.color_rgb.assert_called_once_with("S", "D", 255, 170, 90)
        self.assertEqual(client.brightness.call_count, len(winddown_fade.BRIGHTNESS_CURVE))
        client.power.assert_called_once_with("S", "D", False)


if __name__ == "__main__":
    unittest.main()
