#!/usr/bin/env python3
"""
Tests for compute_leave_time.py — the airport leave-time calculator.

Covers the five scenarios from the Airport Departure Timing spec plus the
adjustment-rule and fail-soft edges. Pure function, no mocks, no network.

    python3 test_compute_leave_time.py
"""
import importlib.util
import unittest
from datetime import datetime, timedelta
from pathlib import Path

_MOD = Path(__file__).parent / "compute_leave_time.py"
_spec = importlib.util.spec_from_file_location("compute_leave_time", _MOD)
clt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(clt)

compute_leave_time = clt.compute_leave_time


def _mins_before(result_iso: str, departure_iso: str) -> int:
    r = datetime.fromisoformat(result_iso)
    d = datetime.fromisoformat(departure_iso)
    return int((d - r).total_seconds() // 60)


BASE = {
    "scheduled_departure": "2026-09-15T13:40:00-05:00",
    "airport": "DAL",
    "terminal": "MAIN",
    "buffer_minutes": 20,
    "gate_close_lead_minutes": 15,
    "walk_minutes": 10,
    "security_p85": 12,
    "security_source": "live",
    "drive_minutes": 22,
    "drive_source": "live",
    "parking_minutes": 0,
    "weather_source": "live",
    "stage": "final",
}


class NormalDomestic(unittest.TestCase):
    """Case 1: carry-on, midday, all sources live."""

    def setUp(self):
        self.r = compute_leave_time(dict(BASE))

    def test_leave_time_is_sum_of_terms(self):
        # gate_close 13:25, minus walk10 + sec12 + buffer20 = arrive 12:43,
        # minus drive22 + parking0 = leave 12:21  -> 79 min before departure
        self.assertEqual(self.r["leave_home_display"], "12:21 PM")
        self.assertEqual(_mins_before(self.r["leave_home"], BASE["scheduled_departure"]), 79)

    def test_no_estimates_or_adjustments(self):
        self.assertEqual(self.r["estimated_terms"], [])
        self.assertEqual(self.r["adjustments_applied"], [])
        self.assertFalse(self.r["checked_bag_floor_hit"])

    def test_confidence_normal(self):
        self.assertEqual(self.r["confidence"], "normal")

    def test_well_under_old_fixed_buffer(self):
        # Old logic: 90 (precheck) + 22 (drive) + 15 (slack) = 127 min before.
        self.assertLess(_mins_before(self.r["leave_home"], BASE["scheduled_departure"]), 127)


class TsaSourceDown(unittest.TestCase):
    """Case 2: same flight, live TSA returns nothing -> static fallback, said so."""

    def setUp(self):
        d = dict(BASE)
        d["security_p85"] = 18          # caller-supplied static fallback for DAL
        d["security_source"] = "static"
        self.r = compute_leave_time(d)

    def test_falls_back_and_still_computes(self):
        self.assertEqual(self.r["breakdown"]["security_p85"], 18)
        self.assertTrue(self.r["leave_home_display"])

    def test_security_flagged_estimated(self):
        self.assertIn("security", self.r["estimated_terms"])
        self.assertTrue(any("Security wait is a static estimate" in c for c in self.r["caveats"]))

    def test_missing_p85_entirely_is_soft(self):
        d = dict(BASE)
        d["security_p85"] = None
        r = compute_leave_time(d)
        self.assertEqual(r["breakdown"]["security_p85"], 0)
        self.assertIn("security", r["estimated_terms"])


class CheckedBagFloor(unittest.TestCase):
    """Case 3a: checked bag, midday -> base calc lands inside 60 min, floor binds."""

    def setUp(self):
        d = dict(BASE)            # midday; gate_close - (10+12+20) = 57 min before dep
        d["checked_bag"] = True
        self.dep = d["scheduled_departure"]
        self.r = compute_leave_time(d)

    def test_bag_floor_hit(self):
        self.assertTrue(self.r["checked_bag_floor_hit"])
        # arrive_terminal is pulled out to exactly departure - 60
        self.assertEqual(_mins_before(self.r["arrive_terminal"], self.dep), 60)

    def test_leave_accounts_for_drive_below_the_floor(self):
        # leave = (dep - 60) - drive 22 = 82 min before departure
        self.assertEqual(_mins_before(self.r["leave_home"], self.dep), 82)

    def test_no_floor_when_carryon(self):
        r = compute_leave_time(dict(BASE))
        self.assertFalse(r["checked_bag_floor_hit"])

    def test_floor_does_not_bind_when_calc_already_earlier(self):
        # A big adjustment (early morning) already puts arrival >60 min out.
        d = dict(BASE)
        d["scheduled_departure"] = "2026-09-15T07:00:00-05:00"
        d["checked_bag"] = True
        r = compute_leave_time(d)
        self.assertFalse(r["checked_bag_floor_hit"])


class EarlyMorning(unittest.TestCase):
    """Case 3b: 7am departure -> +10 min PreCheck-opens-late adjustment."""

    def test_morning_adjustment_applied(self):
        d = dict(BASE)
        d["scheduled_departure"] = "2026-09-15T07:00:00-05:00"
        r = compute_leave_time(d)
        self.assertIn("early_morning", r["adjustments_applied"])

    def test_no_adjustment_at_nine_am(self):
        d = dict(BASE)
        d["scheduled_departure"] = "2026-09-15T09:00:00-05:00"
        r = compute_leave_time(d)
        self.assertEqual(r["adjustments_applied"], [])


class InboundDelayed(unittest.TestCase):
    """Case 4: inbound aircraft 90 min late -> leave time slides, explained."""

    def setUp(self):
        d = dict(BASE)
        d["effective_departure"] = "2026-09-15T15:10:00-05:00"   # +90 min
        d["inbound_delay_minutes"] = 90
        self.r = compute_leave_time(d)
        self.baseline = compute_leave_time(dict(BASE))

    def test_leave_time_pushed_by_the_delay(self):
        shifted = datetime.fromisoformat(self.r["leave_home"])
        original = datetime.fromisoformat(self.baseline["leave_home"])
        self.assertEqual(shifted - original, timedelta(minutes=90))

    def test_delay_is_explained_first(self):
        self.assertIn("Inbound aircraft delayed", self.r["notes"][0])
        self.assertIn("3:10 PM", self.r["notes"][0])

    def test_infers_delay_minutes_when_not_passed(self):
        d = dict(BASE)
        d["effective_departure"] = "2026-09-15T15:10:00-05:00"
        r = compute_leave_time(d)
        self.assertIn("~90 min", r["notes"][0])


class UnlearnedAirport(unittest.TestCase):
    """Case 5: new airport, no profile -> conservative defaults, flagged."""

    def setUp(self):
        d = dict(BASE)
        d["airport"] = "PDX"
        d["airport_known"] = False
        d["terminal"] = ""
        d["walk_minutes"] = None
        d["security_p85"] = 20
        d["security_source"] = "static"
        d["drive_minutes"] = None
        self.r = compute_leave_time(d)

    def test_conservative_walk_and_drive_defaults(self):
        self.assertEqual(self.r["breakdown"]["walk"], clt.CONSERVATIVE_WALK_MIN)
        self.assertEqual(self.r["breakdown"]["drive"], clt.CONSERVATIVE_DRIVE_MIN)

    def test_low_confidence_and_unlearned_note(self):
        self.assertEqual(self.r["confidence"], "low")
        self.assertIn("no saved profile", self.r["notes"][0])

    def test_estimated_terms_listed(self):
        for term in ("walk", "drive", "security"):
            self.assertIn(term, self.r["estimated_terms"])


class AdjustmentRules(unittest.TestCase):
    def test_international_adds_30(self):
        d = dict(BASE)
        d["international"] = True
        r = compute_leave_time(d)
        base = compute_leave_time(dict(BASE))
        self.assertEqual(
            datetime.fromisoformat(base["leave_home"]) - datetime.fromisoformat(r["leave_home"]),
            timedelta(minutes=30),
        )

    def test_weather_adds_15(self):
        d = dict(BASE)
        d["precip_or_freezing"] = True
        r = compute_leave_time(d)
        base = compute_leave_time(dict(BASE))
        self.assertEqual(
            datetime.fromisoformat(base["leave_home"]) - datetime.fromisoformat(r["leave_home"]),
            timedelta(minutes=15),
        )

    def test_weather_unavailable_is_flagged_not_buffered(self):
        d = dict(BASE)
        d["weather_source"] = "unavailable"
        r = compute_leave_time(d)
        self.assertIn("weather", r["estimated_terms"])
        self.assertEqual(r["adjustments_applied"], [])

    def test_parking_pushes_leave_earlier(self):
        d = dict(BASE)
        d["parking_minutes"] = 18
        r = compute_leave_time(d)
        base = compute_leave_time(dict(BASE))
        self.assertEqual(
            datetime.fromisoformat(base["leave_home"]) - datetime.fromisoformat(r["leave_home"]),
            timedelta(minutes=18),
        )

    def test_buffer_is_the_only_knob(self):
        d = dict(BASE)
        d["buffer_minutes"] = 10
        r = compute_leave_time(d)
        base = compute_leave_time(dict(BASE))
        # smaller buffer -> later leave time
        self.assertEqual(
            datetime.fromisoformat(r["leave_home"]) - datetime.fromisoformat(base["leave_home"]),
            timedelta(minutes=10),
        )


class Contract(unittest.TestCase):
    def test_rough_stage_is_low_confidence_and_labelled(self):
        d = dict(BASE)
        d["stage"] = "rough"
        r = compute_leave_time(d)
        self.assertEqual(r["confidence"], "low")
        self.assertIn("Rough estimate", r["notes"][0])

    def test_naive_departure_rejected(self):
        d = dict(BASE)
        d["scheduled_departure"] = "2026-09-15T13:40:00"
        with self.assertRaises(ValueError):
            compute_leave_time(d)

    def test_one_line_reason_mentions_estimates(self):
        d = dict(BASE)
        d["security_source"] = "static"
        r = compute_leave_time(d)
        self.assertIn("estimated: security", clt.one_line_reason(r))


if __name__ == "__main__":
    unittest.main(verbosity=2)
