#!/usr/bin/env python3
"""
Regression tests for detect_trips_from_calendar.py's flight-leg direction
parsing and outbound/return pairing.

Run manually after touching parse_flight_leg / match_flight_leg / the pairing
logic in main():

    python3 test_detect_trips.py

No pytest dependency -- plain unittest, stdlib only. Imports the module by
file path since it isn't packaged.

Background: on 2026-08-26 the LA trip's return leg ("WN 202 LAX -> DAL")
matched the generic keyword+home-metro-exclusion path, which has no concept
of flight direction. It got stored as depart_date with return_date null,
while the outbound leg ("WN 201 DAL -> LAX", venue "Dallas Love Field") was
dropped entirely because "Dallas" appeared in its own location text. The
agent then asked the user for a departure date the calendar already had. This
file exists so a future edit to the direction/pairing logic can't silently
reintroduce that.
"""
import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import unittest

_MODULE_PATH = Path(__file__).parent / "detect_trips_from_calendar.py"
_spec = importlib.util.spec_from_file_location("detect_trips_from_calendar", _MODULE_PATH)
dt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dt)

ROUTES_BY_ID = {
    "la": {"id": "la", "label": "LA (Southern California)", "destinations": ["LAX", "BUR", "LGB"]},
}
HOME_AIRPORTS = {"DAL"}


class TestParseFlightLeg(unittest.TestCase):
    def test_extracts_origin_and_dest(self):
        self.assertEqual(dt.parse_flight_leg("WN 201 DAL → LAX <> the user @ Dallas Love Field"), ("DAL", "LAX"))
        self.assertEqual(dt.parse_flight_leg("WN 202 LAX → DAL <> the user @ Los Angeles International Airport"), ("LAX", "DAL"))

    def test_ascii_arrow_variant(self):
        self.assertEqual(dt.parse_flight_leg("WN 201 DAL -> LAX <> the user @ Dallas Love Field"), ("DAL", "LAX"))

    def test_no_match_on_plain_event(self):
        self.assertIsNone(dt.parse_flight_leg("Weekend in LA"))


class TestMatchFlightLeg(unittest.TestCase):
    def test_home_to_dest_is_outbound(self):
        route_id, direction = dt.match_flight_leg("DAL", "LAX", HOME_AIRPORTS, ROUTES_BY_ID)
        self.assertEqual((route_id, direction), ("la", "outbound"))

    def test_dest_to_home_is_return_not_outbound(self):
        # This is the exact bug: the return leg's venue/location text contains
        # no home-metro terms, so the old keyword path would happily match it
        # as a fresh candidate and store its date as depart_date.
        route_id, direction = dt.match_flight_leg("LAX", "DAL", HOME_AIRPORTS, ROUTES_BY_ID)
        self.assertEqual((route_id, direction), ("la", "return"))

    def test_unrelated_route_no_match(self):
        route_id, direction = dt.match_flight_leg("DAL", "ORD", HOME_AIRPORTS, ROUTES_BY_ID)
        self.assertEqual((route_id, direction), (None, None))


class TestIsTooLate(unittest.TestCase):
    """Fixed 2026-08-26: a leg departing today is already flying (or flown)
    by the time the daily cron runs -- no window left to rebook on a price
    drop, so it shouldn't be proposed. See feedback-flight-leg-direction-bug
    memory for the incident this guard was added for."""

    def test_today_is_too_late(self):
        today = dt.date.today().isoformat()
        self.assertTrue(dt.is_too_late(today))

    def test_past_is_too_late(self):
        self.assertTrue(dt.is_too_late("2000-01-01"))

    def test_future_is_not_too_late(self):
        self.assertFalse(dt.is_too_late("2099-01-01"))


class TestOutboundExcludedByHomeMetroTerms(unittest.TestCase):
    """The outbound leg's venue ('Dallas Love Field') contains a home-metro
    term, so the generic match_route() path (correctly) excludes it -- proof
    that direction-aware parsing must run BEFORE that path, not alongside it."""

    def test_generic_matcher_drops_outbound_leg(self):
        text = "WN 201 DAL → LAX <> the user @ Dallas Love Field Dallas Love Field"
        self.assertIsNone(dt.match_route(text))


if __name__ == "__main__":
    unittest.main()
