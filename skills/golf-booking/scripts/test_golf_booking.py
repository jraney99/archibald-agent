#!/usr/bin/env python3
"""golf-booking: approved list is the gate, GolfNow parsing, proposal text, confirm guards.

Offline: the API fixture and the page capture in flowmap/ stand in for the network and
the browser, every test runs in a throwaway OPENCLAW_HOME, and TELEGRAM_SEND_DISABLED=1
is set before anything that could send is imported.

    python3 test_golf_booking.py
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

os.environ["TELEGRAM_SEND_DISABLED"] = "1"
HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(SKILL.parent / "shared"))

FIXTURE_API = json.loads((HERE / "flowmap" / "api_examplecreek_west_2026-10-17.json").read_text())
FIXTURE_PAGE = (HERE / "flowmap" / "teetime_prelogin_examplecreek_2026-10-11.txt").read_text()
LIVE_CONFIG = json.loads((SKILL / "config" / "approved_courses.json").read_text())


class Sandbox(unittest.TestCase):
    """Every test gets its own OPENCLAW_HOME with a copy of the live config."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["OPENCLAW_HOME"] = self.tmp.name
        for mod in ("data_paths", "courses", "golfnow", "search_tee_times", "propose_booking",
                    "confirm_booking", "browser_helpers", "deal_watch", "sunset_golf"):
            sys.modules.pop(mod, None)
        import data_paths  # noqa: F401
        self.dp = data_paths
        cfg_path = data_paths.path("approved_courses.json")
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(json.dumps(LIVE_CONFIG))
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(lambda: os.environ.pop("OPENCLAW_HOME", None))

    def fetch(self, facility_id, day, players, lat, lon):
        self.fetched = (facility_id, day, players)
        return FIXTURE_API


class Registry(Sandbox):
    def test_files_are_registered_in_their_proper_places(self):
        for name, kind in (("approved_courses.json", "config"), ("pending_golf_booking.json", "state"),
                           ("tee_time_bookings.json", "data")):
            owner, k, today = self.dp.REGISTRY[name]
            self.assertEqual((owner, k), ("golf-booking", kind), name)
            self.assertEqual(Path(today).parts[-3:], ("golf-booking", kind, name), name)


class Courses(Sandbox):
    def test_alias_and_id_resolve_to_one_course(self):
        import courses
        cfg = courses.load()
        self.assertEqual(courses.find(cfg, "example creek")["id"], "example-creek-west")
        self.assertEqual(courses.find(cfg, "Cedar")["id"], "cedar-hills")
        self.assertEqual(courses.find(cfg, "cedar-hills")["id"], "cedar-hills")
        self.assertEqual(courses.find(cfg, "oak park")["id"], "oak-park-highlands")

    def test_unknown_course_is_nobody(self):
        import courses
        cfg = courses.load()
        self.assertIsNone(courses.find(cfg, "Augusta National"))
        self.assertIsNone(courses.find(cfg, ""))

    def test_only_golfnow_courses_are_bookable_and_the_rest_say_how(self):
        import courses
        cfg = courses.load()
        self.assertTrue(courses.bookable(courses.find(cfg, "example creek")))
        ten = courses.find(cfg, "oak park")
        self.assertFalse(courses.bookable(ten))
        why = courses.not_bookable_reason(ten)
        self.assertIn("not sold through GolfNow", why)
        self.assertIn(ten["book_url"], why)
        self.assertIn(ten["phone"], why)

    def test_unapproved_course_in_config_is_invisible(self):
        import courses
        cfg = courses.load()
        cfg["courses"].append({"id": "x", "name": "Secret Club", "short": "Secret", "provider": "golfnow",
                               "facility_id": 1, "approved": False})
        self.assertIsNone(courses.find(cfg, "secret club"))


class GolfNowParsing(Sandbox):
    def test_slots_from_the_real_response(self):
        import golfnow
        s = golfnow.slots(FIXTURE_API)
        self.assertEqual(len(s), 3)
        first = s[0]
        self.assertEqual(first["time_label"], "3:30 PM")
        self.assertEqual(first["time"], datetime(2026, 10, 17, 15, 30))
        self.assertEqual(first["price"], 60.95)
        self.assertEqual(first["holes"], 18)
        self.assertTrue(first["cart"])
        self.assertTrue(first["prepaid"])
        self.assertEqual(first["rate_id"], -100000001)
        self.assertEqual(first["detail_url"], "https://www.golfnow.com/tee-times/facility/1001/tee-time/-100000001")
        self.assertEqual(first["course"], "Example Creek Golf Club - West Course")
        self.assertIn("100 Example St", first["address"])

    def test_time_normalisation_and_windows(self):
        import golfnow
        for raw, want in (("3:30pm", "3:30 PM"), ("3 PM", "3:00 PM"), ("at 7:05 am", "7:05 AM"), ("noonish", "")):
            self.assertEqual(golfnow.norm_time(raw), want, raw)
        s = golfnow.slots(FIXTURE_API)[0]
        self.assertTrue(golfnow.within(s, "07:00", "18:30"))
        self.assertFalse(golfnow.within(s, "07:00", "15:00"))
        self.assertTrue(golfnow.within(s, "3:30 PM", "4:00 PM"))

    def test_http_500_is_a_provider_error_not_an_empty_sheet(self):
        import urllib.error
        import golfnow

        def boom(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 500, "Server Error", {}, None)
        real = golfnow.urllib.request.urlopen
        golfnow.urllib.request.urlopen = boom
        try:
            with self.assertRaises(golfnow.ProviderError) as cm:
                golfnow.fetch(1004, date(2026, 10, 17), 2)
            self.assertIn("does not sell", str(cm.exception))
        finally:
            golfnow.urllib.request.urlopen = real

    def test_estimate_total_is_fees_plus_allowance(self):
        import golfnow
        self.assertEqual(golfnow.estimate_total(60.95, 2, 10), 141.90)


class Search(Sandbox):
    def test_search_is_approved_only_and_formats_plainly(self):
        import search_tee_times as st
        import courses
        cfg = courses.load()
        res = st.search(cfg, date(2026, 10, 17), 2, ["example creek"], "07:00", "18:30", 90, fetch=self.fetch)
        self.assertEqual(self.fetched, (1001, date(2026, 10, 17), 2))
        self.assertEqual(len(res["courses"]), 1)
        self.assertEqual(len(res["courses"][0]["slots"]), 3)
        text = st.format_telegram(res)
        self.assertIn("Example Creek West", text)
        self.assertIn("3:30 PM - 3:49 PM (3)  $61/golfer, 18 holes", text)
        self.assertNotIn("*", text)
        self.assertNotIn(".py", text)

    def test_unapproved_course_exits_with_a_sentence(self):
        import search_tee_times as st
        import courses
        cfg = courses.load()
        res = st.search(cfg, date(2026, 10, 17), 1, ["Augusta"], "07:00", "18:30", None, fetch=self.fetch)
        self.assertIn("not on your approved course list", res["error"])

    def test_non_golfnow_course_is_listed_with_its_own_link_and_never_fetched(self):
        import search_tee_times as st
        import courses
        cfg = courses.load()
        self.fetched = None
        res = st.search(cfg, date(2026, 10, 17), 1, ["oak park"], "07:00", "18:30", None, fetch=self.fetch)
        self.assertIsNone(self.fetched)
        self.assertIn("cps.golf", res["courses"][0]["not_bookable"])
        self.assertIn("cps.golf", st.format_telegram(res))

    def test_price_ceiling_and_window_filter(self):
        import search_tee_times as st
        import courses
        cfg = courses.load()
        res = st.search(cfg, date(2026, 10, 17), 1, ["example creek"], "07:00", "12:00", None, fetch=self.fetch)
        self.assertEqual(res["courses"][0]["slots"], [])
        self.assertIn("nothing open between 07:00 and 12:00", st.format_telegram(res))
        res = st.search(cfg, date(2026, 10, 17), 1, ["example creek"], "07:00", "18:30", 50, fetch=self.fetch)
        self.assertEqual(res["courses"][0]["slots"], [])

    def test_parse_day(self):
        import search_tee_times as st
        self.assertEqual(st.parse_day("2026-10-17"), date(2026, 10, 17))
        self.assertEqual(st.parse_day("today"), date.today())
        self.assertGreater(st.parse_day("saturday"), date.today())


class Propose(Sandbox):
    def test_builds_pending_and_card_text(self):
        import propose_booking as pb
        import courses
        cfg = courses.load()
        pending, text = pb.build(cfg, "example creek", datetime(2026, 10, 17), "3:30pm", 2, "", fetch=self.fetch)
        self.assertEqual(pending["course_id"], "example-creek-west")
        self.assertEqual(pending["time"], "3:30 PM")
        self.assertEqual(pending["players"], 2)
        self.assertEqual(pending["price_per_golfer"], 60.95)
        self.assertEqual(pending["max_total"], 141.90)
        self.assertEqual(pending["expected_card"]["last4"], "0000")
        self.assertIn("Example Creek Golf Club - West Course", text)
        self.assertIn("Saturday Oct 17 at 3:30 PM", text)
        self.assertIn("2 golfers", text)
        self.assertIn("$60.95/golfer", text)
        self.assertIn("Card A", text)
        self.assertIn("Non-refundable", text)
        self.assertIn("stop at the checkout page", text)   # checkout.mapped is false
        self.assertNotIn(".py", text)

    def test_refuses_unapproved_non_bookable_missing_slot_and_over_ceiling(self):
        import propose_booking as pb
        import courses
        cfg = courses.load()
        day = datetime(2026, 10, 17)
        with self.assertRaises(LookupError) as cm:
            pb.build(cfg, "Augusta", day, "3:30 PM", 1, "", fetch=self.fetch)
        self.assertIn("approved course list", str(cm.exception))
        with self.assertRaises(LookupError) as cm:
            pb.build(cfg, "oak park", day, "3:30 PM", 1, "", fetch=self.fetch)
        self.assertIn("cps.golf", str(cm.exception))
        with self.assertRaises(LookupError) as cm:
            pb.build(cfg, "example creek", day, "7:00 AM", 1, "", fetch=self.fetch)
        self.assertIn("no 7:00 AM tee time", str(cm.exception))
        self.assertIn("3:30 PM", str(cm.exception))
        cfg["defaults"]["max_price_per_golfer"] = 50
        with self.assertRaises(LookupError) as cm:
            pb.build(cfg, "example creek", day, "3:30 PM", 1, "", fetch=self.fetch)
        self.assertIn("over your $50 ceiling", str(cm.exception))

    def test_main_writes_pending_in_the_sandbox_and_sends_once(self):
        import propose_booking as pb
        import golfnow
        sent = []
        pb.send_proposal = lambda text: sent.append(text) or 4242
        golfnow.fetch = self.fetch
        pb.golfnow.fetch = self.fetch
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc = pb.main(["--course", "example creek", "--date", "2026-10-17", "--time", "3:30 PM", "--players", "2"])
        self.assertEqual(rc, 0)
        self.assertEqual(len(sent), 1)
        pf = self.dp.path("pending_golf_booking.json")
        self.assertTrue(str(pf).startswith(self.tmp.name))
        pending = json.loads(pf.read_text())
        self.assertEqual(pending["source_tg_msg_id"], 4242)
        # a second proposal while one waits is refused, and nothing is sent
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc = pb.main(["--course", "example creek", "--date", "2026-10-17", "--time", "3:39 PM"])
        self.assertEqual(rc, 5)
        self.assertEqual(len(sent), 1)


class PageParsing(Sandbox):
    def test_prelogin_capture_reads_as_logged_out_with_the_slot_facts(self):
        import browser_helpers as bh
        sel = LIVE_CONFIG["checkout"]["selectors"]
        self.assertTrue(bh.logged_out(FIXTURE_PAGE, sel["logged_out_marker"]))
        page = bh.read_tee_time_page(FIXTURE_PAGE, sel)
        self.assertEqual(page["date"], "2026-10-17")
        self.assertEqual(page["time"], "3:30 PM")
        self.assertEqual(page["price"], 60.95)
        self.assertEqual(page["holes"], 18)
        self.assertEqual(page["rate"], "Prepaid - Online Rate")
        self.assertIn("Example Creek", page["course"])
        self.assertEqual(page["golfers_selected"], 1)
        self.assertEqual(page["total"], 69.22)
        self.assertEqual(page["golfer_refs"], {"1": "e77", "2": "e78", "3": "e79", "4": "e80"})
        self.assertEqual(page["continue_ref"], "e98")

    def test_logged_in_page_has_no_log_in_item(self):
        import browser_helpers as bh
        snap = FIXTURE_PAGE.replace('menuitem "Log in"', 'menuitem "My Account"')
        self.assertFalse(bh.logged_out(snap, r"^log in$"))

    def test_signed_in_header_capture_is_positive_and_prelogin_is_not(self):
        import browser_helpers as bh
        signed = (HERE / "flowmap" / "home_loggedin_header_2026-10-12.txt").read_text()
        marker = LIVE_CONFIG["checkout"]["selectors"]["logged_in_marker"]
        self.assertTrue(bh.logged_in(signed, marker))
        self.assertFalse(bh.logged_out(signed, LIVE_CONFIG["checkout"]["selectors"]["logged_out_marker"]))
        self.assertFalse(bh.logged_in(FIXTURE_PAGE, marker))

    def test_card_last4_and_money_after(self):
        import browser_helpers as bh
        snap = '- text: Visa ending in 0000\n- term [ref=e1]: Total\n- definition [ref=e2]: $139.44\n'
        self.assertEqual(bh.card_last4(snap, LIVE_CONFIG["checkout"]["selectors"]["card_marker"]), ["0000"])
        self.assertEqual(bh.money_after(snap, r"^\s*-\s*term \[ref=e1\]: Total"), 139.44)


class ConfirmGuards(Sandbox):
    PENDING = {"course": "Example Creek Golf Club - West Course", "short": "Example Creek West", "date": "2026-10-17",
               "time": "3:30 PM", "price_per_golfer": 60.95, "players": 2, "max_total": 141.90,
               "expected_card": {"name": "Card A", "last4": "0000"}}

    def test_page_must_match_what_he_approved(self):
        import confirm_booking as cb
        import browser_helpers as bh
        page = bh.read_tee_time_page(FIXTURE_PAGE, LIVE_CONFIG["checkout"]["selectors"])
        self.assertEqual(cb.page_matches(self.PENDING, page), [])
        self.assertTrue(cb.page_matches(dict(self.PENDING, time="4:08 PM"), page))
        self.assertTrue(cb.page_matches(dict(self.PENDING, date="2026-10-18"), page))
        self.assertTrue(cb.page_matches(dict(self.PENDING, price_per_golfer=45.0), page))
        self.assertTrue(cb.page_matches(dict(self.PENDING, course="Cedar Hills Golf Club", short="Cedar Hills GC"), page))
        empty = dict(page, date="", time="", price=None)
        self.assertTrue(cb.page_matches(self.PENDING, empty))

    def test_total_and_card_guards(self):
        import confirm_booking as cb
        self.assertTrue(cb.total_ok(139.44, self.PENDING))
        self.assertFalse(cb.total_ok(150.00, self.PENDING))
        self.assertFalse(cb.total_ok(None, self.PENDING))
        card = self.PENDING["expected_card"]
        self.assertTrue(cb.card_ok(["0000"], card))
        self.assertFalse(cb.card_ok(["9999"], card))
        self.assertFalse(cb.card_ok(["0000", "9999"], card))
        self.assertFalse(cb.card_ok([], card))

    def test_dry_run_is_the_default_until_checkout_is_mapped(self):
        self.assertFalse(LIVE_CONFIG["checkout"]["mapped"],
                         "checkout.mapped is true: only flip it after the supervised walkthrough, and record the capture")

    def test_record_booking_writes_in_the_sandbox(self):
        import confirm_booking as cb
        p = cb.record_booking(dict(self.PENDING, course_id="example-creek-west", holes=18, rate_id=-1), "ABC123", 139.44)
        self.assertTrue(str(p).startswith(self.tmp.name))
        rows = json.loads(p.read_text())["bookings"]
        self.assertEqual(rows[0]["confirmation"], "ABC123")
        self.assertEqual(rows[0]["card"], "Card A")


class DealWatch(Sandbox):
    """The watcher: deal + window + bandwidth + cap, and it sends nothing unless enabled."""

    NOW = datetime(2026, 10, 12, 9, 50)

    def hot(self, when, price=38.0, rate_id=-1):
        return {"time": when, "time_label": when.strftime("%-I:%M %p"), "price": price, "holes": 18, "cart": True,
                "rate_name": "Hot Deal", "prepaid": True, "hot": price < 50, "trade": False, "rate_id": rate_id,
                "detail_url": f"https://www.golfnow.com/tee-times/facility/1001/tee-time/{rate_id}",
                "player_rule": "Any", "restricted": False, "course": "Example Creek Golf Club - West Course",
                "facility_id": 1001, "address": "100 Example St, Dallas, TX 75201", "phone": ""}

    def cfg_and_fake(self, slots_by_fid):
        import courses
        import golfnow
        cfg = courses.load()
        fake = dict(slots_by_fid)

        def fetch(fid, day, players, lat, lon):
            return {"_fake": [s for s in fake.get(fid, []) if s["time"].date() == day]}
        real = golfnow.slots
        golfnow.slots = lambda data: data["_fake"] if "_fake" in data else real(data)
        self.addCleanup(lambda: setattr(golfnow, "slots", real))
        return cfg, fetch

    def test_is_deal_and_window(self):
        import deal_watch as dw
        import courses
        cfg = courses.load()
        bc = courses.find(cfg, "example creek west")
        sat_am = datetime(2026, 10, 17, 8, 30)
        self.assertIn("vs the usual $61", dw.is_deal(bc, self.hot(sat_am, 38.0)))
        self.assertEqual(dw.is_deal(bc, self.hot(sat_am, 61.0)), "")
        self.assertIn("vs the usual", dw.is_deal(bc, self.hot(sat_am, 40.0)))
        self.assertTrue(dw.in_window(self.hot(sat_am), cfg["watch"]["windows"]))
        self.assertFalse(dw.in_window(self.hot(datetime(2026, 10, 17, 15, 30)), cfg["watch"]["windows"]))
        self.assertTrue(dw.in_window(self.hot(datetime(2026, 10, 14, 16, 0)), cfg["watch"]["windows"]))
        self.assertFalse(dw.in_window(self.hot(datetime(2026, 10, 14, 8, 0)), cfg["watch"]["windows"]))

    def test_scan_picks_the_best_free_deal_and_names_why_others_fail(self):
        import deal_watch as dw
        sat = datetime(2026, 10, 17, 8, 30)
        sun = datetime(2026, 10, 18, 9, 0)
        wed_pm = datetime(2026, 10, 14, 16, 0)
        cfg, fetch = self.cfg_and_fake({1001: [self.hot(sat, 38.0, -1), self.hot(sun, 30.0, -2), self.hot(wed_pm, 35.0, -3),
                                               self.hot(datetime(2026, 10, 17, 15, 30), 20.0, -4)]})
        events = {date(2026, 10, 18): [{"title": "Brunch with friends", "all_day": False,
                                         "start_iso": "2026-10-18T11:00:00-05:00", "end_iso": "2026-10-18T13:00:00-05:00"}],
                  date(2026, 10, 14): [{"title": "Austin", "all_day": True, "start_iso": "2026-10-13", "end_iso": "2026-10-16"}]}
        res = dw.scan(cfg, self.NOW, {"seen": {}, "sent": {}}, fetch=fetch,
                      read_events=lambda d: events.get(d, []), away=False)
        self.assertEqual([c["slot"]["rate_id"] for c in res["candidates"]], [-1])
        whys = {r.get("when", r.get("course")): r["why"] for r in res["rejected"]}
        self.assertIn("Brunch with friends", whys["Sun Oct 18 9:00 AM"])
        self.assertIn("Austin", whys["Wed Oct 14 4:00 PM"])
        self.assertEqual(whys["Sat Oct 17 3:30 PM"], "outside the tee-time window")

    def test_seen_notice_and_away_gates(self):
        import deal_watch as dw
        sat = datetime(2026, 10, 17, 8, 30)
        soon = datetime(2026, 10, 12, 11, 0)   # a Monday 11am: weekday window is 15-18, use a Tue 4pm instead
        tue = datetime(2026, 10, 13, 16, 0)
        cfg, fetch = self.cfg_and_fake({1001: [self.hot(sat, 38.0, -1), self.hot(tue, 38.0, -2)]})
        res = dw.scan(cfg, self.NOW, {"seen": {"example-creek-west|2026-10-17|-1": "x"}, "sent": {}},
                      fetch=fetch, read_events=lambda d: [], away=True)
        whys = {r["when"]: r["why"] for r in res["rejected"]}
        self.assertEqual(whys["Sat Oct 17 8:30 AM"], "already proposed")
        self.assertIn("away from home", whys["Tue Oct 13 4:00 PM"])
        self.assertIsNone(res["pick"])
        res = dw.scan(cfg, datetime(2026, 10, 13, 14, 0), {"seen": {}, "sent": {}},
                      fetch=fetch, read_events=lambda d: [], away=False)
        self.assertIn("too little notice", {r["when"]: r["why"] for r in res["rejected"]}["Tue Oct 13 4:00 PM"])

    def test_unreadable_calendar_is_not_bandwidth(self):
        import deal_watch as dw
        sat = datetime(2026, 10, 17, 8, 30)
        cfg, fetch = self.cfg_and_fake({1001: [self.hot(sat, 38.0, -1)]})

        def boom(d):
            raise RuntimeError("gog down")
        res = dw.scan(cfg, self.NOW, {"seen": {}, "sent": {}}, fetch=fetch, read_events=boom, away=False)
        self.assertIsNone(res["pick"])
        self.assertIn("calendar unreadable", res["rejected"][0]["why"])

    def test_main_sends_nothing_while_disabled_and_once_when_enabled(self):
        import contextlib
        import io
        import deal_watch as dw
        import propose_booking as pb
        sat = datetime(2026, 10, 17, 8, 30)
        sun = datetime(2026, 10, 18, 9, 0)
        cfg, fetch = self.cfg_and_fake({1001: [self.hot(sat, 38.0, -1), self.hot(sun, 45.0, -2)], 1002: [], 1003: []})
        dw.golfnow.fetch = fetch
        import sunset_golf as sg
        sg._calendar_events = lambda d: []
        dw.scan.__globals__["golfnow"].fetch = fetch
        sent = []
        pb.send_proposal = lambda text: sent.append(text) or 777
        # disabled in config -> prints the card, sends nothing, records nothing
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = dw.main(["--now", self.NOW.isoformat()])
        self.assertEqual(rc, 0)
        report = json.loads(out.getvalue())
        self.assertEqual(report["outcome"], "would send (dry run / watch disabled)")
        self.assertIn("Deal: GolfNow hot deal, $38 vs the usual $61", report["card"])
        self.assertIn("Saturday Oct 17 at 8:30 AM", report["card"])
        self.assertEqual(sent, [])
        self.assertFalse(self.dp.path("pending_golf_booking.json").exists())
        self.assertFalse(self.dp.path("deal_watch_state.json").exists())
        # enabled -> one card, state recorded, and the next scan the same day sends nothing
        cfg_path = self.dp.path("approved_courses.json")
        live_cfg = json.loads(cfg_path.read_text()); live_cfg["watch"]["enabled"] = True
        cfg_path.write_text(json.dumps(live_cfg))
        with contextlib.redirect_stdout(io.StringIO()):
            rc = dw.main(["--now", self.NOW.isoformat()])
        self.assertEqual(rc, 0)
        self.assertEqual(len(sent), 1)
        self.assertIn("Deal: GolfNow hot deal, $38 vs the usual $61", sent[0])
        pending = json.loads(self.dp.path("pending_golf_booking.json").read_text())
        self.assertEqual(pending["source_tg_msg_id"], 777)
        state = json.loads(self.dp.path("deal_watch_state.json").read_text())
        self.assertIn("example-creek-west|2026-10-17|-1", state["seen"])
        self.assertEqual(state["sent"]["2026-10-12"], 1)
        # the afternoon scan the same day: the Saturday slot is now "already proposed", the
        # Sunday one still qualifies, and the daily cap holds it back - nothing else is sent
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            dw.main(["--now", (self.NOW + timedelta(hours=6)).isoformat()])
        report = json.loads(out.getvalue())
        self.assertEqual(report["outcome"], "cap reached today, card not sent")
        self.assertEqual([c["when"] for c in report["candidates"]], ["Sun Oct 18 9:00 AM"])
        self.assertIn("already proposed", [r["why"] for r in report["rejected"]])
        self.assertEqual(len(sent), 1)


if __name__ == "__main__":
    unittest.main(verbosity=1)
