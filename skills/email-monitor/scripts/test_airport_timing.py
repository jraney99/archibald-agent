#!/usr/bin/env python3
"""
Tests for the config-resolution layer (airport_timing.py) and the feedback
loop (log_flight.py). No network; filesystem writes are redirected to temp
files. Run:  python3 test_airport_timing.py
"""
import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

_DIR = Path(__file__).parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


at = _load("airport_timing")
lf = _load("log_flight")

CFG = at.load_cfg()


class Resolution(unittest.TestCase):
    def test_terminal_by_airline(self):
        self.assertEqual(at.resolve_terminal(CFG, "WN", "DAL"), "MAIN")
        self.assertEqual(at.resolve_terminal(CFG, "AA", "DFW"), "A")

    def test_terminal_unknown_airport_blank(self):
        self.assertEqual(at.resolve_terminal(CFG, "WN", "ZZZ"), "")

    def test_walk_seed_is_estimated(self):
        walk, est = at.resolve_walk(CFG, "DAL", "MAIN", learned={})
        self.assertEqual(walk, 10)
        self.assertTrue(est)

    def test_walk_learned_overrides_and_is_not_estimated(self):
        learned = {"walk_minutes": {"DAL": {"MAIN": 8}}}
        walk, est = at.resolve_walk(CFG, "DAL", "MAIN", learned=learned)
        self.assertEqual(walk, 8)
        self.assertFalse(est)

    def test_security_bucket_split(self):
        early = datetime.fromisoformat("2026-09-15T07:00:00-05:00")
        mid = datetime.fromisoformat("2026-09-15T13:00:00-05:00")
        self.assertEqual(at.resolve_security(CFG, "DAL", early, learned={})[0], 20)
        self.assertEqual(at.resolve_security(CFG, "DAL", mid, learned={})[0], 14)

    def test_security_learned_overrides_seed(self):
        learned = {"security_p85": {"DAL": {"default": 9}}}
        mid = datetime.fromisoformat("2026-09-15T13:00:00-05:00")
        self.assertEqual(at.resolve_security(CFG, "DAL", mid, learned=learned)[0], 9)

    def test_gate_lead_southwest_shorter(self):
        self.assertEqual(at.resolve_gate_lead(CFG, "WN"), 10)
        self.assertEqual(at.resolve_gate_lead(CFG, "AA"), 15)

    def test_build_inputs_end_to_end(self):
        dep = datetime.fromisoformat("2026-09-15T13:40:00-05:00")
        r = at.leave_time_for_flight(
            cfg=CFG, flight_number="WN 100", orig="DAL", dest="DEN",
            dep_dt=dep, drive_minutes=22, drive_source="live",
        )
        self.assertEqual(r["terminal"], "MAIN")
        self.assertEqual(r["breakdown"]["gate_close_lead"], 10)
        self.assertLess(r["breakdown"]["total_minutes_before_departure"], 120)


class FeedbackLoop(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.hist = d / "flight_history.jsonl"
        self.learned = d / "flight_learned.json"
        self.pending = d / "pending_flight_log.json"
        lf.HISTORY_FILE = self.hist
        lf.LEARNED_FILE = self.learned
        lf.PENDING = self.pending
        lf.send_tg = lambda *a, **k: None
        self._ctx = {
            "key": "WN100-20260915", "flight": "WN 100", "origin": "DAL",
            "dest": "DEN", "terminal": "MAIN",
            "departure": "2026-09-15T13:40:00-05:00", "departure_hhmm": "13:40",
            "predicted_leave_display": "12:22 PM",
        }

    def tearDown(self):
        self.tmp.cleanup()

    def _reply(self, text, ctx=None):
        self.pending.write_text(json.dumps(ctx or self._ctx))
        lf.handle_reply(text)

    def test_two_numbers_appended_and_pending_cleared(self):
        self._reply("8 22")
        rows = [json.loads(l) for l in self.hist.read_text().splitlines() if l.strip()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["actual_security_min"], 8)
        self.assertEqual(rows[0]["gate_idle_min"], 22)
        self.assertFalse(self.pending.exists())

    def test_messy_reply_still_parses(self):
        self._reply("security was 12, gate 30")
        rows = [json.loads(l) for l in self.hist.read_text().splitlines() if l.strip()]
        self.assertEqual((rows[0]["actual_security_min"], rows[0]["gate_idle_min"]), (12, 30))

    def test_skip_clears_without_logging(self):
        self._reply("skip")
        self.assertFalse(self.pending.exists())
        self.assertFalse(self.hist.exists() and self.hist.read_text().strip())

    def test_security_p85_learns_after_three(self):
        for s in (10, 6, 14):
            self._reply(f"{s} 20")
        learned = json.loads(self.learned.read_text())
        self.assertIn("default", learned["security_p85"]["DAL"])
        self.assertEqual(len(learned["security_samples"]["DAL"]["default"]), 3)

    def test_no_p85_before_three_samples(self):
        self._reply("10 20")
        learned = json.loads(self.learned.read_text())
        self.assertNotIn("security_p85", learned)

    def test_buffer_suggestion_fires_on_high_idle(self):
        for _ in range(5):
            self._reply("8 40")
        learned = json.loads(self.learned.read_text())
        self.assertIn("buffer_suggestion", learned)
        self.assertEqual(learned["buffer_suggestion"]["to"], 10)

    def test_buffer_suggestion_suppressed_by_a_tight_flight(self):
        for idle in (40, 40, 40, 40, 3):
            self._reply(f"8 {idle}")
        learned = json.loads(self.learned.read_text())
        self.assertNotIn("buffer_suggestion", learned)

    def test_apply_buffer_writes_override(self):
        self.learned.write_text(json.dumps({"buffer_suggestion": {"to": 10, "from": 20}}))
        lf.apply_buffer(10)
        self.assertEqual(json.loads(self.learned.read_text())["buffer_minutes"], 10)


class SchedulerWiring(unittest.TestCase):
    """daemon._schedule_flight_reminders writes all five cron jobs plus the
    leave-for-airport calendar block, for a future flight."""

    def setUp(self):
        self.daemon = _load("daemon")
        self.written = []
        self.daemon._write_job = lambda job: self.written.append(job)
        self.daemon._tomtom_drive_minutes = lambda *a, **k: 22
        self.daemon._log = lambda *a, **k: None
        self.daemon._load = lambda: {}
        self.daemon._save = lambda s: None
        self.daemon._cancel_stale_flight_reminders = lambda *a, **k: None

        class _R:
            returncode = 0
            stdout = '{"event": {"id": "evt123"}}'
            stderr = ""
        self.daemon.subprocess.run = lambda *a, **k: _R()

        future = (datetime.now().astimezone() + timedelta(days=10)).replace(microsecond=0)
        self.dep_iso = future.isoformat()
        self.spec = {
            "type": "flight",
            "from": self.dep_iso,
            "to": (future + timedelta(hours=3)).isoformat(),
            "description": "DAL → DEN | Confirmation: ABC123",
            "_flight_number": "WN 100",
            "_dep_display": future.strftime("%-I:%M %p"),
            "_orig": "DAL", "_dest": "DEN", "_orig_tz": "America/Chicago",
        }

    def test_all_five_jobs_scheduled(self):
        self.daemon._schedule_flight_reminders(self.spec)
        names = {j["name"].split("-")[0] for j in self.written}
        self.assertEqual(names, {"checkin", "dayof", "recheck", "flightlog", "traveldetect"})

    def test_script_jobs_are_command_payloads_not_announced(self):
        self.daemon._schedule_flight_reminders(self.spec)
        for j in self.written:
            if j["name"].startswith(("checkin-", "dayof-", "recheck-", "flightlog-", "traveldetect-")):
                self.assertEqual(j["payload"]["kind"], "command")
                self.assertEqual(j["delivery"]["mode"], "none")

    def test_traveldetect_scheduled_after_landing_not_departure(self):
        self.daemon._schedule_flight_reminders(self.spec)
        td = next(j for j in self.written if j["name"].startswith("traveldetect-"))
        arr_dt = datetime.fromisoformat(self.spec["to"])
        scheduled_at = datetime.fromisoformat(td["schedule"]["at"].replace("Z", "+00:00"))
        # Landing + 30m, not departure + 30m — a 3h flight landing 30m late
        # should schedule ~3h30m after departure, not 30m after it.
        self.assertGreater(scheduled_at, arr_dt)
        self.assertLess(scheduled_at, arr_dt + timedelta(hours=1))
        self.assertIn("detect_travel.py", td["payload"]["argv"][-1])

    def test_recheck_command_has_recheck_flag(self):
        self.daemon._schedule_flight_reminders(self.spec)
        recheck = next(j for j in self.written if j["name"].startswith("recheck-"))
        self.assertIn("--recheck", recheck["payload"]["argv"])

    def test_flightlog_scheduled_with_confirmation_for_stale_match(self):
        self.daemon._schedule_flight_reminders(self.spec)
        fl = next(j for j in self.written if j["name"].startswith("flightlog-"))
        self.assertIn("prompt_flight_log.py", fl["payload"]["argv"][1])
        self.assertIn("ABC123", fl["payload"]["argv"])


class RecheckDirection(unittest.TestCase):
    """travel_reminder.py T-90m re-check: quiet on small moves, correct
    earlier/later wording on big ones."""

    def setUp(self):
        self.tr = _load("travel_reminder")
        self.tmp = tempfile.TemporaryDirectory()
        self.tr.STATE_FILE = Path(self.tmp.name) / "state.json"
        self.tr.STATE_FILE.write_text("{}")
        self.sent = []
        self.tr.send_tg = lambda t: self.sent.append(t)
        self.tr.get_security_p85 = lambda *a, **k: (14, "static")   # no live network in tests
        self.base = [
            "x", "--flight", "WN 100", "--origin", "DAL", "--dest", "DEN",
            "--departure", "2026-09-15T13:40:00-05:00", "--confirmation", "ABC123",
            "--airport-lat", "32.8481", "--airport-lng", "-96.8512",
            "--home-lat", "40.0000", "--home-lng", "-75.0000",
            "--airport-name", "Dallas Love Field",
        ]

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, argv, drive):
        import sys
        self.tr.get_drive_minutes = lambda *a, **k: drive
        sys.argv = argv
        self.tr.main()

    def test_small_move_stays_quiet(self):
        self._run(self.base, 24)
        n = len(self.sent)
        self._run(self.base + ["--recheck"], 27)
        self.assertEqual(len(self.sent), n)

    def test_worse_traffic_says_earlier(self):
        self._run(self.base, 24)
        self._run(self.base + ["--recheck"], 70)
        self.assertIn("earlier", self.sent[-1])
        self.assertIn("Plans changed", self.sent[-1])

    def test_better_traffic_says_later(self):
        self._run([*self.base], 70)
        self._run(self.base + ["--recheck"], 20)
        self.assertIn("later", self.sent[-1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
