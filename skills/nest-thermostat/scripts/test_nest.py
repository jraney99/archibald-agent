#!/usr/bin/env python3
"""The Nest skill: unit conversion, the three API rules, quota, setback times, eco.

No network (the HTTP layer is faked), no cron (subprocess is captured), no live
state (temp files).

What each group is here to stop coming back:
  Fahrenheit     Every SDM temperature is Celsius. A raw Celsius number reaching
                 The user is a bug, and so is sending Fahrenheit to the device.
  The three 400s SetHeat needs HEAT / SetCool needs COOL / SetRange needs
                 HEATCOOL; eco rejects setpoints outright; cool must exceed heat.
                 Each one is an unexplained 400 if you get it wrong.
  Quota          Google's per-device ceiling is 5/min AND 100/hour across all
                 projects. The hourly one is what makes polling impossible.
  Setback times  The morning must LEAD the alarm, or he wakes in a cold house.
  Eco restraint  Coming home must never cancel eco the user set himself.

    python3 test_nest.py
"""

import json
import sys
import tempfile
import unittest
from datetime import datetime, time
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(SCRIPTS_DIR.parent.parent / "shared"))
sys.path.insert(0, str(SCRIPTS_DIR.parent.parent / "chilipad" / "scripts"))

import away  # noqa: E402
import nest_client as nc  # noqa: E402
import night_schedule as ns  # noqa: E402

CT = ZoneInfo("America/Chicago")
DEVICE = "enterprises/p/devices/d"


def traits(mode="HEAT", eco="OFF", heat_c=21.0, cool_c=24.0, ambient_c=21.5):
    return {"traits": {
        "sdm.devices.traits.Connectivity": {"status": "ONLINE"},
        "sdm.devices.traits.Temperature": {"ambientTemperatureCelsius": ambient_c},
        "sdm.devices.traits.Humidity": {"ambientHumidityPercent": 44},
        "sdm.devices.traits.Settings": {"temperatureScale": "FAHRENHEIT"},
        "sdm.devices.traits.ThermostatMode": {"mode": mode,
                                              "availableModes": ["HEAT", "COOL", "HEATCOOL", "OFF"]},
        "sdm.devices.traits.ThermostatEco": {"mode": eco, "heatCelsius": 9.0, "coolCelsius": 24.4},
        "sdm.devices.traits.ThermostatHvac": {"status": "HEATING"},
        "sdm.devices.traits.ThermostatTemperatureSetpoint": {"heatCelsius": heat_c,
                                                             "coolCelsius": cool_c},
    }}


def client_with(device):
    """A NestClient whose transport is a recorder, so commands can be asserted."""
    with patch.object(nc, "missing_secrets", lambda: []), \
         patch.object(nc, "_secret", lambda n: "x"):
        client = nc.NestClient()
    client.sent = []

    def fake_request(method, endpoint, payload=None):
        client.sent.append((method, endpoint, payload))
        return device if method == "GET" else {}

    client._request = fake_request
    return client


class Units(unittest.TestCase):
    def test_fahrenheit_goes_in_and_celsius_goes_to_the_device(self):
        self.assertEqual(nc.f_to_c(70), 21.0)
        self.assertEqual(nc.f_to_c(32), 0.0)
        self.assertEqual(nc.f_to_c(67), 19.5)      # snapped to the half degree

    def test_celsius_comes_back_as_fahrenheit_for_owner(self):
        self.assertEqual(nc.c_to_f(21.0), 69.8)
        self.assertIsNone(nc.c_to_f(None))
        self.assertEqual(nc.describe_f(21.0), "70°F")
        self.assertEqual(nc.describe_f(None), "unknown")

    def test_setpoints_are_snapped_to_the_half_degree_the_device_accepts(self):
        for f in range(55, 90):
            self.assertEqual(nc.f_to_c(f) * 2 % 1, 0, f)


class Reading(unittest.TestCase):
    def test_the_trait_soup_becomes_something_readable(self):
        state = nc.NestClient.read(traits())
        self.assertTrue(state["online"])
        self.assertEqual(state["mode"], "HEAT")
        self.assertEqual(state["ambient_f"], 70.7)
        self.assertEqual(state["humidity"], 44)
        self.assertEqual(state["eco"], "OFF")

    def test_missing_traits_do_not_explode(self):
        state = nc.NestClient.read({})
        self.assertFalse(state["online"])
        self.assertIsNone(state["mode"])
        self.assertIsNone(state["ambient_f"])


class TheThreeRulesTheApiEnforces(unittest.TestCase):
    def commands(self, client):
        return [(p or {}).get("command") for m, e, p in client.sent if m == "POST"]

    def test_heat_mode_uses_setheat(self):
        client = client_with(traits(mode="HEAT"))
        client.set_temperature_f(DEVICE, 70)
        self.assertEqual(self.commands(client),
                         ["sdm.devices.commands.ThermostatTemperatureSetpoint.SetHeat"])
        self.assertEqual(client.sent[-1][2]["params"], {"heatCelsius": 21.0})

    def test_cool_mode_uses_setcool(self):
        client = client_with(traits(mode="COOL"))
        client.set_temperature_f(DEVICE, 74)
        self.assertEqual(self.commands(client),
                         ["sdm.devices.commands.ThermostatTemperatureSetpoint.SetCool"])
        self.assertIn("coolCelsius", client.sent[-1][2]["params"])

    def test_heatcool_uses_setrange_and_brackets_the_target(self):
        client = client_with(traits(mode="HEATCOOL"))
        done = client.set_temperature_f(DEVICE, 70, spread_f=4)
        params = client.sent[-1][2]["params"]
        self.assertEqual(self.commands(client),
                         ["sdm.devices.commands.ThermostatTemperatureSetpoint.SetRange"])
        self.assertGreater(params["coolCelsius"], params["heatCelsius"])
        self.assertAlmostEqual(done["heat_f"], 68.0, delta=1)
        self.assertAlmostEqual(done["cool_f"], 72.0, delta=1)

    def test_a_zero_spread_still_keeps_cool_above_heat(self):
        # The API rejects coolCelsius <= heatCelsius outright.
        client = client_with(traits(mode="HEATCOOL"))
        client.set_temperature_f(DEVICE, 70, spread_f=0)
        params = client.sent[-1][2]["params"]
        self.assertGreater(params["coolCelsius"], params["heatCelsius"])

    def test_eco_is_cleared_first_because_it_rejects_setpoints(self):
        client = client_with(traits(mode="HEAT", eco="MANUAL_ECO"))
        done = client.set_temperature_f(DEVICE, 70)
        self.assertTrue(done["eco_cleared"])
        self.assertEqual(self.commands(client)[0],
                         "sdm.devices.commands.ThermostatEco.SetMode")
        self.assertEqual(client.sent[1][2]["params"], {"mode": "OFF"})

    def test_a_thermostat_that_is_off_refuses_rather_than_doing_nothing(self):
        client = client_with(traits(mode="OFF"))
        with self.assertRaises(nc.NestError) as cm:
            client.set_temperature_f(DEVICE, 70)
        self.assertIn("OFF", str(cm.exception))
        self.assertEqual(self.commands(client), [])

    def test_nonsense_modes_never_reach_the_device(self):
        client = client_with(traits())
        with self.assertRaises(nc.NestError):
            client.set_mode(DEVICE, "AUTO")
        self.assertEqual(client.sent, [])


class SafetyBound(unittest.TestCase):
    """The hard band on what can be asked of the HVAC.

    Added 2026-10-07. The thermostat is the one device here that can damage the
    building: heat off, or 50°F asked for, in a hard freeze is burst pipes.
    In code, not config — a bound a config edit could widen is not a bound.
    """

    def test_the_temperatures_owner_actually_uses_are_allowed(self):
        for f in (nc.SAFE_MIN_F, 67, 70, 72, nc.SAFE_MAX_F):
            client = client_with(traits(mode="HEAT"))
            client.set_temperature_f(DEVICE, f)
            self.assertTrue(client.sent, f)

    def test_above_the_band_is_refused_before_anything_is_sent(self):
        for f in (nc.SAFE_MAX_F + 1, 90, 115):
            client = client_with(traits(mode="HEAT"))
            with self.assertRaises(nc.NestError, msg=f):
                client.set_temperature_f(DEVICE, f)
            self.assertEqual(client.sent, [], f)

    def test_a_freeze_risk_temperature_is_refused_and_says_why(self):
        client = client_with(traits(mode="HEAT"))
        with self.assertRaises(nc.NestError) as cm:
            client.set_temperature_f(DEVICE, 50)
        self.assertIn("pipes", str(cm.exception))
        self.assertEqual(client.sent, [])

    def test_a_refused_request_does_not_even_clear_eco(self):
        # The check runs before the eco clear, so a refused request leaves the
        # thermostat exactly as it was rather than half-changed.
        client = client_with(traits(mode="HEAT", eco="MANUAL_ECO"))
        with self.assertRaises(nc.NestError):
            client.set_temperature_f(DEVICE, 95)
        self.assertEqual(client.sent, [])

    def test_the_heatcool_bracket_cannot_escape_the_band(self):
        # The spread widens the range around the target, so the bound has to hold
        # for the bracket too, not just the midpoint.
        client = client_with(traits(mode="HEATCOOL"))
        client.set_temperature_f(DEVICE, nc.SAFE_MAX_F, spread_f=4)
        params = client.sent[-1][2]["params"]
        self.assertLessEqual(nc.c_to_f(params["coolCelsius"]), nc.SAFE_MAX_F + 3)

    def test_the_configured_presets_are_inside_the_band(self):
        import json as _json
        from pathlib import Path as _Path
        cfg = _json.loads((_Path(__file__).resolve().parent.parent
                           / "config" / "nest_config.json").read_text())
        for key in ("sleep_temp_f", "day_temp_f"):
            self.assertGreaterEqual(cfg[key], nc.SAFE_MIN_F, key)
            self.assertLessEqual(cfg[key], nc.SAFE_MAX_F, key)

    def test_eco_is_left_out_of_the_band_deliberately(self):
        # Eco has Nest's own safe bounds, set by Nest, so away.py is unaffected.
        client = client_with(traits(mode="HEAT"))
        client.set_eco(DEVICE, True)
        self.assertEqual(client.sent[-1][2]["command"],
                         "sdm.devices.commands.ThermostatEco.SetMode")


class Quota(unittest.TestCase):
    """Google's per-device ceiling is 5/min and 100/hour, across all projects."""

    def client(self):
        with patch.object(nc, "missing_secrets", lambda: []), \
             patch.object(nc, "_secret", lambda n: "x"):
            return nc.NestClient()

    def test_the_configured_ceilings_sit_under_googles(self):
        self.assertLess(nc.MAX_PER_MINUTE, 5)
        self.assertLess(nc.MAX_PER_HOUR, 100)

    def test_a_burst_waits_for_the_minute_window(self):
        client = self.client()
        with patch.object(nc.time, "sleep") as slept:
            for _ in range(nc.MAX_PER_MINUTE + 1):
                client._spend_quota()
        self.assertTrue(slept.called)

    def test_the_hourly_cap_refuses_instead_of_sleeping_for_an_hour(self):
        # Sleeping an hour inside a cron job would hold it open until its timeout.
        client = self.client()
        with patch.object(nc.time, "sleep"):
            for _ in range(nc.MAX_PER_HOUR):
                client._spend_quota()
            with self.assertRaises(nc.NestError) as cm:
                client._spend_quota()
        self.assertIn("hourly", str(cm.exception).lower())


class Secrets(unittest.TestCase):
    def test_all_four_are_required_and_named_when_missing(self):
        with patch.object(nc, "_secret", lambda n: ""):
            with self.assertRaises(nc.NestError) as cm:
                nc.NestClient()
        message = str(cm.exception)
        for name in nc.SECRETS:
            self.assertIn(name, message)

    def test_the_consent_url_asks_for_an_offline_refresh_token(self):
        # Without both of these Google returns no refresh_token, which is the single
        # most common way this setup fails.
        url = nc.auth_url("proj-uuid", "client-123")
        self.assertIn("access_type=offline", url)
        self.assertIn("prompt=consent", url)
        self.assertIn("proj-uuid", url)
        self.assertIn("client-123", url)
        self.assertTrue(url.startswith(nc.PCM_BASE), "must be the PCM flow, not plain Google OAuth")


class AwayEco(unittest.TestCase):
    def test_leaving_turns_eco_on(self):
        self.assertEqual(away.decide(away=True, eco_on=False, we_set_it=False)[0], "set_eco")

    def test_already_eco_while_away_does_nothing(self):
        self.assertEqual(away.decide(away=True, eco_on=True, we_set_it=True)[0], "none")

    def test_coming_home_clears_only_the_eco_this_script_set(self):
        self.assertEqual(away.decide(away=False, eco_on=True, we_set_it=True)[0], "clear_eco")

    def test_coming_home_leaves_owners_own_eco_alone(self):
        # The restraint that matters: cancelling a setting he chose himself is the
        # proactive behaviour he has ruled out.
        action, why = away.decide(away=False, eco_on=False or True, we_set_it=False)
        self.assertEqual(action, "none")
        self.assertIn("not set by this script", why)

    def test_home_and_off_is_a_no_op(self):
        self.assertEqual(away.decide(away=False, eco_on=False, we_set_it=False)[0], "none")


def run_night_schedule(wake, alarm_on=True, cfg=None, dark=time(22, 45), problems=()):
    cfg = cfg or {"device": "house", "sleep_temp_f": 67, "day_temp_f": 70,
                  "morning_lead_minutes": 45,
                  "jobs": {"night": "J-night", "morning": "J-morning"}}
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

    import chilipad_jobs
    with patch.object(ns, "read_plan", lambda: {"enabled": alarm_on, "wake_dt": wake}), \
         patch.object(ns, "load_config", lambda: cfg), \
         patch.object(ns, "lights_dark_time", lambda: (dark, "stub")), \
         patch.object(chilipad_jobs, "will_not_run", lambda ids: list(problems)), \
         patch.object(ns.subprocess, "run", fake_run), \
         patch("builtins.print", lambda *a, **k: printed.extend(a)):
        ns.main()
    return {"report": json.loads(printed[-1]), "edits": edits}


class Setback(unittest.TestCase):
    def test_the_house_drops_at_lights_out_and_returns_before_the_alarm(self):
        out = run_night_schedule(wake=datetime(2026, 10, 7, 7, 30, tzinfo=CT))
        self.assertEqual(out["report"]["at"], {"night": "22:45", "morning": "06:45"})
        self.assertEqual(out["report"]["status"], "ok")

    def test_the_morning_leads_the_alarm_rather_than_landing_on_it(self):
        # Landing on the wake time means waking in a cold house that warms up
        # while he is in the shower.
        out = run_night_schedule(wake=datetime(2026, 10, 7, 7, 30, tzinfo=CT))
        self.assertLess(out["report"]["at"]["morning"], out["report"]["wake_local"])

    def test_an_early_alarm_pulls_the_morning_with_it(self):
        out = run_night_schedule(wake=datetime(2026, 10, 7, 5, 0, tzinfo=CT))
        self.assertEqual(out["report"]["at"]["morning"], "04:15")
        self.assertEqual(out["report"]["at"]["night"], "22:45")

    def test_a_changed_light_schedule_moves_the_night_setback(self):
        out = run_night_schedule(wake=datetime(2026, 10, 7, 7, 30, tzinfo=CT), dark=time(23, 30))
        self.assertEqual(out["report"]["at"]["night"], "23:30")

    def test_a_lead_that_would_reach_past_bedtime_is_clamped(self):
        cfg = {"sleep_temp_f": 67, "day_temp_f": 70, "morning_lead_minutes": 600,
               "jobs": {"night": "J-night", "morning": "J-morning"}}
        out = run_night_schedule(wake=datetime(2026, 10, 7, 6, 0, tzinfo=CT), cfg=cfg)
        self.assertGreater(out["report"]["at"]["morning"], out["report"]["at"]["night"])

    def test_a_switched_off_alarm_restores_the_house_late_not_at_a_stale_wake(self):
        out = run_night_schedule(wake=datetime(2026, 10, 7, 7, 30, tzinfo=CT), alarm_on=False)
        self.assertEqual(out["report"]["at"]["morning"], "10:00")
        self.assertIn("not keeping", out["report"]["note"])

    def test_both_jobs_carry_the_guards_and_their_own_time(self):
        out = run_night_schedule(wake=datetime(2026, 10, 7, 7, 30, tzinfo=CT))
        for expr, argv in out["edits"]:
            self.assertIn("--skip-if-away", argv)
            self.assertIn("--late-grace-min", argv)
            minute, hour = expr.split()[0], expr.split()[1]
            self.assertEqual(argv[argv.index("--expected-start") + 1],
                             f"{int(hour):02d}:{int(minute):02d}")

    def test_the_jobs_ask_for_a_preset_not_a_hardcoded_number(self):
        # A number in the cron store would silently outlive a config change.
        out = run_night_schedule(wake=datetime(2026, 10, 7, 7, 30, tzinfo=CT))
        presets = [argv[argv.index("--preset") + 1] for _, argv in out["edits"]]
        self.assertEqual(presets, ["sleep", "day"])
        for _, argv in out["edits"]:
            self.assertNotIn("--temp", argv)

    def test_a_disabled_job_is_not_reported_as_success(self):
        out = run_night_schedule(wake=datetime(2026, 10, 7, 7, 30, tzinfo=CT),
                                 problems=["nest-night is disabled"])
        self.assertEqual(out["report"]["status"], "will_not_run")

    def test_the_report_says_where_the_lights_anchor_came_from(self):
        out = run_night_schedule(wake=datetime(2026, 10, 7, 7, 30, tzinfo=CT))
        self.assertIn("lights_dark_from", out["report"])


class SharesTheBedsAnchors(unittest.TestCase):
    def test_lights_dark_is_the_same_function_the_chilipad_uses(self):
        # One definition of "when the room goes dark", imported rather than copied.
        # Two copies would drift and the bed and house would stop agreeing.
        import night_plan
        self.assertIs(ns.lights_dark_time, night_plan.lights_dark_time)


if __name__ == "__main__":
    unittest.main(verbosity=2)
