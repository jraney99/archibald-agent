#!/usr/bin/env python3
"""
Integration tests for detect_trips_from_calendar.main() covering the
2026-08-29 failure:

  - The real DAL→LGA / LGA→DAL flight legs were filed "no_match" back when
    the nyc route only listed JFK, and that verdict was frozen forever.
  - So a "Dinner - Example Trattoria" event whose street address contained
    "New York, NY" got proposed as the NYC trip instead — wrong date, no
    return leg.

Run: python3 test_detect_trips_travel.py
"""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

_MODULE_PATH = Path(__file__).parent / "detect_trips_from_calendar.py"
_spec = importlib.util.spec_from_file_location("detect_trips_from_calendar", _MODULE_PATH)
dt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dt)

FUTURE_Y = dt.date.today().year + 1

OUTBOUND = {
    "id": "leg-out", "summary": "WN 101 DAL → LGA <> the user @ Dallas Love Field",
    "location": "Dallas Love Field",
    "start": {"dateTime": f"{FUTURE_Y}-09-01T06:25:00-05:00"},
    "end": {"dateTime": f"{FUTURE_Y}-09-01T10:40:00-05:00"},
}
RETURN = {
    "id": "leg-back", "summary": "WN 102 LGA → DAL <> the user @ LaGuardia Airport",
    "location": "LaGuardia Airport",
    "start": {"dateTime": f"{FUTURE_Y}-09-08T08:45:00-04:00"},
    "end": {"dateTime": f"{FUTURE_Y}-09-08T11:40:00-04:00"},
}
DINNER = {
    "id": "dinner-nyc", "summary": "Dinner - Example Trattoria <> the user @ Example Trattoria",
    "location": "100 Example St, New York, NY 10018",
    "start": {"dateTime": f"{FUTURE_Y}-09-02T18:00:00-04:00"},
    "end": {"dateTime": f"{FUTURE_Y}-09-02T20:30:00-04:00"},
}

WATCHLIST = {
    "home_airports": ["DAL"],
    "opportunistic_routes": [
        {"id": "nyc", "label": "New York", "origin": "DAL",
         "destinations": ["LGA", "JFK", "EWR"], "max_price_oneway": 150, "enabled": True},
    ],
    "trips": [],
}


class DetectTripsIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.seen = self.tmp / "seen.json"
        self.watch = self.tmp / "watchlist.json"
        self.watch.write_text(json.dumps(WATCHLIST))
        self.sent = []

        self._orig = (dt.SEEN_EVENTS_FILE, dt.WATCHLIST_FILE, dt.fetch_events, dt.send_telegram)
        dt.SEEN_EVENTS_FILE = self.seen
        dt.WATCHLIST_FILE = self.watch
        dt.send_telegram = lambda text, dry_run: self.sent.append(text)

    def tearDown(self):
        dt.SEEN_EVENTS_FILE, dt.WATCHLIST_FILE, dt.fetch_events, dt.send_telegram = self._orig

    def run_main(self, events):
        dt.fetch_events = lambda: events
        sys.argv = ["detect_trips_from_calendar.py"]
        dt.main()
        return json.loads(self.watch.read_text()), json.loads(self.seen.read_text())

    def test_flight_legs_pair_into_one_candidate_dinner_suppressed(self):
        watch, seen = self.run_main([OUTBOUND, RETURN, DINNER])
        trips = watch["trips"]
        self.assertEqual(len(trips), 1, trips)
        t = trips[0]
        self.assertEqual(t["destination"], "LGA")
        self.assertEqual(t["depart_date"], f"{FUTURE_Y}-09-01")
        self.assertEqual(t["return_date"], f"{FUTURE_Y}-09-08")
        self.assertEqual(seen["dinner-nyc"]["status"], "covered_by_flight")
        self.assertEqual(sum("Looks like a trip" in m for m in self.sent), 1)

    def test_frozen_no_match_is_re_evaluated_after_route_gains_airport(self):
        # Simulate the real history: both legs filed "no_match" under the old
        # (JFK-only) config, dinner still unseen.
        self.seen.write_text(json.dumps({
            "leg-out": {"status": "no_match"},
            "leg-back": {"status": "no_match"},
        }))
        watch, seen = self.run_main([OUTBOUND, RETURN, DINNER])
        self.assertEqual(len(watch["trips"]), 1)
        self.assertEqual(watch["trips"][0]["destination"], "LGA")

    def test_declined_candidate_never_re_pings(self):
        # candidate_sent is sticky even though the trip was removed from watchlist.
        self.seen.write_text(json.dumps({"dinner-nyc": {"status": "candidate_sent", "trip_id": "x"}}))
        watch, seen = self.run_main([DINNER])
        self.assertEqual(watch["trips"], [])
        self.assertEqual(self.sent, [])

    def test_dinner_alone_still_proposes_when_no_flight(self):
        watch, seen = self.run_main([DINNER])
        self.assertEqual(len(watch["trips"]), 1)
        self.assertEqual(watch["trips"][0]["depart_date"], f"{FUTURE_Y}-09-02")

    def test_second_run_is_idempotent_no_repeat_ping(self):
        # First run: pairs the legs, suppresses the dinner, one ping.
        self.run_main([OUTBOUND, RETURN, DINNER])
        self.sent.clear()
        # Second run with the exact same calendar: nothing new, no ping,
        # still exactly one trip. Provisional verdicts (dinner) get re-derived
        # but must land back on covered_by_flight because the (still
        # unconfirmed) candidate keeps the window covered.
        watch, seen = self.run_main([OUTBOUND, RETURN, DINNER])
        self.assertEqual(len(watch["trips"]), 1)
        self.assertEqual(self.sent, [])
        self.assertEqual(seen["dinner-nyc"]["status"], "covered_by_flight")

    def test_dinner_covered_by_preexisting_watching_trip(self):
        WATCHLIST_W = json.loads(json.dumps(WATCHLIST))
        WATCHLIST_W["trips"].append({
            "id": "manual1", "label": "NYC", "origin": "DAL", "destination": "LGA",
            "depart_date": f"{FUTURE_Y}-09-01", "return_date": f"{FUTURE_Y}-09-08",
            "status": "watching", "source": "manual",
        })
        self.watch.write_text(json.dumps(WATCHLIST_W))
        watch, seen = self.run_main([DINNER])
        self.assertEqual(len(watch["trips"]), 1)  # just the manual one, no new candidate
        self.assertEqual(seen["dinner-nyc"]["status"], "covered_by_flight")
        self.assertEqual(self.sent, [])


class RouteOfAirport(unittest.TestCase):
    def test_maps_airport_to_route(self):
        rb = {r["id"]: r for r in WATCHLIST["opportunistic_routes"]}
        self.assertEqual(dt.route_of_airport("LGA", rb), "nyc")
        self.assertIsNone(dt.route_of_airport("ORD", rb))


if __name__ == "__main__":
    unittest.main()
