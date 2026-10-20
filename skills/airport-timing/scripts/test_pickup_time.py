#!/usr/bin/env python3
"""Tests for pickup_time.py. No network: config, origin and traffic are patched."""
import shlex
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import pickup_time as pt  # noqa: E402

CFG = {
    "fallback_drive_minutes": {"DAL": 20, "LAX": 25},
    "default_walk_minutes": 12,
    "airports": {
        "DAL": {"lat": 32.8481, "lng": -96.8512, "name": "Dallas Love Field",
                "address": "8008 Herb Kelleher Way, Dallas, TX 75235", "tz": "America/Chicago",
                "terminals": {"MAIN": {"walk_minutes": 10}}, "pickup_note": "Wait in the Cell Phone Lot."},
        "LAX": {"lat": 33.94, "lng": -118.41, "name": "Los Angeles International Airport",
                "address": "1 World Way, Los Angeles, CA 90045", "tz": "America/Los_Angeles",
                "terminals": {"1": {"walk_minutes": 14}, "4": {"walk_minutes": 16}}},
    },
}


def run(drive=18, back=18, **kw):
    calls = []

    def fake_drive(*args, depart_at=None):
        calls.append(depart_at)
        return drive if len(calls) == 1 else back

    args = dict(flight="DL100", airport_code="DAL", arrival="2026-09-22 14:05", checked_bag=True)
    args.update(kw)
    with patch.object(pt.drive_time, "_travel_cfg", return_value=CFG), \
         patch.object(pt.drive_time, "origin_coords_and_label", return_value=(40.0000, -75.0000, "Home")), \
         patch.object(pt.drive_time, "drive_minutes", side_effect=fake_drive):
        return pt.plan(**args), calls


class TestPickupTime(unittest.TestCase):
    def test_dl512_with_checked_bag(self):
        # 2026-09-21: lands 2:05, bag on the carousel by 2:25, out by 2:30.
        r, _ = run()
        self.assertEqual((r["out_at_curb"], r["be_at_airport"], r["leave_by"]), ("2:30 PM", "2:25 PM", "2:05 PM"))
        self.assertEqual(r["event"]["reminder_minutes"], 20)  # 2:07 rounded down to 2:05
        self.assertEqual((r["event"]["from"], r["event"]["to"]), ("2026-09-22T14:25:00-05:00", "2026-09-22T14:45:00-05:00"))
        self.assertEqual(r["back_by"], "3:03 PM")
        self.assertEqual(r["estimated"], [])

    def test_carry_on_only_is_deplane_plus_walk(self):
        r, _ = run(checked_bag=False)
        self.assertEqual(r["out_at_curb"], "2:25 PM")
        self.assertIn("carry-on only", r["event"]["description"])

    def test_traffic_is_predicted_for_the_real_departure_time(self):
        _, calls = run()
        self.assertIsNotNone(calls[0].tzinfo)
        self.assertEqual(calls[0].isoformat(), "2026-09-22T14:05:00-05:00")  # 2:25 minus 20-min fallback guess

    def test_no_traffic_data_falls_back_and_says_so(self):
        r, _ = run(drive=None, back=None)
        self.assertEqual(r["drive_minutes"], 20)
        self.assertEqual(r["estimated"], ["drive to airport", "drive back"])

    def test_naive_arrival_is_the_arrival_airports_local_time(self):
        r, _ = run(airport_code="LAX", arrival="2026-09-22 14:05", checked_bag=False)
        self.assertTrue(r["event"]["from"].endswith("-07:00"))
        self.assertEqual(r["out_at_curb"], "2:27 PM")  # multi-terminal: deplane 10 + default walk 12

    def test_offset_arrival_is_converted_to_airport_time(self):
        r, _ = run(arrival="2026-09-22T15:05:00-04:00")
        self.assertEqual(r["lands"], "2:05 PM")

    def test_propose_command_is_a_valid_proposal(self):
        r, _ = run(passenger="Sam")
        argv = shlex.split(r["propose_command"])
        self.assertEqual(argv[-1], "--propose")
        self.assertEqual(argv[argv.index("--reminder-minutes") + 1], "20")
        self.assertEqual(argv[argv.index("--tz") + 1], "America/Chicago")
        self.assertEqual(argv[argv.index("--title") + 1], "Sam Pickup <> the user @ Dallas Love Field")
        self.assertIn("Wait in the Cell Phone Lot.", argv[argv.index("--description") + 1])

    def test_unknown_airport_stops(self):
        with self.assertRaises(SystemExit):
            run(airport_code="XYZ")


if __name__ == "__main__":
    unittest.main(verbosity=2)
