#!/usr/bin/env python3
"""
Tests for the nightly putting habit (built 2026-09-27).

The three things that can quietly go wrong here are all covered: the
after-midnight session landing on the wrong night, a travel night breaking a
streak it was supposed to step over, and the cue sending twice (or at 3am).
No network — the Telegram send, the travel gate and the clock are patched, and
the log is redirected to a temp file so the tests never touch the live one.

    python3 test_putting.py
"""
import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS.parents[1] / "shared"))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


status = _load("putting_status", SCRIPTS / "putting_status.py")
cue = _load("send_putting_cue", SCRIPTS / "send_putting_cue.py")
logger = _load("log_putting", SCRIPTS / "log_putting.py")

TZ = ZoneInfo("America/Chicago")
SENT_OK = {"ok": True, "result": {"message_id": 5150}}


class _TempLog(unittest.TestCase):
    """Every writer and reader points at a temp log, so nothing here can touch
    the real one (guard-tests-read-live-state, 2026-09-24)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.log = root / "putting_log.json"
        self.state = root / "putting_cue_state.json"
        # The goal is read from config at call time. Tests pin their own copy (50, the
        # original goal) so the live config — 25 since 2026-10-03 — never leaks in
        # (guard-tests-read-live-state).
        self.config = root / "putting_config.json"
        self.config.write_text(json.dumps({"nightly_goal_putts": 50, "cue_time": "21:45",
                                           "skip_while_away": True, "night_rollover_hour": 4}))
        for target, attr, value in [
            (status, "LOG_PATH", self.log),
            (status, "LOCK_PATH", root / "putting_log.json.lock"),
            (status, "CONFIG_PATH", self.config),
            (cue, "STATE_PATH", self.state),
            (logger, "STATE_PATH", self.state),
        ]:
            p = patch.object(target, attr, value)
            p.start()
            self.addCleanup(p.stop)

    def write_log(self, entries):
        self.log.write_text(json.dumps(entries))

    def night(self, iso, status_str, putts=None):
        e = {"night_of": iso, "status": status_str, "timestamp": f"{iso}T21:50:00-05:00"}
        if putts is not None:
            e["putts"] = putts
        return e


class TestNightBoundary(_TempLog):
    def test_before_bed_belongs_to_that_night(self):
        self.assertEqual(status.night_of(datetime(2026, 9, 27, 22, 10, tzinfo=TZ)), date(2026, 9, 27))

    def test_after_midnight_still_belongs_to_the_night_before(self):
        self.assertEqual(status.night_of(datetime(2026, 9, 28, 0, 30, tzinfo=TZ)), date(2026, 9, 27))

    def test_the_rollover_hour_starts_the_new_night(self):
        self.assertEqual(status.night_of(datetime(2026, 9, 28, 4, 0, tzinfo=TZ)), date(2026, 9, 28))


class TestStreak(_TempLog):
    def test_counts_consecutive_nights(self):
        self.write_log([self.night(f"2026-09-2{d}", "done", 50) for d in (3, 4, 5, 6, 7)])
        self.assertEqual(status.streak_nights(status.load_entries(), date(2026, 9, 27)), 5)

    def test_a_skipped_night_breaks_it(self):
        self.write_log([self.night("2026-09-25", "done", 50), self.night("2026-09-26", "skipped"),
                        self.night("2026-09-27", "done", 50)])
        self.assertEqual(status.streak_nights(status.load_entries(), date(2026, 9, 27)), 1)

    def test_a_missing_night_breaks_it(self):
        self.write_log([self.night("2026-09-25", "done", 50), self.night("2026-09-27", "done", 50)])
        self.assertEqual(status.streak_nights(status.load_entries(), date(2026, 9, 27)), 1)

    def test_travel_nights_are_stepped_over_not_counted(self):
        # The user's choice 2026-09-27: a trip must not wipe out a long run.
        self.write_log([self.night("2026-09-23", "done", 50), self.night("2026-09-24", "done", 50),
                        self.night("2026-09-25", "away"), self.night("2026-09-26", "away"),
                        self.night("2026-09-27", "done", 50)])
        self.assertEqual(status.streak_nights(status.load_entries(), date(2026, 9, 27)), 3)

    def test_an_all_travel_log_terminates(self):
        self.write_log([self.night("2026-09-26", "away"), self.night("2026-09-27", "away")])
        self.assertEqual(status.streak_nights(status.load_entries(), date(2026, 9, 27)), 0)

    def test_empty_log_is_no_streak(self):
        self.assertEqual(status.streak_nights([], date(2026, 9, 27)), 0)

    def test_a_later_entry_for_a_night_wins(self):
        # The cue logged 'away' at 9:45, then he putted anyway and tapped Done.
        self.write_log([self.night("2026-09-27", "away"), self.night("2026-09-27", "done", 50)])
        self.assertEqual(status.status_for_night(status.load_entries(), date(2026, 9, 27)), "done")


class TestAppendEntry(_TempLog):
    def test_creates_the_log_and_its_folder(self):
        status.append_entry("done", night=date(2026, 9, 27), putts=50)
        self.assertEqual(status.load_entries()[0]["status"], "done")

    def test_the_same_tap_twice_logs_once(self):
        for _ in range(2):
            status.append_entry("done", night=date(2026, 9, 27), putts=50)
        self.assertEqual(len(status.load_entries()), 1)

    def test_a_real_change_of_mind_is_recorded(self):
        status.append_entry("away", night=date(2026, 9, 27))
        status.append_entry("done", night=date(2026, 9, 27), putts=50)
        self.assertEqual(len(status.load_entries()), 2)


class TestCue(_TempLog):
    def send_cue(self, argv=("send_putting_cue.py",), away=False, now=datetime(2026, 9, 27, 21, 45, tzinfo=TZ)):
        with patch.object(cue, "send", return_value=SENT_OK) as mock_send, \
             patch.object(cue, "is_away_from_home", return_value=away), \
             patch.object(cue, "near_slot", return_value=(True, "ok")), \
             patch.object(status, "night_of", return_value=now.date()), \
             patch.object(sys, "argv", list(argv)):
            cue.main()
        return mock_send

    def test_the_message_is_the_approved_wording(self):
        mock_send = self.send_cue()
        self.assertEqual(mock_send.call_args[0][0], "⛳ 50 putts before bed, Master Wayne.")

    def test_the_goal_comes_from_config_so_changing_it_changes_every_message(self):
        # 2026-10-03: The user lowered the goal to 25. One number in putting_config.json;
        # the cue, the done-edit and the logged count all follow it.
        self.config.write_text(json.dumps({"nightly_goal_putts": 25, "cue_time": "21:45",
                                           "skip_while_away": True, "night_rollover_hour": 4}))
        mock_send = self.send_cue()
        self.assertEqual(mock_send.call_args[0][0], "⛳ 25 putts before bed, Master Wayne.")
        self.assertEqual(status.goal_putts(), 25)

    def test_a_streak_is_shown(self):
        self.write_log([self.night(f"2026-09-2{d}", "done", 50) for d in (4, 5, 6)])
        mock_send = self.send_cue()
        self.assertEqual(mock_send.call_args[0][0],
                         "⛳ 50 putts before bed, Master Wayne.\n\n🔥 3 nights running.")

    def test_one_night_is_singular(self):
        self.write_log([self.night("2026-09-26", "done", 50)])
        self.assertIn("1 night running", self.send_cue().call_args[0][0])

    def test_tonights_own_session_is_not_counted_in_the_streak_it_shows(self):
        self.write_log([self.night("2026-09-26", "done", 50), self.night("2026-09-27", "done", 50)])
        # Already done tonight -> no cue at all, rather than a cue claiming 2.
        self.send_cue().assert_not_called()

    def test_travelling_sends_nothing_and_logs_the_night_away(self):
        self.send_cue(away=True).assert_not_called()
        self.assertEqual(status.status_for_night(status.load_entries(), date(2026, 9, 27)), "away")

    def test_a_misfire_sends_nothing(self):
        with patch.object(cue, "send") as mock_send, \
             patch.object(cue, "near_slot", return_value=(False, "fired 6h before its slot")), \
             patch.object(sys, "argv", ["send_putting_cue.py"]):
            cue.main()
        mock_send.assert_not_called()

    def test_dry_run_prints_the_text_and_sends_nothing(self):
        with patch.object(cue, "send") as mock_send, \
             patch.object(sys, "argv", ["send_putting_cue.py", "--dry-run"]):
            cue.main()
        mock_send.assert_not_called()

    def test_the_cue_records_the_message_id_for_the_edit(self):
        self.send_cue()
        self.assertEqual(json.loads(self.state.read_text())["message_id"], 5150)


class TestButtons(_TempLog):
    def tap(self, argv, night=date(2026, 9, 27), edit_ok=True):
        with patch.object(logger, "edit_message_text", return_value=edit_ok) as mock_edit, \
             patch.object(status, "night_of", return_value=night), \
             patch.object(logger.status, "night_of", return_value=night), \
             patch.object(sys, "argv", ["log_putting.py"] + argv):
            logger.main()
        return mock_edit

    def arm_cue(self, night="2026-09-27"):
        self.state.write_text(json.dumps({"message_id": 5150, "text": "cue", "night_of": night}))

    def test_done_logs_the_goal_and_edits_the_cue_in_place(self):
        self.arm_cue()
        mock_edit = self.tap(["--done"])
        self.assertEqual(status.putts_for_night(status.load_entries(), date(2026, 9, 27)), 50)
        self.assertEqual(mock_edit.call_args[0][1], "⛳ 50 putts logged. 1 night running.")

    def test_done_reports_the_running_streak(self):
        self.write_log([self.night(f"2026-09-2{d}", "done", 50) for d in (5, 6)])
        self.arm_cue()
        self.assertIn("3 nights running", self.tap(["--done"]).call_args[0][1])

    def test_a_partial_session_is_recorded_honestly(self):
        self.arm_cue()
        self.tap(["--done", "--putts", "30"])
        self.assertEqual(status.putts_for_night(status.load_entries(), date(2026, 9, 27)), 30)

    def test_skip_states_the_cost_once_without_a_lecture(self):
        self.write_log([self.night(f"2026-09-2{d}", "done", 50) for d in (5, 6)])
        self.arm_cue()
        text = self.tap(["--skip"]).call_args[0][1]
        self.assertEqual(text, "⛳ Noted, no putting tonight. Streak was 2.")
        self.assertEqual(status.status_for_night(status.load_entries(), date(2026, 9, 27)), "skipped")

    def test_skip_with_no_streak_says_nothing_about_one(self):
        self.arm_cue()
        self.assertEqual(self.tap(["--skip"]).call_args[0][1], "⛳ Noted, no putting tonight.")

    def test_a_stale_cue_from_an_earlier_night_is_never_edited(self):
        self.arm_cue(night="2026-09-20")
        self.tap(["--done"]).assert_not_called()

    def test_a_failed_edit_falls_back_to_a_plain_reply(self):
        self.arm_cue()
        with patch("builtins.print") as mock_print:
            self.tap(["--done"], edit_ok=False)
        self.assertNotIn("NO_REPLY", [c[0][0] for c in mock_print.call_args_list])


class TestMorningBrief(_TempLog):
    def brief(self, now=datetime(2026, 9, 28, 7, 45, tzinfo=TZ)):
        with patch.object(status, "night_of", return_value=now.date()):
            return status.brief_morning()

    def test_silent_until_the_habit_has_started(self):
        self.assertEqual(self.brief(), "")

    def test_reports_last_night_and_the_streak(self):
        self.write_log([self.night("2026-09-26", "done", 50), self.night("2026-09-27", "done", 50)])
        self.assertEqual(self.brief(), "⛳ Putting: 50 putts last night ✅ — 2 nights running")

    def test_a_miss_reports_the_streak_it_cost(self):
        self.write_log([self.night("2026-09-25", "done", 50), self.night("2026-09-26", "done", 50)])
        self.assertEqual(self.brief(), "⛳ Putting: nothing logged last night — streak was 2, last done Sat Sep 26")

    def test_a_deliberate_skip_is_worded_as_one(self):
        self.write_log([self.night("2026-09-26", "done", 50), self.night("2026-09-27", "skipped")])
        self.assertIn("skipped last night", self.brief())

    def test_travel_nights_are_silent(self):
        self.write_log([self.night("2026-09-26", "done", 50), self.night("2026-09-27", "away")])
        self.assertEqual(self.brief(), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
