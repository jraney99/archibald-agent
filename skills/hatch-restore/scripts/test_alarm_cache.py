#!/usr/bin/env python3
"""The shared alarm-time cache (alarm_cache.py, 2026-10-06).

The user changed his alarm to 8:30 as a deliberate blast-radius test. It found this: alarm.py
changes the alarm and then runs four followers, each of which read the alarm back for itself,
so one change made six Hatch calls in a row and the LAST follower got HTTP 429 — the morning
briefing gave up and left tomorrow's an hour early, at the old wake time.

What must hold:
  1. A fresh note is served without touching the Hatch at all.
  2. A cached "the alarm is off" is a real answer, not a cache miss.
  3. A stale (or future-dated, or corrupt) cache is ignored — it can never pin a wake time
     The user has since changed in the app.
  4. read_current_wake_time still reads the device when there is nothing fresh, and records
     what it read for whoever comes next.

    python3 test_alarm_cache.py
"""
import importlib.util
import json
import sys
import tempfile
import unittest
import shutil
from datetime import datetime, time as dtime, timedelta
from pathlib import Path
from unittest.mock import patch

_SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPTS_DIR))
sys.path.insert(0, str(_SCRIPTS_DIR.parent.parent / "shared"))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


alarm_cache = _load("alarm_cache", _SCRIPTS_DIR / "alarm_cache.py")
sync_alarm = _load("sync_alarm", _SCRIPTS_DIR / "sync_alarm.py")


class TempCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = patch.object(alarm_cache, "CACHE", Path(self.tmp.name) / "hatch_alarm_cache.json")
        p.start()
        self.addCleanup(p.stop)

    def write(self, age_seconds, time_value):
        alarm_cache.CACHE.write_text(json.dumps({
            "at": datetime.now().timestamp() - age_seconds, "time": time_value}))


class Cache(TempCache):
    def test_a_fresh_note_comes_back(self):
        alarm_cache.note(dtime(8, 30))
        self.assertEqual(alarm_cache.fresh(), (dtime(8, 30), True))

    def test_an_alarm_that_is_off_is_a_real_cached_answer(self):
        alarm_cache.note(None)
        self.assertEqual(alarm_cache.fresh(), (None, True))

    def test_nothing_on_file_is_a_miss(self):
        self.assertEqual(alarm_cache.fresh(), (None, False))

    def test_a_stale_note_is_a_miss_so_an_app_change_is_never_hidden(self):
        self.write(alarm_cache.MAX_AGE_SECONDS + 1, "07:30")
        self.assertEqual(alarm_cache.fresh(), (None, False))

    def test_a_future_dated_note_is_a_miss_too(self):
        self.write(-(alarm_cache.MAX_AGE_SECONDS + 1), "07:30")
        self.assertEqual(alarm_cache.fresh(), (None, False))

    def test_corrupt_notes_are_misses(self):
        for junk in ("", "{", '{"time": "07:30"}', '{"at": "soon", "time": "07:30"}',
                     '{"at": 0, "time": "half seven"}'):
            alarm_cache.CACHE.write_text(junk)
            self.assertEqual(alarm_cache.fresh(), (None, False), junk)

    def test_clear_leaves_no_note(self):
        alarm_cache.note(dtime(8, 30))
        alarm_cache.clear()
        self.assertEqual(alarm_cache.fresh(), (None, False))


class ReadThrough(TempCache):
    """read_current_wake_time: the followers' entry point."""

    def setUp(self):
        super().setUp()
        p = patch.object(sync_alarm, "alarm_cache", alarm_cache)
        p.start()
        self.addCleanup(p.stop)

    def test_a_fresh_cache_costs_no_hatch_call(self):
        alarm_cache.note(dtime(8, 30))
        with patch.object(sync_alarm, "_device_and_token", side_effect=AssertionError("called the Hatch")):
            self.assertEqual(sync_alarm.read_current_wake_time({}), dtime(8, 30))

    def test_use_cache_false_always_asks_the_device(self):
        alarm_cache.note(dtime(8, 30))
        with patch.object(sync_alarm, "_device_and_token", return_value=("mac", "token")), \
             patch.object(sync_alarm.alarm_api, "fetch_alarms", return_value=[{"id": 1, "enabled": True}]), \
             patch.object(sync_alarm, "_find_alarm", return_value={"id": 1, "enabled": True}), \
             patch.object(sync_alarm.alarm_api, "effective_wake_datetime",
                          return_value=datetime(2026, 10, 7, 7, 30)):
            self.assertEqual(sync_alarm.read_current_wake_time({}, use_cache=False), dtime(7, 30))

    def test_a_device_read_is_noted_for_the_next_follower(self):
        with patch.object(sync_alarm, "_device_and_token", return_value=("mac", "token")), \
             patch.object(sync_alarm.alarm_api, "fetch_alarms", return_value=[{"id": 1, "enabled": True}]), \
             patch.object(sync_alarm, "_find_alarm", return_value={"id": 1, "enabled": True}), \
             patch.object(sync_alarm.alarm_api, "effective_wake_datetime",
                          return_value=datetime(2026, 10, 7, 8, 30)):
            sync_alarm.read_current_wake_time({})
        self.assertEqual(alarm_cache.fresh(), (dtime(8, 30), True))

    def test_an_alarm_read_as_off_is_noted_as_off(self):
        with patch.object(sync_alarm, "_device_and_token", return_value=("mac", "token")), \
             patch.object(sync_alarm.alarm_api, "fetch_alarms", return_value=[{"id": 1, "enabled": False}]), \
             patch.object(sync_alarm, "_find_alarm", return_value={"id": 1, "enabled": False}):
            self.assertIsNone(sync_alarm.read_current_wake_time({}))
        self.assertEqual(alarm_cache.fresh(), (None, True))




class PlanCacheSharesOneHatchRead(unittest.TestCase):
    """2026-10-12: an alarm change runs the bedroom-sunrise follower and then the
    ChiliPad one, both through sync_bedroom_sunrise.read_plan(). That was two
    logins back to back and the ChiliPad's got HTTP 429 ("couldn't retimed —
    Hatch rate-limited on the second login"), leaving the bed at yesterday's
    timing. The second read must come from the cache."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig_cache = alarm_cache.CACHE
        alarm_cache.CACHE = Path(self._tmp) / "hatch_alarm_cache.json"

    def tearDown(self):
        alarm_cache.CACHE = self._orig_cache
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _plan(self):
        wake = datetime(2026, 10, 13, 8, 30)
        return {"enabled": True, "wake_dt": wake, "hatch_duration": 300,
                "ramp_duration": 450, "ramp_start_dt": wake - timedelta(seconds=450)}

    def test_a_wake_time_only_cache_is_a_miss(self):
        # note() writes just the time; the first follower must still do a real read.
        alarm_cache.note(dtime(8, 30))
        self.assertEqual(alarm_cache.fresh_plan(), (None, False))

    def test_round_trips_the_device_facts(self):
        alarm_cache.note_plan(self._plan())
        got, hit = alarm_cache.fresh_plan()
        self.assertTrue(hit)
        for k in ("enabled", "wake_dt", "hatch_duration"):
            self.assertEqual(got[k], self._plan()[k], k)

    def test_the_ramp_length_is_not_cached(self):
        # It is local config; caching it would serve a stale sunrise_minutes.
        alarm_cache.note_plan(self._plan())
        got, _ = alarm_cache.fresh_plan()
        self.assertNotIn("ramp_duration", got)

    def test_a_cache_hit_still_reflects_a_changed_ramp_length(self):
        import sync_bedroom_sunrise as sbs
        alarm_cache.note_plan(self._plan())
        with patch.object(sbs, "sunrise_seconds", return_value=900):
            plan = sbs.read_plan()
        self.assertEqual(plan["ramp_duration"], 900)
        self.assertEqual(plan["ramp_start_dt"], plan["wake_dt"] - timedelta(seconds=900))

    def test_a_set_alarm_leaves_followers_nothing_to_ask(self):
        """The whole point: alarm.py records what it just wrote, so read_plan()
        serves both followers without a single Hatch call (2026-10-13)."""
        import sync_bedroom_sunrise as sbs
        alarm_cache.note_plan(self._plan())          # stands in for alarm.py's write
        calls = []
        with patch.object(sbs.alarm_api, "login",
                          side_effect=lambda *a, **k: calls.append("login") or "tok"), \
             patch.object(sbs, "sunrise_seconds", return_value=450):
            sbs.read_plan()   # bedroom sunrise
            sbs.read_plan()   # ChiliPad
        self.assertEqual(calls, [], "a follower went to the Hatch after an alarm change")

    def test_a_plan_write_also_satisfies_plain_fresh(self):
        alarm_cache.note_plan(self._plan())
        self.assertEqual(alarm_cache.fresh(), (dtime(8, 30), True))

    def test_a_stale_plan_is_a_miss(self):
        alarm_cache.note_plan(self._plan())
        data = json.loads(alarm_cache.CACHE.read_text())
        data["at"] = datetime.now().timestamp() - (alarm_cache.MAX_AGE_SECONDS + 5)
        alarm_cache.CACHE.write_text(json.dumps(data))
        self.assertEqual(alarm_cache.fresh_plan(), (None, False))

    def test_the_second_follower_does_not_touch_the_hatch(self):
        import sync_bedroom_sunrise as sbs
        calls = []
        with patch.object(sbs.alarm_api, "login",
                          side_effect=lambda *a, **k: calls.append("login") or "tok"), \
             patch.object(sbs.alarm_api, "fetch_alarms",
                          return_value=[{"id": "1", "enabled": True}]), \
             patch.object(sbs, "effective_wake_datetime",
                          return_value=datetime(2026, 10, 13, 8, 30)), \
             patch.object(sbs, "_alarm_sunrise_duration_seconds", return_value=300), \
             patch.object(sbs, "sunrise_seconds", return_value=450), \
             patch.object(sbs, "HATCH_CONFIG", Path(self._tmp) / "cfg.json"), \
             patch.object(sbs, "load_device_map", return_value={"bed": {"mac": "mac"}}):
            (Path(self._tmp) / "cfg.json").write_text(json.dumps({"device": "bed", "alarm_id": "1"}))
            first = sbs.read_plan()           # bedroom sunrise follower
            second = sbs.read_plan()          # ChiliPad follower, seconds later
        self.assertEqual(calls, ["login"], "the ChiliPad follower logged in again")
        self.assertEqual(first["wake_dt"], second["wake_dt"])
        self.assertEqual(first["ramp_start_dt"], second["ramp_start_dt"])

    def test_use_cache_false_still_reaches_the_device(self):
        import sync_bedroom_sunrise as sbs
        alarm_cache.note_plan(self._plan())
        calls = []
        with patch.object(sbs.alarm_api, "login",
                          side_effect=lambda *a, **k: calls.append("login") or "tok"), \
             patch.object(sbs.alarm_api, "fetch_alarms",
                          return_value=[{"id": "1", "enabled": True}]), \
             patch.object(sbs, "effective_wake_datetime",
                          return_value=datetime(2026, 10, 13, 9, 0)), \
             patch.object(sbs, "_alarm_sunrise_duration_seconds", return_value=300), \
             patch.object(sbs, "sunrise_seconds", return_value=450), \
             patch.object(sbs, "HATCH_CONFIG", Path(self._tmp) / "cfg.json"), \
             patch.object(sbs, "load_device_map", return_value={"bed": {"mac": "mac"}}):
            (Path(self._tmp) / "cfg.json").write_text(json.dumps({"device": "bed", "alarm_id": "1"}))
            plan = sbs.read_plan(use_cache=False)
        self.assertEqual(calls, ["login"])
        self.assertEqual(plan["wake_dt"].hour, 9)



class NeverWritesTheLiveFileUnderTest(unittest.TestCase):
    """2026-10-12: read_plan() now records what it read, and test_sunrise_length.py
    drives read_plan() with a mocked device — which wrote a fabricated wake time
    into the LIVE cache, where a real follower would have believed it for up to
    MAX_AGE_SECONDS. Same guard chat_cleanup keeps on its own state file."""

    def test_the_live_file_is_refused_while_a_test_runs(self):
        live = alarm_cache._LIVE_CACHE
        before = live.read_text() if live.exists() else None
        alarm_cache.CACHE = live                      # exactly what a careless test leaves
        try:
            alarm_cache.note(dtime(3, 33))
            alarm_cache.note_plan({"enabled": True, "wake_dt": datetime(2026, 1, 1, 3, 33),
                                   "hatch_duration": 1, "ramp_duration": 1})
            self.assertEqual(alarm_cache.fresh(), (None, False))
            self.assertEqual(alarm_cache.fresh_plan(), (None, False))
        finally:
            after = live.read_text() if live.exists() else None
            alarm_cache.CACHE = live
        self.assertEqual(after, before, "a test wrote to the live alarm cache")

    def test_a_repointed_cache_still_works(self):
        tmp = tempfile.mkdtemp()
        orig = alarm_cache.CACHE
        alarm_cache.CACHE = Path(tmp) / "c.json"
        try:
            alarm_cache.note(dtime(8, 30))
            self.assertEqual(alarm_cache.fresh(), (dtime(8, 30), True))
        finally:
            alarm_cache.CACHE = orig
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
