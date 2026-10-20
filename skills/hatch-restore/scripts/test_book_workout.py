#!/usr/bin/env python3
"""
Regression guard for the 2026-08-28 revert: nightly_wake_and_workout_sync.py
must book tomorrow's workout INDEPENDENTLY of the computed wake time — i.e.
it must NOT pass --preferred-start to schedule_workouts.py. Feeding it
`wake + buffer` meant a commitment-driven early wake jammed a workout into
the gap before the commitment (see feedback-workout-wake-buffer).

No network — subprocess.run is mocked.

    python3 test_book_workout.py
"""
import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_SCRIPTS_DIR = Path(__file__).parent


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


nightly = _load("nightly_wake_and_workout_sync", _SCRIPTS_DIR / "nightly_wake_and_workout_sync.py")


class TestBookWorkoutIsWakeIndependent(unittest.TestCase):
    def test_no_preferred_start_flag_and_no_wake_args(self):
        captured = {}

        def fake_run(cmd, *a, **k):
            captured["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"status": "success", "booked": []}), stderr="")

        # book_workout() takes no arguments at all now.
        with patch.object(nightly.subprocess, "run", side_effect=fake_run):
            result = nightly.book_workout()

        cmd = captured["cmd"]
        self.assertIn(str(nightly.SCHEDULE_WORKOUTS), cmd)
        self.assertIn("--date", cmd)
        self.assertEqual(cmd[cmd.index("--date") + 1], "tomorrow")
        self.assertNotIn("--preferred-start", cmd)
        self.assertEqual(result["status"], "success")

    def test_helpers_removed(self):
        # These fed the wake time into the booking and are gone for good.
        self.assertFalse(hasattr(nightly, "get_wake_buffer_minutes"))
        self.assertFalse(hasattr(nightly, "get_active_transit_minutes"))


class TestMisfireGuard(unittest.TestCase):
    """A gateway-restart catch-up run outside the evening slot must no-op —
    no Hatch write, no workout booking, no Telegram (see 2026-08-30: a
    skipped 9pm run fired at 4:35pm CT and sent an afternoon alarm brief)."""

    def _run_main_at(self, hour):
        from datetime import datetime
        from misfire_guard import within_slot as real_within_slot
        now = datetime(2026, 8, 30, hour, 0, tzinfo=nightly.get_tz())
        # Pin the guard's clock; anything past it raises so we can prove
        # main() bailed before doing real work.
        with patch.object(nightly, "within_slot",
                          side_effect=lambda wd, lo, hi: real_within_slot(wd, lo, hi, now=now)), \
             patch.object(nightly.sync_alarm, "load_config", side_effect=AssertionError("ran past guard")), \
             patch.object(nightly.subprocess, "run", side_effect=AssertionError("ran past guard")), \
             patch.object(nightly, "notify", side_effect=AssertionError("sent past guard")):
            nightly.main()

    def test_afternoon_catchup_noops(self):
        # 16:00 — the real failure time. Must return cleanly, touching nothing.
        self._run_main_at(16)

    def test_after_midnight_noops(self):
        self._run_main_at(0)

    def test_evening_slot_runs(self):
        from datetime import datetime
        from misfire_guard import within_slot as real_within_slot
        now = datetime(2026, 8, 30, 21, 0, tzinfo=nightly.get_tz())
        with patch.object(nightly, "within_slot",
                          side_effect=lambda wd, lo, hi: real_within_slot(wd, lo, hi, now=now)), \
             patch.object(nightly.sync_alarm, "load_config", side_effect=RuntimeError("reached body")):
            with self.assertRaises(RuntimeError):
                nightly.main()


if __name__ == "__main__":
    unittest.main()
