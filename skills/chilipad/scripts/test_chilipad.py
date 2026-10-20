#!/usr/bin/env python3
"""The ChiliPad skill: sentinel temperatures, the two guards, and the nightly retime.

No network (the HTTP session is a fake), no live state (the plan file is a temp), no
cron (subprocess.run is captured).

What each group is here to stop coming back:
  Sentinels      999 is not a temperature. The Home Assistant integration shipped
                 it to users as 1830°F (its issue #50); nothing here may print or
                 do arithmetic on a raw set_temperature_*.
  Guards in argv `cron edit --command-argv` replaces the whole argv, so a guard
                 left out of warm_argv/off_argv is a guard silently dropped — that
                 is how the govee misfire guard vanished for weeks before 2026-09-09.
  Retime math    The morning jobs follow the Hatch. Getting the offsets backwards
                 warms the bed after he is up, or cools it as he wakes.
  Fail open      The plan file may only ever cancel a run, never cause one.
  Disabled jobs  `cron edit` returns 0 on a disabled job, so "edited" never means
                 "will run" (The user lost two mornings of sunrise to exactly this).

    python3 test_chilipad.py
"""

import json
import sys
import tempfile
import unittest
import unittest.mock
from datetime import date, datetime, time, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(SCRIPTS_DIR.parent.parent / "shared"))

import chilipad_jobs  # noqa: E402
import control  # noqa: E402
import detach  # noqa: E402
import manual_hold as mh  # noqa: E402
import night_plan as np  # noqa: E402
import maintenance as mt  # noqa: E402
import ramp  # noqa: E402
import night_schedule as ns  # noqa: E402
import sleepme_client as sc  # noqa: E402
import rate_prompt as rp
import trial  # noqa: E402

CT = ZoneInfo("America/Chicago")


def _capture():
    import contextlib
    import io
    out = io.StringIO()
    @contextlib.contextmanager
    def ctx():
        with contextlib.redirect_stdout(out):
            yield out
    return ctx()


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text="", headers=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text or (json.dumps(payload) if payload is not None else "")
        self.headers = headers or {}
        self.content = self.text.encode()

    @property
    def ok(self):
        return 200 <= self.status_code < 300

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    """Answers each request from a queued list and records what was sent."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []
        self.headers = {}

    def request(self, method, url, json=None, timeout=None):
        self.calls.append((method, url, json))
        return self._responses.pop(0) if self._responses else FakeResponse(200, {})


def client_with(responses):
    client = sc.SleepMeClient(token="test-token")
    client._session = FakeSession(responses)
    return client


class Sentinels(unittest.TestCase):
    def test_max_heat_and_max_cool_are_words_not_degrees(self):
        self.assertEqual(sc.describe_temp(999), "max heat")
        self.assertEqual(sc.describe_temp(-1), "max cool")
        self.assertEqual(sc.describe_temp(999.0, "C"), "max heat")
        self.assertEqual(sc.describe_temp(-1.0, "C"), "max cool")

    def test_real_temperatures_read_normally(self):
        self.assertEqual(sc.describe_temp(60), "60°F")
        self.assertEqual(sc.describe_temp(60.0), "60°F")
        self.assertEqual(sc.describe_temp(21.5, "C"), "21.5°C")
        self.assertEqual(sc.describe_temp(None), "unknown")

    def test_clamping_keeps_setpoints_inside_the_hardware_range(self):
        self.assertEqual(sc.clamp_f(40), sc.TEMP_MIN_F)
        self.assertEqual(sc.clamp_f(200), sc.TEMP_MAX_F)
        self.assertEqual(sc.clamp_f(60.4), 60)
        self.assertEqual(sc.clamp_c(5), sc.TEMP_MIN_C)
        self.assertEqual(sc.clamp_c(99), sc.TEMP_MAX_C)
        self.assertEqual(sc.clamp_c(20.3), 20.5)   # half-degree steps

    def test_temp_argument_forms(self):
        self.assertEqual(sc.parse_temp_arg("60"), ("set_temperature_f", 60))
        self.assertEqual(sc.parse_temp_arg("60F"), ("set_temperature_f", 60))
        self.assertEqual(sc.parse_temp_arg("21C"), ("set_temperature_c", 21.0))
        self.assertEqual(sc.parse_temp_arg("max-cool"), ("set_temperature_f", sc.MAX_COOL))
        self.assertEqual(sc.parse_temp_arg("max-heat"), ("set_temperature_f", sc.MAX_HEAT))
        with self.assertRaises(sc.SleepMeError):
            sc.parse_temp_arg("chilly")


class SafetyBound(unittest.TestCase):
    """The hard band on what can reach a bed someone is asleep in.

    Added 2026-10-07 after reviewing what connecting physical devices to an agent
    that reads untrusted email actually exposes. The hardware ceiling (115°F) is
    what the pump can do, not what is safe. This band is in code, not config: a
    bound a config edit could widen is not a bound.
    """

    def client(self):
        return client_with([FakeResponse(200, {})] * 4)

    def test_the_temperatures_owner_actually_uses_are_allowed(self):
        with patch.object(sc, "load_config", return_value={}):   # no floor of his own, see HisOwnFloor
            for f in (sc.SAFE_MIN_F, 68, 72, 76, 92, sc.SAFE_MAX_F):
                client = self.client()
                client.set_temperature("abc", "set_temperature_f", f)
                self.assertEqual(len(client._session.calls), 1, f)

    def test_above_the_band_is_refused_and_nothing_is_sent(self):
        for f in (sc.SAFE_MAX_F + 1, 110, sc.TEMP_MAX_F):
            client = self.client()
            with self.assertRaises(sc.SleepMeError, msg=f):
                client.set_temperature("abc", "set_temperature_f", f)
            self.assertEqual(client._session.calls, [], f)

    def test_below_the_band_is_refused(self):
        client = self.client()
        with self.assertRaises(sc.SleepMeError):
            client.set_temperature("abc", "set_temperature_f", sc.SAFE_MIN_F - 1)

    def test_the_max_heat_preset_is_refused_outright(self):
        # "max heat" is exactly the instruction an injection would reach for, and as
        # a sentinel (999) it would sail past any numeric comparison.
        client = self.client()
        with self.assertRaises(sc.SleepMeError) as cm:
            client.set_temperature("abc", "set_temperature_f", sc.MAX_HEAT)
        self.assertIn("max heat", str(cm.exception))
        self.assertEqual(client._session.calls, [])

    def test_the_max_cool_preset_is_refused_outright(self):
        client = self.client()
        with self.assertRaises(sc.SleepMeError):
            client.set_temperature("abc", "set_temperature_f", sc.MAX_COOL)

    def test_celsius_is_bounded_too_not_just_fahrenheit(self):
        # 45C is 113F — inside the hardware range, far outside the safe one.
        client = self.client()
        with self.assertRaises(sc.SleepMeError):
            client.set_temperature("abc", "set_temperature_c", 45.0)

    def test_the_band_sits_inside_what_the_hardware_allows(self):
        self.assertGreaterEqual(sc.SAFE_MIN_F, sc.TEMP_MIN_F)
        self.assertLess(sc.SAFE_MAX_F, sc.TEMP_MAX_F)

    def test_the_configured_entry_temperature_is_still_reachable(self):
        # A bound that blocks the user's own warm-entry setting would be a regression,
        # not a safeguard.
        import json as _json
        from pathlib import Path as _Path
        cfg = _json.loads((_Path(__file__).resolve().parent.parent
                           / "config" / "chilipad_config.json").read_text())
        self.assertLessEqual(cfg["entry_temp_f"], sc.SAFE_MAX_F)
        for rung in cfg["trial"]["ladder_f"]:
            self.assertGreaterEqual(rung, sc.SAFE_MIN_F)
            self.assertLessEqual(rung, sc.SAFE_MAX_F)


class Transport(unittest.TestCase):
    def test_a_bad_token_says_so_instead_of_retrying(self):
        client = client_with([FakeResponse(401, text="unauthorized")])
        with self.assertRaises(sc.SleepMeError) as cm:
            client.get_device("abc")
        self.assertIn("401", str(cm.exception))
        self.assertEqual(len(client._session.calls), 1, "a 401 must not be retried")

    def test_a_429_is_retried_and_honours_retry_after(self):
        client = client_with([
            FakeResponse(429, text="slow down", headers={"Retry-After": "7"}),
            FakeResponse(200, {"control": {}}),
        ])
        with patch.object(sc.time, "sleep") as slept:
            self.assertEqual(client.get_device("abc"), {"control": {}})
        slept.assert_called_once_with(7.0)

    def test_the_window_ceiling_waits_rather_than_tripping_the_account_limit(self):
        client = client_with([FakeResponse(200, {})] * (sc.MAX_REQUESTS_PER_MINUTE + 1))
        with patch.object(sc.time, "sleep") as slept:
            for _ in range(sc.MAX_REQUESTS_PER_MINUTE + 1):
                client.get_device("abc")
        self.assertTrue(slept.called, "the request past the ceiling must wait for a slot")

    def test_patch_sends_one_field_at_a_time(self):
        client = client_with([FakeResponse(200, {}), FakeResponse(200, {})])
        with patch.object(sc, "load_config", return_value={}):   # not his live floor
            client.set_temperature("abc", "set_temperature_f", 60)
        client.set_thermal_status("abc", "active")
        bodies = [body for _, _, body in client._session.calls]
        self.assertEqual(bodies, [{"set_temperature_f": 60}, {"thermal_control_status": "active"}])
        self.assertTrue(all(m == "PATCH" for m, _, _ in client._session.calls))

    def test_nonsense_values_never_reach_the_device(self):
        client = client_with([])
        with self.assertRaises(sc.SleepMeError):
            client.set_thermal_status("abc", "on")
        with self.assertRaises(sc.SleepMeError):
            client.set_temperature("abc", "brightness_level", 50)
        self.assertEqual(client._session.calls, [])

    def test_a_missing_token_names_where_to_put_one(self):
        with patch.object(sc, "_get_token", lambda: ""):
            with self.assertRaises(sc.SleepMeError) as cm:
                sc.SleepMeClient()
        self.assertIn("SLEEPME_API_TOKEN", str(cm.exception))


class DeviceLookup(unittest.TestCase):
    def test_an_unknown_nickname_lists_the_real_ones(self):
        with patch.object(sc, "load_devices", lambda: {"bed": {"id": "d1"}}), \
             patch.object(sc, "load_config", lambda: {"device": "bed"}):
            self.assertEqual(sc.resolve_device("bed"), ("d1", "bed"))
            with self.assertRaises(sc.SleepMeError) as cm:
                sc.resolve_device("couch")
        self.assertIn("bed", str(cm.exception))

    def test_nothing_discovered_yet_points_at_discover_devices(self):
        with patch.object(sc, "load_devices", lambda: {}):
            with self.assertRaises(sc.SleepMeError) as cm:
                sc.resolve_device(None)
        self.assertIn("discover_devices", str(cm.exception))


class GuardsSurviveEveryRetime(unittest.TestCase):
    def test_every_phase_carries_the_two_guards_and_its_own_time(self):
        for phase in ("entry", "descend", "early_off", "off"):
            argv = ns.argv_for(phase, "23:15")
            self.assertIn("--skip-if-away", argv, phase)
            self.assertEqual(argv[argv.index("--expected-start") + 1], "23:15", phase)
            self.assertIn("--late-grace-min", argv, phase)

    def test_only_the_entry_step_starts_the_pump_and_checks_the_water(self):
        entry = ns.argv_for("entry", "22:15")
        self.assertIn("--on", entry)
        self.assertIn("--check-water", entry)
        for phase in ("descend", "early_off"):
            self.assertNotIn("--on", ns.argv_for(phase, "23:15"), phase)
            self.assertNotIn("--check-water", ns.argv_for(phase, "23:15"), phase)
        self.assertIn("--off", ns.argv_for("off", "08:00"))

    def test_the_descent_runs_the_ramp_for_the_planned_duration(self):
        argv = ns.argv_for("descend", "22:45", descend_minutes=30)
        self.assertIn("ramp.py", argv[1])
        self.assertEqual(argv[argv.index("--duration-seconds") + 1], "1800")

    def test_a_phase_asks_for_a_temperature_by_name_not_by_number(self):
        # The number is resolved at fire time, so stepping the trial ladder never
        # requires re-scheduling anything. A --temp in a scheduled argv would freeze
        # tonight's temperature into the cron store and silently outlive the ladder.
        argv = ns.argv_for("entry", "23:15")
        self.assertEqual(argv[argv.index("--phase") + 1], "entry")
        self.assertNotIn("--temp", argv)
        # The two off steps name no temperature at all — they stop the pump (2026-10-07: the
        # third phase stopped being a lift and became an off at 04:30).
        for phase in ("early_off", "off"):
            argv = ns.argv_for(phase, "04:30")
            self.assertIn("--off", argv)
            self.assertNotIn("--phase", argv)
            self.assertNotIn("--temp", argv)
        self.assertNotIn("--temp", ns.argv_for("descend", "22:45"))

    def test_expected_start_matches_the_cron_time_it_is_scheduled_at(self):
        # The misfire guard compares the wall clock against this string, so a
        # mismatch between the cron expression and --expected-start would make every
        # real run look like a catch-up and skip itself.
        plan = run_night_schedule(wake=datetime(2026, 10, 7, 7, 0, tzinfo=CT))
        self.assertTrue(plan["edits"])
        for expr, argv in plan["edits"]:
            minute, hour = expr.split()[0], expr.split()[1]
            self.assertEqual(argv[argv.index("--expected-start") + 1], f"{int(hour):02d}:{int(minute):02d}")


def run_night_schedule(wake, alarm_on=True, cfg=None, problems=(), dark=time(22, 45)):
    """night_schedule.main() with the Hatch, the lights, the cron CLI and the trial
    stubbed. Returns {"report", "edits": [(cron_expr, argv)]}."""
    cfg = cfg or dict(BASE_CFG)
    edits, printed = [], []

    class Done:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(argv, **kwargs):
        if argv[:3] == ["openclaw", "cron", "edit"]:
            edits.append((argv[argv.index("--cron") + 1],
                          json.loads(argv[argv.index("--command-argv") + 1])))
        return Done()

    with patch.object(ns, "read_plan", lambda: {"enabled": alarm_on, "wake_dt": wake}), \
         patch.object(ns, "load_config", lambda: cfg), \
         patch.object(ns, "lights_dark_time", lambda: (dark, "stub")), \
         patch.object(ns, "tonight_base", lambda c: (cfg["base_temp_f"], "stub")), \
         patch.object(ns, "advance", lambda c: {"trial": "stub"}), \
         patch.object(ns, "will_not_run", lambda ids: list(problems)), \
         patch.object(ns.subprocess, "run", fake_run), \
         patch("builtins.print", lambda *a, **k: printed.extend(a)):
        ns.main()
    return {"report": json.loads(printed[-1]), "edits": edits}


BASE_CFG = {
    "device": "bed",
    "entry_temp_f": 92,
    "base_temp_f": 72,
    "early_off_time": "04:30",
    "prewarm_minutes": 30,
    "descend_minutes": 45,
    "morning_off_delay_minutes": 30,
    "no_alarm_off_time": "10:00",
    "jobs": {"entry": "J-entry", "descend": "J-desc", "early_off": "J-early", "off": "J-off"},
}


class NightPlan(unittest.TestCase):
    """The placement maths, independent of cron and the Hatch."""

    def plan(self, wake, dark=time(22, 45), cfg=None, base=72):
        return np.build(wake, base, cfg or BASE_CFG, dark_t=dark)

    def test_the_night_is_built_from_lights_out_to_wake(self):
        p = self.plan(datetime(2026, 10, 7, 7, 30, tzinfo=CT))
        self.assertEqual(np.hhmm(p["lights_dark"]), "22:45")
        self.assertEqual(p["window_minutes"], 8 * 60 + 45)
        self.assertEqual(np.hhmm(p["moments"]["entry"]), "22:15")     # dark - 30 prewarm
        self.assertEqual(np.hhmm(p["moments"]["descend"]), "22:45")   # at lights-out
        self.assertEqual(np.hhmm(p["moments"]["early_off"]), "04:30")  # the clock time he picked
        self.assertEqual(np.hhmm(p["moments"]["off"]), "08:00")       # wake + 30

    def test_the_bed_is_warm_before_he_gets_in_and_cool_once_he_is_under(self):
        # The user 2026-10-06: "I love to get into a warm bed and then have it
        # gradually move down." Entry must be WARMER than the sleeping floor, and
        # the descent must not start before he is in it.
        p = self.plan(datetime(2026, 10, 7, 7, 30, tzinfo=CT))
        self.assertGreater(p["temps"]["entry"], p["temps"]["base"])
        self.assertLessEqual(p["moments"]["entry"], p["moments"]["descend"])

    def test_lights_out_is_placed_on_the_evening_before_the_wake(self):
        p = self.plan(datetime(2026, 10, 7, 7, 30, tzinfo=CT))
        self.assertEqual(p["lights_dark"].date().isoformat(), "2026-10-06")
        self.assertEqual(p["moments"]["entry"].date().isoformat(), "2026-10-06")

    def test_the_pump_stops_at_the_clock_time_he_picked_whatever_the_night(self):
        # 2026-10-07, after a midpoint lift and then a pre-wake lift were both wrong for him:
        # "if it shut off at 4:30 am that would be fine. It's mostly the falling asleep and the
        # REM I am concerned about." A stopped pump lets the bed settle at body heat.
        long = self.plan(datetime(2026, 10, 7, 10, 45, tzinfo=CT))   # 12h night
        self.assertEqual(np.hhmm(long["moments"]["early_off"]), "04:30")
        short = self.plan(datetime(2026, 10, 7, 4, 45, tzinfo=CT))   # wakes before 04:30
        self.assertIsNone(short["moments"]["early_off"], "no point stopping the pump after he is up")
        self.assertIn("early_off", short["dropped"])

    def test_an_early_alarm_moves_the_morning_but_not_the_evening(self):
        early = self.plan(datetime(2026, 10, 7, 5, 0, tzinfo=CT))
        self.assertEqual(np.hhmm(early["moments"]["entry"]), "22:15")
        self.assertEqual(np.hhmm(early["moments"]["off"]), "05:30")

    def test_a_changed_light_schedule_moves_the_whole_evening(self):
        p = self.plan(datetime(2026, 10, 7, 7, 30, tzinfo=CT), dark=time(23, 30))
        self.assertEqual(np.hhmm(p["moments"]["entry"]), "23:00")
        self.assertEqual(np.hhmm(p["moments"]["descend"]), "23:30")

    def test_the_descent_is_shortened_rather_than_overrunning_a_short_night(self):
        cfg = {**BASE_CFG, "descend_minutes": 300}
        p = self.plan(datetime(2026, 10, 7, 1, 45, tzinfo=CT), cfg=cfg)   # 3h night
        self.assertLessEqual(p["descend_minutes"], p["window_minutes"] // 2)

    def test_a_night_too_short_for_a_phase_drops_it_instead_of_churning_the_pump(self):
        p = self.plan(datetime(2026, 10, 6, 23, 45, tzinfo=CT))   # 1h after lights-out
        self.assertIn("early_off", p["dropped"])
        self.assertIsNone(p["moments"]["early_off"])

    def test_the_ladder_moves_the_floor_and_the_lift_but_never_the_entry(self):
        # Only base_temp_f is laddered; how warm the bed is to climb into is tuned
        # on its own (he set 95F himself and called it too hot to sleep at).
        self.assertEqual(np.phase_temps(BASE_CFG, 72), {"entry": 92, "base": 72})
        self.assertEqual(np.phase_temps(BASE_CFG, 68), {"entry": 92, "base": 68})

    def test_temperatures_are_clamped_to_what_the_hardware_accepts(self):
        self.assertEqual(np.phase_temps({**BASE_CFG, "entry_temp_f": 200}, 72)["entry"], 115)
        self.assertEqual(np.phase_temps(BASE_CFG, 10)["base"], 55)

    def test_anchors_that_disagree_are_refused_rather_than_turned_into_a_24h_night(self):
        # lights-out and wake on the same clock minute: the day-shift would make this
        # a 24-hour "night" and put the midpoint phase at lunchtime.
        with self.assertRaises(np.PlanError) as cm:
            np.build(datetime(2026, 10, 6, 22, 45, tzinfo=CT), 76, BASE_CFG, dark_t=time(22, 45))
        self.assertIn("anchors disagree", str(cm.exception))


class LightsAnchor(unittest.TestCase):
    """lights_dark_time() reads the Govee fade job, so govee-lights owns the time."""

    def fake_cron(self, job):
        class Done:
            returncode = 0
            stdout = json.dumps({"jobs": [job] if job else []})
            stderr = ""
        return patch.object(np.subprocess, "run", lambda *a, **k: Done())

    def job(self, expr="0 22 * * *", seconds="2700", enabled=True):
        return {"id": np.BEDROOM_FADE_JOB, "enabled": enabled,
                "payload": {"schedule": {"expr": expr},
                            "argv": ["python3", "winddown_fade.py", "--duration-seconds", seconds]}}

    def test_dark_is_the_fade_start_plus_its_duration(self):
        with self.fake_cron(self.job()):
            self.assertEqual(np.lights_dark_time()[0], time(22, 45))

    def test_a_longer_fade_moves_dark_later(self):
        with self.fake_cron(self.job(seconds="5400")):
            self.assertEqual(np.lights_dark_time()[0], time(23, 30))

    def test_a_missing_or_disabled_job_falls_back_and_says_so(self):
        for job in (None, self.job(enabled=False)):
            with self.fake_cron(job):
                dark, why = np.lights_dark_time()
            self.assertEqual(dark, np.FALLBACK_LIGHTS_DARK)
            self.assertIn("fallback", why)


class NightlyRetime(unittest.TestCase):
    def test_every_job_is_retimed_onto_the_plan(self):
        out = run_night_schedule(wake=datetime(2026, 10, 7, 7, 30, tzinfo=CT))
        self.assertEqual(out["report"]["status"], "ok")
        # "rate" joined the four bed phases on 2026-10-07 (the trial rating prompt,
        # wake 07:30 + 60).
        self.assertEqual(out["report"]["at"],
                         {"entry": "22:15", "descend": "22:45", "early_off": "04:30",
                          "off": "08:00", "rate": "08:30"})
        self.assertEqual([e for e, _ in out["edits"]],
                         ["15 22 * * *", "45 22 * * *", "30 4 * * *", "0 8 * * *"])

    def test_a_later_alarm_moves_the_morning_and_the_lift_with_it(self):
        out = run_night_schedule(wake=datetime(2026, 10, 7, 9, 30, tzinfo=CT))
        self.assertEqual(out["report"]["at"]["off"], "10:00")
        self.assertEqual(out["report"]["at"]["early_off"], "04:30")
        self.assertEqual(out["report"]["at"]["entry"], "22:15")

    def test_a_switched_off_alarm_keeps_the_pad_on_until_the_backstop(self):
        # The failure this prevents: read_plan() still returns a disabled alarm's
        # time, and switching the pad off then would cut the cooling mid-sleep on
        # exactly the morning he meant to sleep in.
        out = run_night_schedule(wake=datetime(2026, 10, 7, 7, 30, tzinfo=CT), alarm_on=False)
        self.assertEqual(out["report"]["at"]["off"], "10:00")
        self.assertIs(out["report"]["alarm_on"], False)
        self.assertIn("rather than cutting out mid-sleep", out["report"]["note"])

    def test_the_backstop_is_not_pulled_back_before_bedtime(self):
        out = run_night_schedule(wake=datetime(2026, 10, 7, 7, 30, tzinfo=CT), alarm_on=False,
                                 cfg={**BASE_CFG, "no_alarm_off_time": "21:00"})
        off = out["report"]["at"]["off"]
        self.assertEqual(off, "21:00")
        edited = dict((argv[argv.index("--expected-start") + 1], e) for e, argv in out["edits"])
        self.assertIn("21:00", edited)

    def test_a_dropped_phase_is_reported_and_its_job_left_alone(self):
        out = run_night_schedule(wake=datetime(2026, 10, 6, 23, 45, tzinfo=CT))
        self.assertIn("early_off", out["report"]["dropped"])
        self.assertEqual(len(out["edits"]), 3)

    def test_the_report_names_where_both_anchors_came_from(self):
        out = run_night_schedule(wake=datetime(2026, 10, 7, 7, 30, tzinfo=CT))
        self.assertIn("lights_dark_from", out["report"])
        self.assertIn("base_from", out["report"])

    def test_a_disabled_job_is_not_reported_as_success(self):
        out = run_night_schedule(wake=datetime(2026, 10, 7, 7, 30, tzinfo=CT),
                                 problems=["chilipad-descend is disabled"])
        self.assertEqual(out["report"]["status"], "will_not_run")
        self.assertIn("chilipad-descend is disabled", out["report"]["problems"])


class Trial(unittest.TestCase):
    """The ladder, and that ratings are recorded against the night's real temperature."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.file = Path(tmp.name) / "chilipad_nights.json"
        p = patch.object(trial, "_state_file", lambda: self.file)
        p.start()
        self.addCleanup(p.stop)
        self.cfg = {"base_temp_f": 68,
                    "trial": {"active": True, "nights_per_step": 3, "ladder_f": [72, 70, 68]}}

    def test_the_first_rung_is_what_tonight_runs_at(self):
        base, why = trial.tonight_base(self.cfg)
        self.assertEqual(base, 72)
        self.assertIn("rung 1 of 3", why)

    def test_running_twice_in_one_evening_counts_the_night_once(self):
        # night_schedule runs at 9:20pm AND whenever the Hatch alarm changes (it is
        # in alarm.py's FOLLOWERS), so a non-idempotent advance would race through
        # the ladder and file his rating against a temperature he never slept at.
        first = trial.advance(self.cfg, today="2026-10-06")
        again = trial.advance(self.cfg, today="2026-10-06")
        self.assertEqual(first["nights_on_rung"], 1)
        self.assertEqual(again["nights_on_rung"], 1)
        self.assertEqual(again["already_counted"], "2026-10-06")
        self.assertEqual(trial.load_state()["nights_on_step"], 1)
        trial.advance(self.cfg, today="2026-10-07")
        self.assertEqual(trial.load_state()["nights_on_step"], 2)

    def test_a_rung_lasts_the_configured_number_of_nights(self):
        for d in range(1, 4):
            trial.advance(self.cfg, today=f"2026-10-0{d}")
        self.assertEqual(trial.tonight_base(self.cfg)[0], 70)
        for d in range(4, 7):
            trial.advance(self.cfg, today=f"2026-10-0{d}")
        self.assertEqual(trial.tonight_base(self.cfg)[0], 68)

    def test_the_ladder_ending_settles_and_hands_back_to_base_temp(self):
        for d in range(1, 10):
            trial.advance(self.cfg, today=f"2026-10-{d:02d}")
        self.assertTrue(trial.load_state()["settled"])
        base, why = trial.tonight_base(self.cfg)
        self.assertEqual(base, 68)
        self.assertIn("settled", why)

    def test_an_inactive_trial_uses_base_temp_f(self):
        cfg = {**self.cfg, "trial": {**self.cfg["trial"], "active": False}}
        self.assertEqual(trial.tonight_base(cfg)[0], 68)

    def test_a_rating_is_stored_against_the_temperature_it_was_slept_at(self):
        trial.rate(self.cfg, "hot", "kicked the blanket off", for_date="2026-10-07")
        for d in range(1, 4):
            trial.advance(self.cfg, today=f"2026-10-0{d}")
        trial.rate(self.cfg, "good", None, for_date="2026-10-10")
        rows = trial.report(self.cfg)["by_temperature"]
        self.assertEqual(rows[72]["hot"], 1)
        self.assertEqual(rows[70]["good"], 1)
        self.assertIn("2026-10-07: kicked the blanket off", rows[72]["notes"])

    def test_the_report_states_what_he_said_and_draws_no_conclusion(self):
        trial.rate(self.cfg, "hot", None)
        out = trial.report(self.cfg)
        self.assertEqual(set(out) & {"recommendation", "verdict", "best", "diagnosis"}, set())

    def test_a_nonsense_rating_is_refused(self):
        with self.assertRaises(sc.SleepMeError):
            trial.rate(self.cfg, "clammy", None)

    def test_stopping_mid_ladder_freezes_the_trial(self):
        trial.advance(self.cfg, today="2026-10-06")
        state = trial.load_state()
        state["settled"] = True
        trial.save_state(state)
        self.assertEqual(trial.tonight_base(self.cfg)[0], 68)


class Descent(unittest.TestCase):
    """The gradual walk down from a warm bed to a cool one."""

    def test_the_descent_ends_exactly_on_the_sleeping_temperature(self):
        steps = ramp.plan_steps(92, 72, 2700)
        self.assertEqual(steps[-1][0], 72, "rounding must never leave the floor missed")
        self.assertTrue(all(55 <= f <= 115 for f, _ in steps))

    def test_it_descends_and_never_jumps_back_up(self):
        temps = [f for f, _ in ramp.plan_steps(92, 72, 2700)]
        self.assertEqual(temps, sorted(temps, reverse=True))

    def test_the_first_step_is_immediate_so_the_bed_starts_cooling_at_once(self):
        self.assertEqual(ramp.plan_steps(92, 72, 2700)[0][1], 0)

    def test_the_steps_fit_inside_the_duration_and_the_rate_limit(self):
        steps = ramp.plan_steps(92, 72, 2700)
        total = sum(w for _, w in steps)
        self.assertLessEqual(total, 2700 + 1)
        # 10 calls across 45 min is nowhere near the account's 10/min ceiling.
        self.assertLessEqual(len(steps), 2700 / 60 * sc.MAX_REQUESTS_PER_MINUTE)
        self.assertTrue(all(w == 0 or w >= ramp.MIN_STEP_SECONDS for _, w in steps))

    def test_a_short_duration_still_spaces_the_calls_out(self):
        # A 20F descent in 60s would be 10 calls in a minute — right on the ceiling.
        steps = ramp.plan_steps(92, 72, 60)
        self.assertTrue(all(w == 0 or w >= ramp.MIN_STEP_SECONDS for _, w in steps))

    def test_an_unchanged_temperature_is_a_single_step(self):
        self.assertEqual(ramp.plan_steps(72, 72, 2700), [(72, 0)])

    def test_it_can_also_climb_if_the_numbers_are_ever_reversed(self):
        temps = [f for f, _ in ramp.plan_steps(72, 92, 1800)]
        self.assertEqual(temps, sorted(temps))
        self.assertEqual(temps[-1], 92)


class Upkeep(unittest.TestCase):
    """Water, the monthly cleaner and the semi-annual deep clean.

    sleep.me state that not following the maintenance schedule voids the warranty,
    so the intervals here are theirs, not invented: 30 days and 182 days.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for name, attr in (("chilipad_maintenance.json", "_record"),
                           ("chilipad_maintenance_prompt.json", "_prompt_state")):
            p = patch.object(mt, attr, lambda _n=name: root / _n)
            p.start()
            self.addCleanup(p.stop)

    def rec(self, **last_done):
        return {"last_done": last_done, "last_asked": {}, "log": []}

    def test_a_task_never_recorded_is_due_now_not_silently_skipped(self):
        self.assertEqual(mt.due_in_days("cleaner", self.rec()), 0)
        self.assertEqual(mt.due_in_days("deep", self.rec()), 0)

    def test_the_intervals_are_the_manuals_monthly_and_semi_annual(self):
        today = date(2026, 10, 6)
        rec = self.rec(cleaner="2026-10-06", deep="2026-10-06")
        self.assertEqual(mt.due_in_days("cleaner", rec, today), 30)
        self.assertEqual(mt.due_in_days("deep", rec, today), 182)

    def test_water_is_never_a_calendar_guess(self):
        # It has to come from the device's own is_water_low, so there is no date
        # arithmetic for it at all.
        self.assertIsNone(mt.due_in_days("water", self.rec()))

    def test_a_low_reservoir_outranks_everything_else(self):
        rec = self.rec(cleaner="2020-01-01", deep="2020-01-01")
        self.assertEqual(mt.next_task(rec, water_low=True), "water")

    def test_only_one_thing_is_asked_at_a_time(self):
        # Three prompts at once is the message pile-up he has asked not to get.
        rec = self.rec()
        first = mt.next_task(rec, water_low=True)
        self.assertEqual(first, "water")
        rec["last_asked"]["water"] = date.today().isoformat()
        self.assertEqual(mt.next_task(rec, water_low=True), "deep")

    def test_nothing_due_asks_nothing(self):
        today = date(2026, 10, 6)
        rec = self.rec(cleaner="2026-10-01", deep="2026-09-01")
        self.assertIsNone(mt.next_task(rec, water_low=False, today=today))

    def test_an_unanswered_prompt_waits_out_its_cooldown(self):
        today = date(2026, 10, 6)
        rec = self.rec(cleaner="2026-10-01")          # cleaner not yet due
        rec["last_asked"]["deep"] = today.isoformat()
        self.assertIsNone(mt.next_task(rec, water_low=False, today=today))
        later = today + timedelta(days=mt.COOLDOWN_DAYS)
        self.assertEqual(mt.next_task(rec, water_low=False, today=later), "deep")

    def test_the_cooldown_is_per_task_so_a_held_job_does_not_block_the_others(self):
        today = date(2026, 10, 6)
        rec = self.rec()                               # both overdue
        rec["last_asked"]["deep"] = today.isoformat()
        self.assertEqual(mt.next_task(rec, water_low=False, today=today), "cleaner")

    def test_water_is_chased_sooner_than_the_dated_jobs(self):
        self.assertLess(mt.WATER_COOLDOWN_DAYS, mt.COOLDOWN_DAYS)

    def test_marking_done_restarts_that_clock_and_logs_it(self):
        mt.save_record(self.rec())
        mt.mark("cleaner", done=True)
        rec = mt.load_record()
        self.assertEqual(rec["last_done"]["cleaner"], date.today().isoformat())
        self.assertEqual(rec["log"][-1]["task"], "cleaner")
        self.assertEqual(mt.due_in_days("cleaner", rec), mt.CLEANER_DAYS)

    def test_a_snooze_does_not_count_as_done(self):
        mt.save_record(self.rec())
        mt.mark("deep", done=False)
        self.assertNotIn("deep", mt.load_record()["last_done"])

    def test_every_prompt_names_distilled_water_where_it_matters(self):
        # The manual is explicit that tap water deposits minerals and cuts the
        # cooling, and it is the easiest instruction to get wrong.
        for task in ("water", "cleaner", "deep"):
            self.assertIn("distilled", mt.PROMPTS[task].lower(), task)

    def test_the_deep_clean_prompt_carries_all_five_steps(self):
        text = mt.PROMPTS["deep"]
        for fragment in ("hose", "reservoir", "distilled", "drain key", "gentle"):
            self.assertIn(fragment, text.lower(), fragment)

    def test_prompts_are_plain_text_with_no_markdown_to_mangle(self):
        # Model text going to Telegram has to be plain; these are literals, so the
        # guard is that nobody later adds markdown to them.
        for task, text in mt.PROMPTS.items():
            for bad in ("**", "##", "`", "](" ):
                self.assertNotIn(bad, text, f"{task} contains {bad!r}")

    def test_the_tap_handler_reads_the_senders_own_state_file(self):
        # The button contract: the handler must resolve which task was prompted
        # from the state the SENDER wrote, never from a guess.
        mt._prompt_state().write_text(json.dumps(
            {"task": "deep", "message_id": 42, "text": "x", "asked_on": "2026-10-06"}))
        self.assertEqual(mt.pending_task(), ("deep", 42, "x"))

    def test_no_prompt_waiting_resolves_to_nothing_rather_than_a_wrong_task(self):
        self.assertEqual(mt.pending_task(), (None, None, None))


class CronEnableCheck(unittest.TestCase):
    def fake_cron(self, jobs, returncode=0):
        class Done:
            pass
        done = Done()
        done.returncode = returncode
        done.stdout = json.dumps({"jobs": jobs})
        done.stderr = ""
        return patch.object(chilipad_jobs.subprocess, "run", lambda *a, **k: done)

    def test_all_enabled_is_no_problems(self):
        with self.fake_cron([{"id": "J-warm", "name": "chilipad-descend", "enabled": True}]):
            self.assertEqual(chilipad_jobs.will_not_run(["J-warm"]), [])

    def test_a_disabled_job_is_named(self):
        with self.fake_cron([{"id": "J-warm", "name": "chilipad-descend", "enabled": False}]):
            self.assertEqual(chilipad_jobs.will_not_run(["J-warm"]),
                             ["chilipad-descend is disabled"])

    def test_a_deleted_job_is_not_read_as_healthy(self):
        with self.fake_cron([]):
            self.assertEqual(chilipad_jobs.will_not_run(["J-warm"]),
                             ["J-warm is not in the cron list at all"])

    def test_an_unreadable_cron_list_is_unknown_not_fine(self):
        with self.fake_cron([], returncode=1):
            self.assertEqual(len(chilipad_jobs.will_not_run(["J-warm"])), 1)

    def test_no_jobs_configured_says_so(self):
        self.assertIn("no cron jobs are configured", chilipad_jobs.will_not_run([None, None])[0])


class TrialNightCounter(unittest.TestCase):
    """Which night of the rung is being slept. It read one ahead until 2026-10-07: advance()
    runs at 21:20, before the night, so after the evening retime it announced "night 3 of 3"
    on a night that was only the second."""

    CFG = {"base_temp_f": 80, "trial": {"active": True, "nights_per_step": 3, "ladder_f": [80, 78]}}

    def test_before_tonight_is_counted_it_names_the_night_to_come(self):
        state = {"step": 0, "nights_on_step": 1, "last_counted": "2026-10-06"}
        self.assertEqual(trial.tonight_base(self.CFG, state)[1],
                         "trial rung 1 of 2, night 2 of 3")

    def test_once_tonight_is_counted_it_names_the_night_being_slept(self):
        state = {"step": 0, "nights_on_step": 1, "last_counted": date.today().isoformat()}
        self.assertEqual(trial.tonight_base(self.CFG, state)[1],
                         "trial rung 1 of 2, night 1 of 3")

    def test_a_fresh_rung_never_reads_as_night_zero(self):
        state = {"step": 0, "nights_on_step": 0, "last_counted": date.today().isoformat()}
        self.assertIn("night 1 of 3", trial.tonight_base(self.CFG, state)[1])


class TrialRestart(unittest.TestCase):
    """--restart: a re-based ladder must not hand old ratings to a new rung."""

    CFG = {"base_temp_f": 80, "trial": {"active": True, "nights_per_step": 3, "ladder_f": [80, 78, 82]}}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        f = Path(self.tmp.name) / "chilipad_nights.json"
        p = patch.object(trial, "_state_file", lambda: f)
        p.start()
        self.addCleanup(p.stop)

    def test_it_rewinds_to_rung_one_and_keeps_the_nights_already_rated(self):
        trial.save_state({"step": 2, "nights_on_step": 2, "settled": True, "last_counted": "2026-10-07",
                          "nights": [{"date": "2026-10-06", "base_f": 72, "rating": "cold"}]})
        out = trial.restart(self.CFG, "ladder re-based")
        state = trial.load_state()
        self.assertEqual((out["restarted"], out["rung_1_f"], out["nights_kept"]), (True, 80, 1))
        self.assertEqual((state["step"], state["nights_on_step"], state["settled"]), (0, 0, False))
        self.assertNotIn("last_counted", state, "tonight must be countable again")
        self.assertEqual(len(state["nights"]), 1, "the ratings are history, not casualties")
        self.assertEqual(state["restarts"][-1]["ladder"], [80, 78, 82])


def _FrozenClock(when):
    """A stand-in for manual_hold's `datetime` whose .now() is fixed. A datetime
    subclass, so fromisoformat() and the rest keep working."""
    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return when.astimezone(tz) if tz else when
    return _Frozen


class HisOwnFloor(unittest.TestCase):
    """2026-10-08, after three nights that each ended too cold: "The absolute coldest it should
    go right now is 85 degrees." `min_temp_f` is a floor on everything the schedule decides AND
    on a hand-typed temperature, enforced where the temperature reaches the bed."""

    CFG = {"min_temp_f": 85, "entry_temp_f": 92, "base_temp_f": 85,
           "trial": {"active": True, "nights_per_step": 3, "ladder_f": [85, 86, 87, 88]}}

    def test_no_floor_configured_changes_nothing(self):
        self.assertIsNone(sc.floor_f({}))
        self.assertEqual(sc.at_least_floor(60, {}), 60)

    def test_a_garbled_floor_is_ignored_rather_than_guessed(self):
        for junk in ({"min_temp_f": "cold"}, {"min_temp_f": None}, {"min_temp_f": {}}):
            self.assertIsNone(sc.floor_f(junk), junk)

    def test_the_floor_lifts_anything_below_it_and_leaves_the_rest(self):
        self.assertEqual(sc.at_least_floor(72, self.CFG), 85)
        self.assertEqual(sc.at_least_floor(85, self.CFG), 85)
        self.assertEqual(sc.at_least_floor(92, self.CFG), 92)

    def test_a_stale_ladder_below_the_floor_is_lifted_and_deduplicated(self):
        cfg = {**self.CFG, "trial": {**self.CFG["trial"], "ladder_f": [72, 80, 84, 86]}}
        self.assertEqual(trial.ladder(cfg), [85, 86], "three rungs under the floor collapse to one")

    def test_a_stale_base_below_the_floor_never_reaches_the_night(self):
        cfg = {**self.CFG, "base_temp_f": 72, "trial": {"active": False}}
        self.assertEqual(trial.tonight_base(cfg)[0], 85)
        self.assertEqual(np.phase_temps(cfg, 72)["base"], 85)

    def test_the_descent_lands_on_the_floor_instead_of_stepping_under_it(self):
        steps = [t for t, _ in ramp.plan_steps(92, 72, 2700, self.CFG)]
        self.assertEqual(min(steps), 85)
        self.assertEqual(steps[-1], 85, "the descent still ENDS somewhere, it just ends at the floor")

    def test_the_bed_refuses_a_temperature_under_his_floor_wherever_it_came_from(self):
        client = sc.SleepMeClient(token="t")
        with patch.object(sc, "load_config", return_value=self.CFG), \
             patch.object(client, "update", side_effect=AssertionError("reached the bed")):
            with self.assertRaises(sc.SleepMeError) as caught:
                client.set_temperature("dev", "set_temperature_f", 80)
        self.assertIn("below the 85°F floor", str(caught.exception))

    def test_at_the_floor_and_above_still_goes_through(self):
        client = sc.SleepMeClient(token="t")
        with patch.object(sc, "load_config", return_value=self.CFG), \
             patch.object(client, "update", return_value={"ok": True}) as sent:
            client.set_temperature("dev", "set_temperature_f", 85)
            client.set_temperature("dev", "set_temperature_f", 92)
        self.assertEqual(sent.call_count, 2)

    def test_an_unreadable_config_never_blocks_the_bed(self):
        client = sc.SleepMeClient(token="t")
        with patch.object(sc, "load_config", side_effect=sc.SleepMeError("no config")), \
             patch.object(client, "update", return_value={"ok": True}) as sent:
            client.set_temperature("dev", "set_temperature_f", 80)
        sent.assert_called_once()

    def test_the_hard_safety_band_still_wins_over_a_wider_floor(self):
        client = sc.SleepMeClient(token="t")
        with patch.object(sc, "load_config", return_value={"min_temp_f": 50}), \
             patch.object(client, "update", side_effect=AssertionError("reached the bed")):
            with self.assertRaises(sc.SleepMeError) as caught:
                client.set_temperature("dev", "set_temperature_f", 110)
        self.assertIn("hard limit", str(caught.exception))


class ManualHold(unittest.TestCase):
    """2026-10-06, the first live night: The user said "I'm too cold rn" while ramp.py was walking
    76°F down to 72°F, and the lift job would have cooled the bed again later. A temperature
    he sets by hand now outranks the schedule until the morning-off."""

    CFG = {"jobs": {"off": "off-job"}, "no_alarm_off_time": "10:00"}
    NOW = datetime(2026, 10, 6, 23, 19, tzinfo=ZoneInfo("America/Chicago"))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = patch.object(mh, "HOLD", Path(self.tmp.name) / "chilipad_hold.json")
        p.start()
        self.addCleanup(p.stop)

    def test_it_expires_at_the_morning_off_the_cron_store_really_fires(self):
        with patch.object(np, "_cron_job", return_value={"schedule": {"expr": "0 8 * * *"}}):
            self.assertEqual(mh.until_morning(self.NOW, self.CFG),
                             self.NOW.replace(hour=8, minute=0, second=0, microsecond=0) + timedelta(days=1))

    def test_an_unreadable_cron_store_falls_back_to_the_configured_time(self):
        with patch.object(np, "_cron_job", return_value=None):
            self.assertEqual(mh.until_morning(self.NOW, self.CFG).strftime("%H:%M"), "10:00")

    def test_a_hold_is_live_until_its_expiry_and_then_is_no_hold(self):
        with patch.object(np, "_cron_job", return_value={"schedule": {"expr": "0 8 * * *"}}):
            rec = mh.start("80°F", now=self.NOW, cfg=self.CFG)
        until = datetime.fromisoformat(rec["until"])
        self.assertTrue(mh.held(now=until - timedelta(minutes=1)))
        self.assertFalse(mh.held(now=until), "an expired hold must not silence tomorrow's schedule")

    def test_an_unreadable_hold_file_is_no_hold(self):
        for junk in ("", "{", '{"at": "x"}', '{"until": "whenever"}'):
            mh.HOLD.write_text(junk)
            self.assertFalse(mh.held(now=self.NOW), junk)

    def test_the_ramp_abandons_its_descent_when_he_sets_the_bed_himself(self):
        # The hold is written at NOW and expires at the next morning-off, so ramp.main()
        # must be shown that same NOW. Letting it read the real clock made this test pass
        # before 10am and fail after it (found 2026-10-07 at 21:13, failing) — a test
        # whose result depends on the hour it is run proves nothing either way.
        mh.start("80°F", now=self.NOW, cfg=self.CFG)
        client = unittest.mock.MagicMock()
        with patch.object(ramp, "SleepMeClient", return_value=client), \
             patch.object(ramp, "resolve_device", return_value=("dev", "bed")), \
             patch.object(ramp, "load_config", return_value={"entry_temp_f": 92, "base_temp_f": 72}), \
             patch.object(ramp.time, "sleep"), \
             patch.object(mh, "datetime", _FrozenClock(self.NOW)), \
             patch.object(sys, "argv", ["ramp.py", "--from", "92", "--to", "72", "--duration-seconds", "2700"]):
            with _capture() as out:
                ramp.main()
        said = json.loads(out.getvalue().strip().splitlines()[-1])
        self.assertIn("stopped", said)
        self.assertEqual(client.set_temperature.call_count, 0, "not one more step may be applied")

    def test_the_ramp_runs_normally_with_no_hold(self):
        client = unittest.mock.MagicMock()
        with patch.object(ramp, "SleepMeClient", return_value=client), \
             patch.object(ramp, "resolve_device", return_value=("dev", "bed")), \
             patch.object(ramp, "load_config", return_value={"entry_temp_f": 92, "base_temp_f": 72}), \
             patch.object(ramp.time, "sleep"), \
             patch.object(sys, "argv", ["ramp.py", "--from", "92", "--to", "72", "--duration-seconds", "2700"]):
            with _capture() as out:
                ramp.main()
        said = json.loads(out.getvalue().strip().splitlines()[-1])
        self.assertNotIn("stopped", said)
        self.assertTrue(client.set_temperature.call_count >= 2)


class RatePrompt(unittest.TestCase):
    """The nightly trial rating prompt. Built 2026-10-07, NOT live: a new automated
    message needs the user's go-ahead on wording and trigger first."""

    CFG = {"trial": {"active": True, "nights_per_step": 3, "ladder_f": [80, 78]},
           "rate_prompt": {"enabled": True, "morning_offset_minutes": 60}}
    TODAY = date(2026, 10, 8)

    def setUp(self):
        # trial's path comes from a FUNCTION, not a module attribute. An earlier
        # version of this setUp patched `trial.STATE` behind `if hasattr(...)`,
        # which does not exist — so the patch silently did nothing and the test
        # wrote a fabricated rating into the user's real chilipad_nights.json
        # (2026-10-07, restored from git). No silent guards here: both patches
        # target something that exists, so a rename fails loudly instead.
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.nights = Path(self.tmp.name) / "nights.json"
        for p in (patch.object(rp, "PROMPT_STATE", Path(self.tmp.name) / "prompt.json"),
                  patch.object(trial, "_state_file", lambda: self.nights)):
            p.start()
            self.addCleanup(p.stop)

    def test_this_suite_cannot_touch_the_live_trial_log(self):
        """The guard for the bug above: if either redirect stops working, this
        fails before any test writes to the real file."""
        self.assertEqual(trial._state_file(), self.nights)
        self.assertFalse(str(rp.PROMPT_STATE).startswith("~/.openclaw/workspace"))

    # --- the gate -------------------------------------------------------
    def test_disabled_refuses_to_send(self):
        cfg = {**self.CFG, "rate_prompt": {"enabled": False}}
        ok, why = rp.should_ask(cfg, away=False, today=self.TODAY)
        self.assertFalse(ok)
        self.assertIn("not approved", why)

    def test_the_live_config_matches_the_approved_trigger(self):
        """The user approved the wording and wake+60 on 2026-10-07. Changing either
        needs his sign-off, so a silent edit should fail here."""
        live = json.loads((Path(__file__).resolve().parent.parent /
                           "config" / "chilipad_config.json").read_text())
        self.assertTrue(live["rate_prompt"]["enabled"])
        self.assertEqual(live["rate_prompt"]["morning_offset_minutes"], 60)
        self.assertIn("rate", live["jobs"], "the prompt is enabled but has no cron job")

    def test_the_rate_job_takes_no_flags_it_cannot_parse(self):
        """control.py and ramp.py parse --skip-if-away and the misfire flags;
        rate_prompt.py does not, and argv_for replaces the whole argv."""
        argv = ns.argv_for("rate", "08:30")
        self.assertTrue(argv[1].endswith("rate_prompt.py"))
        for flag in ("--skip-if-away", "--expected-start", "--late-grace-min"):
            self.assertNotIn(flag, argv)

    def test_the_rate_moment_follows_the_alarm(self):
        cfg = {"prewarm_minutes": 30, "descend_minutes": 45,
               "morning_off_delay_minutes": 30, "early_off_time": "04:30",
               "rate_prompt": {"morning_offset_minutes": 60}}
        wake = datetime(2026, 10, 8, 7, 30, tzinfo=ZoneInfo("America/Chicago"))
        plan = np.build(wake, 80, cfg, dark_t=time(22, 45))
        self.assertEqual(np.hhmm(plan["moments"]["rate"]), "08:30")

    def test_the_rate_moment_lands_after_the_pump_stops(self):
        # A "how did you sleep" that arrives while the bed is still running is
        # asking about a night that has not finished.
        cfg = {"prewarm_minutes": 30, "descend_minutes": 45,
               "morning_off_delay_minutes": 30, "early_off_time": "04:30",
               "rate_prompt": {"morning_offset_minutes": 60}}
        wake = datetime(2026, 10, 8, 7, 30, tzinfo=ZoneInfo("America/Chicago"))
        plan = np.build(wake, 80, cfg, dark_t=time(22, 45))
        self.assertGreater(plan["moments"]["rate"], plan["moments"]["off"])

    def test_it_asks_when_everything_is_in_order(self):
        ok, why = rp.should_ask(self.CFG, away=False, today=self.TODAY)
        self.assertTrue(ok, why)

    def test_a_stopped_trial_is_not_asked_about(self):
        cfg = {**self.CFG, "trial": {"active": False}}
        ok, why = rp.should_ask(cfg, away=False, today=self.TODAY)
        self.assertFalse(ok)
        self.assertIn("not running", why)

    def test_away_is_not_asked(self):
        # He did not sleep in this bed, so there is nothing to rate.
        ok, why = rp.should_ask(self.CFG, away=True, today=self.TODAY)
        self.assertFalse(ok)
        self.assertIn("away", why)

    def test_it_does_not_ask_twice_in_one_day(self):
        rp.PROMPT_STATE.write_text(json.dumps({"asked_on": self.TODAY.isoformat()}))
        ok, why = rp.should_ask(self.CFG, away=False, today=self.TODAY)
        self.assertFalse(ok)
        self.assertIn("already asked", why)

    def test_it_does_not_ask_if_he_already_rated_by_hand(self):
        # trial.py --rate run himself; asking would file a second rating for one night.
        trial.save_state({"nights": [{"date": self.TODAY.isoformat(), "base_f": 80,
                                      "rating": "good"}]})
        ok, why = rp.should_ask(self.CFG, away=False, today=self.TODAY)
        self.assertFalse(ok)
        self.assertIn("already rated", why)

    def test_an_unreadable_prompt_state_does_not_block_asking(self):
        rp.PROMPT_STATE.write_text("{ not json")
        ok, _ = rp.should_ask(self.CFG, away=False, today=self.TODAY)
        self.assertTrue(ok)

    # --- the wording ----------------------------------------------------
    def test_the_approved_wording(self):
        """The exact text shown to the user on 2026-10-07. Changing it needs his
        sign-off, so an edit that slips through should fail here."""
        self.assertEqual(rp.compose(80), "🛏 How did you sleep at 80°F?")

    def test_it_names_the_temperature_being_rated(self):
        self.assertIn("78°F", rp.compose(78))

    def test_it_is_one_line(self):
        # He cut the upkeep prompt's second line himself.
        self.assertEqual(len(rp.compose(80).splitlines()), 1)

    def test_plain_text_with_no_markdown_to_mangle(self):
        for ch in ("*", "_", "`", "["):
            self.assertNotIn(ch, rp.compose(80))

    # --- the buttons ----------------------------------------------------
    def test_every_button_maps_to_a_real_trial_verdict(self):
        for verdict, _, _ in rp.BUTTONS:
            self.assertIn(verdict, trial.RATINGS)

    def test_every_button_is_registered_in_the_dispatch_table(self):
        table = json.loads((Path("~/.openclaw/plugins/callback-dispatch/"
                                 "dispatch-table.json")).read_text())["exact"]
        for _, _, callback in rp.BUTTONS:
            self.assertIn(callback, table, f"{callback} would be a dead button")
            self.assertEqual(table[callback]["argv"][0],
                             "chilipad/scripts/rate_prompt.py")


class DetachedRamp(unittest.TestCase):
    """2026-10-08: a 45-minute ramp inside the cron worker froze every other job
    twice in one evening — the user's bed stayed off, then his lights stayed on. The
    cron job now launches the ramp and returns."""

    def test_the_descend_job_detaches(self):
        argv = ns.argv_for("descend", "22:45", 45)
        self.assertIn("--detach", argv)
        self.assertTrue(argv[1].endswith("ramp.py"))

    def test_only_descend_detaches(self):
        # The other phases are single API calls; they have nothing to hand off.
        for phase in ("entry", "off", "early_off"):
            self.assertNotIn("--detach", ns.argv_for(phase, "22:15"))

    def test_the_guards_are_still_on_the_detaching_job(self):
        # argv_for replaces the whole argv, so a dropped guard is silent.
        argv = ns.argv_for("descend", "22:45", 45)
        for flag in ("--skip-if-away", "--expected-start", "--late-grace-min"):
            self.assertIn(flag, argv)

    def test_relaunch_strips_the_flag_so_the_child_does_not_loop(self):
        """Without this the child would detach again, forever."""
        seen = {}

        class FakeProc:
            pid = 4242

        def fake_popen(cmd, **kw):
            seen["cmd"] = cmd
            seen["kw"] = kw
            return FakeProc()

        log = Path(self.tmp.name) / "r.log"
        with patch.object(detach.subprocess, "Popen", fake_popen):
            out = detach.relaunch_detached(log, argv=["/x/ramp.py", "--to", "85", "--detach"])
        self.assertEqual(out["pid"], 4242)
        self.assertNotIn("--detach", seen["cmd"])
        self.assertIn("--to", seen["cmd"])

    def test_the_child_gets_its_own_session_so_it_survives_the_worker(self):
        class FakeProc:
            pid = 1
        seen = {}

        def fake_popen(cmd, **kw):
            seen.update(kw)
            return FakeProc()

        log = Path(self.tmp.name) / "r.log"
        with patch.object(detach.subprocess, "Popen", fake_popen):
            detach.relaunch_detached(log, argv=["/x/ramp.py"])
        self.assertTrue(seen["start_new_session"],
                        "without its own session the ramp dies with the cron worker")

    def test_output_goes_to_a_file_not_a_pipe(self):
        """Nothing stays attached for 45 minutes to drain a pipe, and a full pipe
        buffer would block the ramp mid-descent."""
        class FakeProc:
            pid = 1
        seen = {}

        def fake_popen(cmd, **kw):
            seen.update(kw)
            return FakeProc()

        log = Path(self.tmp.name) / "r.log"
        with patch.object(detach.subprocess, "Popen", fake_popen):
            detach.relaunch_detached(log, argv=["/x/ramp.py"])
        self.assertNotEqual(seen["stdout"], -1)   # not subprocess.PIPE
        self.assertTrue(log.exists())

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)


if __name__ == "__main__":
    unittest.main(verbosity=2)
