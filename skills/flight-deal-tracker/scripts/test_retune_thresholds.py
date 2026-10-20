#!/usr/bin/env python3
"""Tests for retune_thresholds.py. Run: python3 test_retune_thresholds.py"""
import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

_MOD = Path(__file__).parent / "retune_thresholds.py"
_spec = importlib.util.spec_from_file_location("retune_thresholds", _MOD)
rt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rt)


def _history(route_id, day_price_pairs):
    """day_price_pairs: list of (days_ago, [prices]) → history entries."""
    entries = []
    for days_ago, prices in day_price_pairs:
        dc = (date.today() - timedelta(days=days_ago)).isoformat()
        for p in prices:
            entries.append({"date_checked": dc, "kind": "opportunistic", "route_id": route_id,
                            "trip_type": "roundtrip", "price": p, "return_date": "x"})
    return entries


class Percentile(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(rt.percentile([10, 20, 30, 40, 50], 50), 30)
        self.assertEqual(rt.percentile([100], 25), 100)
        self.assertAlmostEqual(rt.percentile([0, 100], 25), 25)


class SuggestForRoute(unittest.TestCase):
    def _route(self, **o):
        r = {"id": "nyc", "label": "New York", "max_price_roundtrip": 360}
        r.update(o)
        return r

    def test_holds_when_too_few_days(self):
        hist = _history("nyc", [(i, [300, 320, 340]) for i in range(1, 6)])  # 5 days
        out = rt.suggest_for_route(self._route(), hist, date.today())
        self.assertIsNone(out["suggested"])
        self.assertIn("5 days", out["reason"])

    def test_suggests_p25_with_enough_days(self):
        # 12 days, prices 300..410 spread → p25 ~ 320s, well within ±35% of 360
        hist = _history("nyc", [(i, [300, 330, 360, 390, 410]) for i in range(1, 13)])
        out = rt.suggest_for_route(self._route(), hist, date.today())
        self.assertIsNotNone(out["suggested"])
        self.assertLess(out["suggested"], 360)
        self.assertGreaterEqual(out["suggested"], 300 * 0.85)
        self.assertEqual(out["suggested"] % rt.ROUND_TO, 0)

    def test_max_step_pct_caps_a_big_drop(self):
        # true p25 would be ~$120 but current is $360 → capped at 360*(1-0.35)=234
        hist = _history("nyc", [(i, [100, 120, 140]) for i in range(1, 13)])
        out = rt.suggest_for_route(self._route(), hist, date.today())
        self.assertEqual(out["suggested"], rt.round_to(360 * (1 - rt.MAX_STEP_PCT), rt.ROUND_TO))

    def test_never_below_cheapest_observed(self):
        # tight cluster near $300; floor = 300*0.85 = 255; p25 ~300 so stays ~300
        hist = _history("nyc", [(i, [300, 305, 310]) for i in range(1, 13)])
        out = rt.suggest_for_route(self._route(), hist, date.today())
        self.assertGreaterEqual(out["suggested"], round(300 * 0.85))

    def test_legacy_oneway_route_current_is_2x(self):
        hist = _history("nyc", [(i, [300, 320]) for i in range(1, 13)])
        out = rt.suggest_for_route({"id": "nyc", "label": "NYC", "max_price_oneway": 150},
                                   hist, date.today())
        self.assertEqual(out["current"], 300)


class ApplyFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.wl = self.tmp / "watchlist.json"
        self.hist = self.tmp / "price_history.json"
        self.wl.write_text(json.dumps({"opportunistic_routes": [
            {"id": "nyc", "label": "New York", "origin": "DAL", "destinations": ["LGA"],
             "max_price_roundtrip": 360, "return_offset_days": 3}], "trips": []}))
        self.hist.write_text(json.dumps({"entries":
            _history("nyc", [(i, [300, 330, 360, 390]) for i in range(1, 15)])}))
        self.sent = []
        self._orig = (rt.WATCHLIST_FILE, rt.HISTORY_FILE, rt.send_telegram)
        rt.WATCHLIST_FILE, rt.HISTORY_FILE = self.wl, self.hist
        rt.send_telegram = lambda text: self.sent.append(text)

    def tearDown(self):
        rt.WATCHLIST_FILE, rt.HISTORY_FILE, rt.send_telegram = self._orig

    def test_apply_writes_new_threshold_and_messages(self):
        sys.argv = ["retune_thresholds.py", "--apply"]
        rt.main()
        new = json.loads(self.wl.read_text())["opportunistic_routes"][0]["max_price_roundtrip"]
        self.assertLess(new, 360)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("applied", self.sent[0])

    def test_default_run_does_not_write(self):
        sys.argv = ["retune_thresholds.py"]
        rt.main()
        self.assertEqual(
            json.loads(self.wl.read_text())["opportunistic_routes"][0]["max_price_roundtrip"], 360)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("proposal", self.sent[0])

    def test_dry_run_no_write_no_telegram(self):
        sys.argv = ["retune_thresholds.py", "--dry-run"]
        rt.main()
        self.assertEqual(
            json.loads(self.wl.read_text())["opportunistic_routes"][0]["max_price_roundtrip"], 360)
        self.assertEqual(self.sent, [])

    def test_silent_when_nothing_to_change(self):
        # threshold already sits at the p25 of a tight price cluster → no change, no ping
        self.wl.write_text(json.dumps({"opportunistic_routes": [
            {"id": "nyc", "label": "New York", "max_price_roundtrip": 300, "return_offset_days": 3}],
            "trips": []}))
        self.hist.write_text(json.dumps({"entries":
            _history("nyc", [(i, [300, 300, 300]) for i in range(1, 15)])}))
        sys.argv = ["retune_thresholds.py", "--apply"]
        rt.main()
        self.assertEqual(self.sent, [])


if __name__ == "__main__":
    unittest.main()
