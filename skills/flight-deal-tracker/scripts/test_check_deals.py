#!/usr/bin/env python3
"""
Tests for check_deals.py — focused on the 2026-08-29 change that makes
opportunistic routes price ROUND TRIPS (was one-way; "if I go to LA I need
to get back").

Run: python3 test_check_deals.py
"""
import importlib.util
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

_MOD = Path(__file__).parent / "check_deals.py"
_spec = importlib.util.spec_from_file_location("check_deals", _MOD)
cd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cd)


def _flights(price, stops=0):
    return {"flights": [{"airline": "Southwest", "stops": stops, "price": price}]}


class RoundTripThreshold(unittest.TestCase):
    def test_reads_explicit_roundtrip(self):
        self.assertEqual(cd.route_roundtrip_threshold({"max_price_roundtrip": 240}), 240)

    def test_missing_roundtrip_threshold_fails_loud(self):
        with self.assertRaises(KeyError):
            cd.route_roundtrip_threshold({"max_price_oneway": 109})


class NonstopOnly(unittest.TestCase):
    """Connecting fares are not deals: never quoted, logged or alerted on."""

    def test_picks_cheapest_nonstop_over_cheaper_connection(self):
        best = cd.cheapest({"flights": [
            {"airline": "Southwest", "stops": 2, "price": 292},
            {"airline": "Southwest", "stops": 1, "price": 297},
            {"airline": "Southwest", "stops": 0, "price": 345},
            {"airline": "Southwest", "stops": 0, "price": 410},
        ]})
        self.assertEqual((best["price"], best["stops"]), (345, 0))

    def test_no_nonstop_means_no_quote(self):
        self.assertIsNone(cd.cheapest(_flights(292, stops=2)))

    def test_unpriced_rows_are_skipped(self):
        best = cd.cheapest({"flights": [
            {"airline": "Southwest", "stops": 0, "price": None},
            {"airline": "Southwest", "stops": 0, "price": 300},
        ]})
        self.assertEqual(best["price"], 300)

    def test_placeholder_row_is_not_a_zero_dollar_nonstop(self):
        """fast_flights' empty row: price 0, stops coerced to 0, stop_info
        "Unknown". It must never win on price (or be logged/alerted)."""
        placeholder = {"airline": "Unknown", "departure_time": "", "arrival_time": "",
                       "duration": "", "stops": 0, "stop_info": "Unknown", "price": 0,
                       "is_best": True, "arrival_ahead": ""}
        best = cd.cheapest({"flights": [placeholder,
                                        {"airline": "Southwest", "stops": 0, "price": 310}]})
        self.assertEqual(best["price"], 310)
        self.assertIsNone(cd.cheapest({"flights": [placeholder]}))
        # Also excluded when connections are allowed -- it isn't a fare at all.
        self.assertIsNone(cd.cheapest({"flights": [placeholder]}, nonstop=False))

    def test_unknown_stop_count_is_not_nonstop(self):
        self.assertFalse(cd.is_nonstop({"stops": 0, "stop_info": "Unknown"}))
        self.assertTrue(cd.is_nonstop({"stops": 0, "stop_info": None}))
        self.assertFalse(cd.is_nonstop({"stops": 1}))

    def test_allow_connections_opt_out(self):
        self.assertTrue(cd.nonstop_only({"id": "nyc"}))
        self.assertFalse(cd.nonstop_only({"id": "nyc", "allow_connections": True}))
        best = cd.cheapest(_flights(292, stops=2), nonstop=False)
        self.assertEqual(best["price"], 292)


class DealContext(unittest.TestCase):
    """The alert says where the fare sits among nonstop fares recently seen,
    not just that it cleared a line."""

    def _hist(self, prices, **over):
        row = {"kind": "opportunistic", "route_id": "la", "trip_type": "roundtrip",
               "date_checked": date.today().isoformat(), "stops": 0}
        row.update(over)
        return [dict(row, price=p) for p in prices]

    def test_percentile_and_typical(self):
        note = cd.deal_context(self._hist(list(range(300, 700, 5))),
                               route_id="la", label="LA (Southern California)",
                               price=310, today=date.today().isoformat())
        # 80 samples 300..695; 77 are dearer than $310 -> 96%
        self.assertIn("Cheaper than 96% of LA nonstops seen", note)
        self.assertIn("typical $500", note)

    def test_label_is_shortened(self):
        note = cd.deal_context(self._hist(range(300, 400)), route_id="la",
                               label="LA (Southern California)", price=305,
                               today=date.today().isoformat())
        self.assertIn("of LA nonstops", note)
        self.assertNotIn("Southern", note)

    def test_thin_history_says_nothing(self):
        self.assertIsNone(cd.deal_context(self._hist([300, 310, 320]), route_id="la",
                                          label="LA", price=300,
                                          today=date.today().isoformat()))

    def test_connections_and_zero_rows_excluded_from_comparison(self):
        # 25 nonstop rows at 500 + cheap connections that must not drag "typical" down
        entries = (self._hist([500] * 25)
                   + self._hist([100] * 40, stops=2)
                   + self._hist([0] * 40))
        note = cd.deal_context(entries, route_id="la", label="LA", price=400,
                               today=date.today().isoformat())
        self.assertIn("typical $500", note)
        self.assertIn("Cheaper than 100%", note)

    def test_stale_rows_outside_lookback_ignored(self):
        old = (date.today() - timedelta(days=cd.CONTEXT_LOOKBACK_DAYS + 5)).isoformat()
        self.assertIsNone(cd.deal_context(self._hist([300] * 50, date_checked=old),
                                          route_id="la", label="LA", price=300,
                                          today=date.today().isoformat()))

    def test_other_routes_not_mixed_in(self):
        entries = self._hist([300] * 50, route_id="nyc")
        self.assertIsNone(cd.deal_context(entries, route_id="la", label="LA",
                                          price=300, today=date.today().isoformat()))


class SouthwestPreference(unittest.TestCase):
    """Southwest out of Love Field is a priced preference, never a filter."""

    def _mix(self, southwest, other, other_name="Delta"):
        return {"flights": [
            {"airline": "Southwest", "stops": 0, "price": southwest},
            {"airline": other_name, "stops": 0, "price": other},
        ]}

    def test_southwest_wins_when_the_gap_is_within_the_margin(self):
        best = cd.cheapest(self._mix(southwest=350, other=300))   # $50 gap
        self.assertEqual(best["airline"], "Southwest")

    def test_southwest_wins_at_exactly_the_margin(self):
        best = cd.cheapest(self._mix(southwest=375, other=300))   # $75 gap
        self.assertEqual(best["airline"], "Southwest")

    def test_a_genuinely_great_deal_still_wins(self):
        best = cd.cheapest(self._mix(southwest=400, other=300))   # $100 gap
        self.assertEqual((best["airline"], best["price"]), ("Delta", 300))

    def test_southwest_taken_when_it_is_also_cheapest(self):
        best = cd.cheapest(self._mix(southwest=280, other=300))
        self.assertEqual((best["airline"], best["price"]), ("Southwest", 280))

    def test_no_southwest_on_the_route_falls_back_to_cheapest(self):
        best = cd.cheapest({"flights": [
            {"airline": "American", "stops": 0, "price": 310},
            {"airline": "Delta", "stops": 0, "price": 290}]})
        self.assertEqual((best["airline"], best["price"]), ("Delta", 290))

    def test_preference_never_resurrects_a_connection(self):
        best = cd.cheapest({"flights": [
            {"airline": "Southwest", "stops": 1, "price": 200},
            {"airline": "Delta", "stops": 0, "price": 400}]})
        self.assertEqual((best["airline"], best["stops"]), ("Delta", 0))


class AlternateOrigin(unittest.TestCase):
    """DFW is mentioned only when it beats Love Field by more than the Ubers."""

    def setUp(self):
        self._real_search, self._real_sleep = cd.search_flight, cd.time.sleep
        cd.time.sleep = lambda s: None

    def tearDown(self):
        cd.search_flight, cd.time.sleep = self._real_search, self._real_sleep

    def _deal(self, price=400):
        return [{"route_id": "la", "label": "LA (Southern California)", "dest": "LAX",
                 "depart": "2026-10-24", "return_date": "2026-10-31", "price": price}]

    def _run(self, dfw_price, dal_price=400, airline="American"):
        self.queries = []
        def fake(o, d, dep, ret=None):
            self.queries.append(o)
            return {"flights": [{"airline": airline, "stops": 0, "price": dfw_price}]}
        cd.search_flight = fake
        deals, hist = self._deal(dal_price), []
        cd.check_alternate_origin(deals, hist, date.today().isoformat(), quiet=True)
        return deals[0], hist

    def test_only_dfw_is_searched_and_only_once(self):
        self._run(250)
        self.assertEqual(self.queries, ["DFW"])

    def test_shown_when_it_clears_the_ground_cost(self):
        deal, _ = self._run(dfw_price=250, dal_price=400)   # 400 - (250+120) = $30
        self.assertEqual(deal["alt"]["saving"], 30)
        self.assertEqual(deal["alt"]["origin"], "DFW")

    def test_hidden_when_the_ubers_eat_the_saving(self):
        deal, _ = self._run(dfw_price=300, dal_price=400)   # $100 gap < $120 ubers
        self.assertNotIn("alt", deal)

    def test_hidden_when_it_only_ties_after_ubers(self):
        deal, _ = self._run(dfw_price=280, dal_price=400)   # exactly $120
        self.assertNotIn("alt", deal)

    def test_dfw_rows_do_not_pollute_the_love_field_baseline(self):
        _, hist = self._run(dfw_price=250)
        self.assertEqual([e["kind"] for e in hist], ["alt_origin"])
        self.assertEqual(cd.route_baseline(hist, "la", date.today().isoformat()), [])

    def test_digest_line_reads_as_a_saving(self):
        deal, hist = self._run(dfw_price=250, dal_price=400)
        hist += [{"kind": "opportunistic", "route_id": "la", "trip_type": "roundtrip",
                  "date_checked": date.today().isoformat(), "stops": 0, "price": 400}]
        body = cd.format_digest([deal], hist, date.today().isoformat())
        self.assertIn("LA  $400 LAX", body)
        self.assertIn("   DFW $250 American · saves $30 after Ubers", body)


class FormatAlert(unittest.TestCase):
    def test_roundtrip_form(self):
        msg = cd.format_alert("New York", "DAL", "LGA", "2026-11-13",
                              188, {"airline": "Southwest", "stops": 0},
                              "(threshold: $330)", return_date="2026-11-16")
        self.assertIn("DAL ⇄ LGA", msg)
        self.assertIn("2026-11-13 → 2026-11-16 · round trip", msg)

    def test_oneway_form_unchanged(self):
        msg = cd.format_alert("New York", "DAL", "LGA", "2026-11-13",
                              95, {"airline": "Southwest", "stops": 0}, "(threshold: $150)")
        self.assertIn("DAL → LGA", msg)
        self.assertNotIn("round trip", msg)


class SampleGrid(unittest.TestCase):
    def test_next_sample_departures_shape(self):
        dates = cd.next_sample_departures((3, 4, 5), 3, 75, 7)
        self.assertEqual(len(dates), 9)                       # 3 weekdays x 3 weeks
        self.assertEqual(sorted(dates), dates)                # sorted
        self.assertEqual({d.weekday() for d in dates}, {3, 4, 5})
        self.assertEqual(sum(d.weekday() == 4 for d in dates), 3)
        self.assertTrue(all((d - date.today()).days >= 7 for d in dates))


class OpportunisticRoundTrip(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self._real_search = cd.search_flight
        self._real_send = cd.send_telegram
        self._real_sleep = cd.time.sleep
        cd.time.sleep = lambda s: None
        cd.send_telegram = lambda text, dry_run: self.sent.append(text)
        self.queries = []
        self.n_dates = len(cd.next_sample_departures(
            cd.SAMPLE_WEEKDAYS, cd.SAMPLE_WEEKS, cd.SAMPLE_HORIZON_DAYS, cd.SAMPLE_START_OFFSET_DAYS))

    def tearDown(self):
        cd.search_flight = self._real_search
        cd.send_telegram = self._real_send
        cd.time.sleep = self._real_sleep

    def _route(self, **over):
        r = {"id": "nyc", "label": "New York", "origin": "DAL", "destinations": ["LGA"],
             "enabled": True, "max_price_roundtrip": 330, "return_offset_days": 3}
        r.update(over)
        return r

    def _run(self, price, route=None, alert_state=None, stops=0):
        route = route or self._route()
        def fake_search(o, d, dep, ret=None):
            self.queries.append((o, d, dep, ret))
            return _flights(price, stops)
        cd.search_flight = fake_search
        hist, astate = [], (alert_state or {})
        today = date.today().isoformat()
        deals = []
        cd.check_opportunistic_routes({"opportunistic_routes": [route]}, hist, astate,
                                      today, dry_run=False, quiet=True, deals=deals)
        cd.deliver_digest(deals, hist, astate, today, dry_run=False)
        return hist, astate

    def test_queries_are_round_trips_across_the_grid(self):
        self._run(300)
        self.assertEqual(len(self.queries), self.n_dates)     # one per sampled departure
        for o, d, dep, ret in self.queries:
            self.assertEqual((o, d), ("DAL", "LGA"))
            self.assertEqual((date.fromisoformat(ret) - date.fromisoformat(dep)).days, 3)
        self.assertEqual({date.fromisoformat(q[2]).weekday() for q in self.queries}, {3, 4, 5})

    def test_multiple_return_lengths_per_departure(self):
        self._run(300, route=self._route(return_offset_days=[3, 6]))
        self.assertEqual(len(self.queries), self.n_dates * 2)
        gaps = {(date.fromisoformat(r) - date.fromisoformat(dep)).days for _, _, dep, r in self.queries}
        self.assertEqual(gaps, {3, 6})

    def test_all_destination_airports_are_checked(self):
        self._run(400, route=self._route(destinations=["LGA", "JFK", "EWR"]))
        self.assertEqual(len(self.queries), self.n_dates * 3)
        self.assertEqual({q[1] for q in self.queries}, {"LGA", "JFK", "EWR"})

    def test_one_digest_not_one_message_per_fare(self):
        hist, _ = self._run(299)
        self.assertEqual(len(self.sent), 1)            # 9 qualifying fares, ONE message
        self.assertEqual(self.sent[0].splitlines()[0], "FLIGHT DEALS")
        self.assertIn("New York  $299 LGA", self.sent[0])
        self.assertIn("(cheapest seen)", self.sent[0])
        self.assertTrue(all(e["trip_type"] == "roundtrip" for e in hist))

    def test_marker_only_when_nothing_cheaper_has_been_seen(self):
        today = date.today().isoformat()
        hist = [{"kind": "opportunistic", "route_id": "nyc", "trip_type": "roundtrip",
                 "date_checked": today, "stops": 0, "price": p} for p in (200, 250, 300)]
        deal = [{"route_id": "nyc", "label": "New York", "dest": "LGA", "price": 260,
                 "depart": "2026-10-24", "return_date": "2026-10-27"}]
        self.assertNotIn("cheapest seen", cd.format_digest(deal, hist, today))
        deal[0]["price"] = 200
        self.assertIn("(cheapest seen)", cd.format_digest(deal, hist, today))

    def test_date_span_repeats_month_only_when_it_changes(self):
        self.assertEqual(cd.span("2026-10-24", "2026-10-31"), "Oct 24 → 31")
        self.assertEqual(cd.span("2026-10-24", "2026-11-07"), "Oct 24 → Nov 7")

    def test_one_line_per_route_showing_the_cheapest_airport(self):
        """The user asked for one line per route, not one per airport (2026-09-28)."""
        def fake_search(o, d, dep, ret=None):
            return _flights({"LGA": 300, "JFK": 250, "EWR": 280}[d])
        cd.search_flight = fake_search
        hist, astate, today = [], {}, date.today().isoformat()
        deals = []
        cd.check_opportunistic_routes(
            {"opportunistic_routes": [self._route(destinations=["LGA", "JFK", "EWR"])]},
            hist, astate, today, dry_run=False, quiet=True, deals=deals)
        cd.deliver_digest(deals, hist, astate, today, dry_run=False)
        self.assertEqual(len(self.sent), 1)
        body = self.sent[0]
        self.assertEqual(len(body.splitlines()), 2)          # header + one route line
        self.assertIn("New York  $250 JFK", body)            # cheapest airport wins
        for gone in ("EWR", "LGA", "typical", "other date", "Card A", "%"):
            self.assertNotIn(gone, body)

    def test_unchanged_picture_stays_silent(self):
        _, astate = self._run(299)
        self.assertEqual(len(self.sent), 1)
        self._run(299, alert_state=astate)          # same fares tomorrow -> no message
        self.assertEqual(len(self.sent), 1)

    def test_connections_are_never_alerted_or_logged(self):
        hist, astate = self._run(199, route=self._route(), stops=1)
        self.assertEqual(self.sent, [])
        self.assertEqual(hist, [])
        self.assertEqual(astate, {})

    def test_allow_connections_route_still_alerts(self):
        self._run(199, route=self._route(allow_connections=True), stops=1)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("FLIGHT DEALS", self.sent[0])

    def test_no_alert_when_over_threshold(self):
        self._run(400)
        self.assertEqual(self.sent, [])

    def test_dedup_same_price_then_improve(self):
        _, astate = self._run(320)
        self.assertEqual(len(self.sent), 1)
        self._run(320, alert_state=astate)          # same price → silent
        self.assertEqual(len(self.sent), 1)
        self._run(300, alert_state=astate)          # cheaper → one new digest
        self.assertEqual(len(self.sent), 2)
        self.assertIn("$300 LGA", self.sent[1])

class TripsStillWork(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self._real_search, self._real_send = cd.search_flight, cd.send_telegram
        cd.send_telegram = lambda text, dry_run: self.sent.append(text)
        self.queries = []

    def tearDown(self):
        cd.search_flight, cd.send_telegram = self._real_search, self._real_send

    def _run(self, trip, price, stops=0):
        def fake_search(o, d, dep, ret=None):
            self.queries.append((o, d, dep, ret))
            return _flights(price, stops)
        cd.search_flight = fake_search
        cd.check_trips({"trips": [trip]}, [], {}, date.today().isoformat(), dry_run=False, quiet=True)

    def test_trip_with_return_is_round_trip_query(self):
        self._run({"id": "t1", "status": "watching", "origin": "DAL", "destination": "LGA",
                   "depart_date": "2026-09-01", "return_date": "2026-09-08", "label": "NYC"}, 250)
        self.assertEqual(self.queries, [("DAL", "LGA", "2026-09-01", "2026-09-08")])

    def test_oneway_trip_query(self):
        self._run({"id": "t2", "status": "watching", "origin": "DAL", "destination": "AUS",
                   "depart_date": "2026-09-01", "return_date": None, "label": "ATX"}, 60)
        self.assertEqual(self.queries, [("DAL", "AUS", "2026-09-01", None)])

    def test_first_observation_is_silent_baseline(self):
        # No target, no history → the very first check must not alert.
        self._run({"id": "t3", "status": "watching", "origin": "DAL", "destination": "LGA",
                   "depart_date": "2026-09-01", "return_date": "2026-09-08", "label": "NYC"}, 503)
        self.assertEqual(self.sent, [])

    def test_target_price_alerts_on_first_check(self):
        self._run({"id": "t4", "status": "watching", "origin": "DAL", "destination": "LGA",
                   "depart_date": "2026-09-01", "return_date": "2026-09-08", "label": "NYC",
                   "target_price": 400}, 380)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("DAL ⇄ LGA", self.sent[0])
        self.assertIn("round trip", self.sent[0])


    def test_trip_connection_is_not_a_deal(self):
        self._run({"id": "t5", "status": "watching", "origin": "DAL", "destination": "LGA",
                   "depart_date": "2026-09-01", "return_date": "2026-09-08", "label": "NYC",
                   "target_price": 400}, 250, stops=1)
        self.assertEqual(self.sent, [])


class DryRunDoesNotPersistAlertState(unittest.TestCase):
    """A --dry-run must log price history but leave deal_alert_state.json
    untouched, or the alerts it only *printed* silently suppress the real
    cron run's alerts."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.hist = self.tmp / "history.json"
        self.astate = self.tmp / "alert_state.json"
        self.watch = self.tmp / "watchlist.json"
        self.watch.write_text(json.dumps({
            "opportunistic_routes": [
                {"id": "aus", "label": "Austin", "origin": "DAL", "destinations": ["AUS"],
                 "enabled": True, "max_price_roundtrip": 300, "return_offset_days": 2}],
            "trips": [],
        }))
        self._orig = (cd.HISTORY_FILE, cd.ALERT_STATE_FILE, cd.WATCHLIST_FILE, cd.OUTBOX_DB,
                      cd.search_flight, cd.send_telegram, cd.time.sleep)
        cd.HISTORY_FILE, cd.ALERT_STATE_FILE, cd.WATCHLIST_FILE = self.hist, self.astate, self.watch
        cd.OUTBOX_DB = self.tmp / "monitoring.sqlite"
        cd.time.sleep = lambda s: None
        self.sent = []
        cd.send_telegram = lambda text, dry_run: self.sent.append(text) if not dry_run else None
        cd.search_flight = lambda o, d, dep, ret=None: _flights(150)  # well under threshold

    def tearDown(self):
        (cd.HISTORY_FILE, cd.ALERT_STATE_FILE, cd.WATCHLIST_FILE, cd.OUTBOX_DB,
         cd.search_flight, cd.send_telegram, cd.time.sleep) = self._orig

    def _main(self, *argv):
        import sys
        sys.argv = ["check_deals.py", *argv]
        cd.main()

    def test_dry_run_logs_history_but_not_alert_state(self):
        self._main("--dry-run", "--quiet")
        self.assertTrue(self.hist.exists())
        self.assertTrue(json.loads(self.hist.read_text())["entries"])   # prices logged
        self.assertEqual(json.loads(self.astate.read_text()), {})       # dedup state clean
        # A subsequent REAL run must therefore still fire the alerts.
        self._main("--quiet")
        self.assertTrue(self.sent)


class DeliveryConfirmation(unittest.TestCase):
    def test_failed_delivery_does_not_mark_alerted(self):
        original_search, original_send = cd.search_flight, cd.send_telegram
        cd.search_flight = lambda *args, **kwargs: _flights(200)
        cd.send_telegram = lambda *args, **kwargs: False
        try:
            state = {}
            cd.check_trips({"trips": [{
                "id": "fail", "status": "watching", "origin": "DAL",
                "destination": "LGA", "depart_date": "2026-10-01",
                "return_date": "2026-10-04", "label": "NYC", "target_price": 300,
            }]}, [], state, date.today().isoformat(), False, True)
            self.assertEqual(state, {})
        finally:
            cd.search_flight, cd.send_telegram = original_search, original_send


if __name__ == "__main__":
    unittest.main()
