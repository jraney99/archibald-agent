#!/usr/bin/env python3
"""On-demand light modes (evening_hold.py): The user asks for one by name — night_light
(2026-09-30), movie (2026-10-01) — it holds, the scheduled evening steps stand down, and it
switches itself off at 22:45 (or 01:00 if he asked for it after that).

The two things worth protecting:
  1. A LIVE hold suppresses the 8pm winddown (control.py --skip-if-held), the console
     curve + post-Zoom restore (console_schedule.py) and the 10pm fade (winddown_fade.py).
  2. An EXPIRED hold suppresses NOTHING — a missed off-run must never eat tomorrow.

No network: every path here stops before GoveeClient, and the state file is redirected to
a temp file so the live hold is never touched.

    python3 test_evening_hold.py
"""
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

_SCRIPTS_DIR = Path(__file__).parent
CT = ZoneInfo("America/Chicago")


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


evening_hold = _load("evening_hold", _SCRIPTS_DIR / "evening_hold.py")
control = _load("control", _SCRIPTS_DIR / "control.py")
console_schedule = _load("console_schedule", _SCRIPTS_DIR / "console_schedule.py")
winddown_fade = _load("winddown_fade", _SCRIPTS_DIR / "winddown_fade.py")


def _run(mod, argv):
    out = io.StringIO()
    code = 0
    with patch.object(sys, "argv", argv), redirect_stdout(out):
        try:
            mod.main()
        except SystemExit as e:
            code = e.code or 0
        except Exception:
            code = "raised"  # got past the gate, then hit something we didn't mock
    return code, out.getvalue()


class TempState(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = patch.object(evening_hold, "STATE", Path(self.tmp.name) / "evening_hold.json")
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_hold(self, off_at):
        evening_hold.STATE.write_text(json.dumps({"on_at": "x", "off_at": off_at.isoformat()}))


class OffTime(unittest.TestCase):
    def at(self, h, m):
        return evening_hold.off_time(datetime(2026, 9, 30, h, m, tzinfo=CT))

    def test_evening_holds_until_the_normal_fade_ends(self):
        for h, m in ((13, 0), (18, 0), (20, 0), (22, 30), (22, 44)):
            self.assertEqual(self.at(h, m), datetime(2026, 9, 30, 22, 45, tzinfo=CT), f"{h}:{m}")

    def test_after_the_evening_off_it_runs_to_one_am(self):
        self.assertEqual(self.at(22, 50), datetime(2026, 10, 1, 1, 0, tzinfo=CT))
        self.assertEqual(self.at(23, 30), datetime(2026, 10, 1, 1, 0, tzinfo=CT))

    def test_small_hours_before_one_am_end_at_one_am(self):
        self.assertEqual(self.at(0, 30), datetime(2026, 9, 30, 1, 0, tzinfo=CT))

    def test_small_hours_and_morning_get_a_short_hold_not_the_whole_day(self):
        self.assertEqual(self.at(2, 0), datetime(2026, 9, 30, 2, 45, tzinfo=CT))
        self.assertEqual(self.at(9, 0), datetime(2026, 9, 30, 9, 45, tzinfo=CT))


class Held(TempState):
    def test_no_file_means_no_hold(self):
        self.assertFalse(evening_hold.held())

    def test_unreadable_or_incomplete_file_means_no_hold(self):
        for junk in ("", "{", '{"on_at": "x"}', '{"off_at": "not a time"}', '{"off_at": null}'):
            evening_hold.STATE.write_text(junk)
            self.assertFalse(evening_hold.held(), junk)

    def test_a_live_hold_is_held(self):
        now = datetime(2026, 9, 30, 20, 0, tzinfo=CT)
        self.write_hold(now + timedelta(hours=2))
        self.assertTrue(evening_hold.held(now=now))

    def test_an_expired_hold_suppresses_nothing(self):
        now = datetime(2026, 9, 30, 20, 0, tzinfo=CT)
        self.write_hold(now - timedelta(minutes=1))
        self.assertFalse(evening_hold.held(now=now))


class OnOffCheck(TempState):
    def test_movie_mode_comes_from_the_config(self):
        seen = {}
        with patch.object(evening_hold, "_control", lambda *a: seen.setdefault("args", a) or 0):
            out = evening_hold.turn_on("movie", datetime(2026, 10, 1, 19, 0, tzinfo=CT))
        mode = evening_hold.resolve("movie")
        self.assertEqual(seen["args"], (mode["lights"], *evening_hold.control_args(mode)))
        self.assertEqual(out["off_at"], "10:45 PM", "movie ends with the evening, like night light")
        self.assertEqual(json.loads(evening_hold.STATE.read_text())["mode"], "movie")

    def test_movie_is_goveees_own_scene_since_a_flat_amber_looked_like_the_winddown(self):
        self.assertEqual(evening_hold.resolve("movie").get("scene"), "Movie")

    def test_any_scene_can_be_held_without_being_a_configured_mode(self):
        seen = {}
        with patch.object(evening_hold, "_control", lambda *a: seen.setdefault("args", a) or 0):
            out = evening_hold.turn_on(scene="War Films", now=datetime(2026, 10, 1, 19, 0, tzinfo=CT))
        self.assertEqual(seen["args"], ("all", "--on", "--scene", "War Films"))
        self.assertEqual(out["mode"], "scene: War Films")
        self.assertEqual(out["off_at"], "10:45 PM", "a held scene ends with the evening too")
        self.assertEqual(json.loads(evening_hold.STATE.read_text())["mode"], "scene: War Films")

    def test_a_held_scene_can_be_one_light_only(self):
        seen = {}
        with patch.object(evening_hold, "_control", lambda *a: seen.setdefault("args", a) or 0):
            evening_hold.turn_on(scene="Reading", lights="bedroom", now=datetime(2026, 10, 1, 19, 0, tzinfo=CT))
        self.assertEqual(seen["args"][0], "bedroom")
        self.assertTrue(evening_hold.held("bedroom", datetime(2026, 10, 1, 19, 30, tzinfo=CT)))
        self.assertFalse(evening_hold.held("console_light", datetime(2026, 10, 1, 19, 30, tzinfo=CT)))

    def test_each_kind_of_setting_becomes_the_matching_control_flags(self):
        args = evening_hold.control_args
        self.assertEqual(args({"brightness": 12, "color": "255,140,60"}),
                         ["--on", "--brightness", "12", "--color", "255,140,60"])
        self.assertEqual(args({"scene": "Movie"}), ["--on", "--scene", "Movie"])
        self.assertEqual(args({"brightness": 40, "temp": 2700}), ["--on", "--brightness", "40", "--temp", "2700"])

    def test_the_living_room_gets_colour_holds_but_not_scenes(self):
        # the living-room Zengge light follows the holds since 2026-10-12: same flags, --temp
        # becomes its single white, and a scene (it has none) leaves it alone
        self.assertEqual(evening_hold._zengge_args(("--on", "--brightness", "6", "--color", "255,170,90")),
                         ["--on", "--brightness", "6", "--color", "255,170,90"])
        self.assertEqual(evening_hold._zengge_args(("--on", "--temp", "2700")), ["--on", "--white"])
        self.assertIsNone(evening_hold._zengge_args(("--on", "--scene", "Movie")))

    def test_a_hold_on_all_drives_both_brands_and_off_reaches_the_living_room(self):
        runs = []
        with patch.object(evening_hold.subprocess, "run",
                          lambda argv, **kw: runs.append(argv) or type("R", (), {"returncode": 0})()):
            evening_hold._control("all", "--on", "--brightness", "6", "--color", "255,170,90")
            evening_hold._control("all", "--on", "--scene", "Movie")
            evening_hold._control("all", "--off")
            evening_hold._control("living_room", "--on", "--white")
        targets = [(argv[1].rsplit("/", 3)[-3], argv[3]) for argv in runs]
        self.assertEqual(targets, [("govee-lights", "all"), ("zengge-lights", "all"),
                                   ("govee-lights", "all"),                       # scene: Govee only
                                   ("govee-lights", "all"), ("zengge-lights", "all"),
                                   ("zengge-lights", "living_room")])

    def test_an_unknown_mode_is_refused_before_any_light_moves(self):
        with patch.object(evening_hold, "_control", lambda *a: self.fail("drove the lights")):
            with self.assertRaises(KeyError):
                evening_hold.turn_on("disco")
        self.assertFalse(evening_hold.STATE.exists())

    def test_off_powers_down_only_the_lights_the_mode_covered(self):
        evening_hold.STATE.write_text(json.dumps({"mode": "movie", "lights": "bedroom",
                                                  "off_at": "2026-10-01T22:45:00-05:00"}))
        calls = []
        with patch.object(evening_hold, "_control", lambda *a: calls.append(a) or 0):
            evening_hold.turn_off()
        self.assertEqual(calls, [("bedroom", "--off")])

    def test_a_one_light_mode_leaves_the_other_lights_evening_alone(self):
        now = datetime(2026, 10, 1, 19, 0, tzinfo=CT)
        evening_hold.STATE.write_text(json.dumps({"mode": "movie", "lights": "bedroom",
                                                  "off_at": (now + timedelta(hours=1)).isoformat()}))
        self.assertTrue(evening_hold.held("bedroom", now))
        self.assertFalse(evening_hold.held("console_light", now))

    def test_on_uses_the_fades_own_last_step_and_writes_the_hold_first(self):
        seen = {}

        def fake_control(*args):
            seen["args"] = args
            seen["state_existed_already"] = evening_hold.STATE.exists()
            return 0

        with patch.object(evening_hold, "_control", fake_control):
            out = evening_hold.turn_on("night_light", datetime(2026, 9, 30, 20, 0, tzinfo=CT))
        step = evening_hold.load_modes()["night_light"]["fade_step_from_end"]
        self.assertEqual(evening_hold.resolve("night_light")["brightness"], winddown_fade.BRIGHTNESS_CURVE[-step])
        self.assertEqual(evening_hold.resolve("night_light")["brightness"], 6,
                         "The user chose the step above the fade's last on 2026-10-01")
        self.assertEqual(evening_hold.resolve("night_light")["color"], ",".join(str(c) for c in winddown_fade.COLOR))
        self.assertEqual(seen["args"], ("all", "--on", "--brightness", "6", "--color", "255,170,90"))
        self.assertTrue(seen["state_existed_already"], "the hold must be on disk before the lights move")
        self.assertEqual(out["off_at"], "10:45 PM")
        self.assertEqual(json.loads(evening_hold.STATE.read_text())["off_at"],
                         datetime(2026, 9, 30, 22, 45, tzinfo=CT).isoformat())

    def test_off_clears_the_hold_and_powers_down(self):
        self.write_hold(datetime(2026, 9, 30, 22, 45, tzinfo=CT))
        with patch.object(evening_hold, "_control", lambda *a: 0) as _:
            out = evening_hold.turn_off()
        self.assertEqual(out["on"], False)
        self.assertFalse(evening_hold.STATE.exists())

    def test_check_does_nothing_without_a_hold(self):
        with patch.object(evening_hold, "_control", lambda *a: self.fail("no lights should be driven")):
            self.assertEqual(evening_hold.check(), {"skipped": "no night light hold"})

    def test_check_waits_while_the_hold_is_live(self):
        self.write_hold(datetime(2026, 9, 30, 22, 45, tzinfo=CT))
        with patch.object(evening_hold, "_control", lambda *a: self.fail("too early to switch off")):
            out = evening_hold.check(datetime(2026, 9, 30, 21, 0, tzinfo=CT))
        self.assertIn("holding_until", out)
        self.assertTrue(evening_hold.STATE.exists())

    def test_check_switches_off_and_clears_once_due(self):
        self.write_hold(datetime(2026, 9, 30, 22, 45, tzinfo=CT))
        calls = []
        with patch.object(evening_hold, "_control", lambda *a: calls.append(a) or 0):
            out = evening_hold.check(datetime(2026, 9, 30, 22, 45, tzinfo=CT))
        self.assertEqual(calls, [("all", "--off")])
        self.assertEqual(out["on"], False)
        self.assertFalse(evening_hold.STATE.exists())


class ScheduledJobsStandDown(unittest.TestCase):
    """The live hold's whole point: nothing scheduled brightens the room back up."""

    def test_winddown_job_skips_while_held(self):
        with patch.object(control, "held", return_value=True):
            code, out = _run(control, ["control.py", "--light", "all", "--on", "--brightness", "50",
                                       "--color", "255,170,90", "--skip-if-held"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"skipped": "night light hold is on"})

    def test_a_manual_call_without_the_flag_is_never_blocked(self):
        with patch.object(control, "held", return_value=True), \
             patch.object(control, "load_devices", side_effect=AssertionError("reached the lights")):
            code, out = _run(control, ["control.py", "--light", "all", "--off"])
        self.assertEqual(code, "raised", f"manual calls must get past the hold gate: {out!r}")

    def test_console_curve_and_zoom_restore_skip_while_held(self):
        with patch.object(console_schedule, "held", return_value=True):
            code, out = _run(console_schedule, ["console_schedule.py"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["skipped"], "night light hold is on")

    def test_console_curve_still_runs_when_not_held(self):
        with patch.object(console_schedule, "held", return_value=False), \
             patch.object(console_schedule.subprocess, "run", side_effect=AssertionError("ran control.py")):
            code, _ = _run(console_schedule, ["console_schedule.py"])
        self.assertEqual(code, "raised")

    def test_ten_pm_fade_skips_while_held(self):
        with patch.object(evening_hold, "held", return_value=True):
            code, out = _run(winddown_fade, ["winddown_fade.py", "--light", "all", "--duration-seconds", "1"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"skipped": "night light hold is on"})

    def test_ten_pm_fade_runs_normally_when_not_held(self):
        with patch.object(evening_hold, "held", return_value=False), \
             patch.object(winddown_fade, "load_targets", side_effect=AssertionError("reached the fade")):
            code, _ = _run(winddown_fade, ["winddown_fade.py", "--light", "all", "--duration-seconds", "1"])
        self.assertEqual(code, "raised")


if __name__ == "__main__":
    unittest.main(verbosity=2)
