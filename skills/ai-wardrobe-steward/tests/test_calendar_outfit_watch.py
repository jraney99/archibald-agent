#!/usr/bin/env python3
"""
Tests for the outfit watcher's state handling (2026-09-29).

The user got the same "👔 Tomorrow: Acme Co <> Alex Example (8:30 AM)" line five
times in one evening. The watcher runs every 15 minutes; the stylist call had
started failing (it asked for a model the config no longer allowed) and raised
before the single state write at the bottom of main(). So `evening_header_sent`
— set the moment the header was actually DELIVERED — was thrown away on every
run, and the next run had no memory of it.

The invariant these tests hold: a message that has reached him is never sent
twice, no matter what fails afterwards; and a board whose stylist call failed
stays eligible for a retry.

No network, no agent calls, no writes outside a temp directory.

    python3 test_calendar_outfit_watch.py
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))


def _load():
    spec = importlib.util.spec_from_file_location("calendar_outfit_watch",
                                                 SCRIPTS / "calendar_outfit_watch.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["calendar_outfit_watch"] = mod
    spec.loader.exec_module(mod)
    return mod


w = _load()

BRIEF = {"event_key": "k1", "top_outfits": [1, 2, 3],
         "event": {"title": "Acme Co <> Alex Example @ Corner Market",
                   "start_iso": "2026-09-30T08:30:00-05:00"}}
BRIEFS_OK = json.dumps({"briefs": [BRIEF], "needs_answer": []})


class WatcherRun(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.sends = []
        for attr, value in (("STATE", self.tmp / "state.json"),
                            ("PAYLOAD", self.tmp / "payload.json"),
                            ("PROPOSALS", self.tmp / "proposals.json")):
            p = patch.object(w, attr, value)
            p.start()
            self.addCleanup(p.stop)

    def run_watcher(self, stylist_rc=0, now="2026-09-29T20:00:00-05:00"):
        """One cron tick. stylist_rc=1 reproduces the rejected-model failure."""
        from datetime import datetime
        fake_now = datetime.fromisoformat(now)
        stub = type(sys)("send_outfit_proposal")
        stub.api = lambda method, payload: (self.sends.append(payload["text"]), {"ok": True})[1]
        with patch.object(w, "stylist_model", return_value="anthropic/claude-sonnet-4-6"), \
             patch.object(w, "now_local", return_value=fake_now), \
             patch.object(w.outfit_questions, "ask", return_value=None), \
             patch.object(sys, "argv", ["calendar_outfit_watch.py"]), \
             patch.dict("sys.modules", {"send_outfit_proposal": stub}), \
             patch.object(w.subprocess, "run") as mock_run:
            mock_run.side_effect = [
                subprocess.CompletedProcess([], 0, stdout=BRIEFS_OK),
                subprocess.CompletedProcess([], stylist_rc, stdout="",
                                            stderr="Model override not allowed for agent"),
            ]
            try:
                w.main()
            except SystemExit:
                pass

    def state(self):
        return json.loads(w.STATE.read_text()) if w.STATE.exists() else {}


class TestHeaderIsSentOnce(WatcherRun):
    def test_a_delivered_header_is_remembered_even_when_the_stylist_fails(self):
        self.run_watcher(stylist_rc=1)
        self.assertEqual(self.state().get("evening_header_sent"), "2026-09-29")

    def test_five_failing_runs_still_send_one_header(self):
        # The actual bug: 15-minute cron × repeated failure = five identical messages.
        for _ in range(5):
            self.run_watcher(stylist_rc=1)
        self.assertEqual(self.sends, ["👔 Tomorrow: Acme Co <> Alex Example (8:30 AM)"])

    def test_a_successful_run_also_sends_one(self):
        for _ in range(3):
            self.run_watcher(stylist_rc=0)
        self.assertEqual(len(self.sends), 1)


class TestRetryEligibility(WatcherRun):
    def test_a_failed_board_is_not_marked_dispatched(self):
        self.run_watcher(stylist_rc=1)
        self.assertEqual(self.state().get("dispatched"), [])

    def test_a_sent_board_is_marked_dispatched(self):
        self.run_watcher(stylist_rc=0)
        self.assertEqual(self.state().get("dispatched"), ["k1"])

    def test_state_is_written_even_on_failure(self):
        self.run_watcher(stylist_rc=1)
        self.assertIn("last_scan_at", self.state())


class TestModelResolution(unittest.TestCase):
    """A pinned model name is what broke this; resolution must follow the config."""

    def resolve(self, defaults):
        cfg = json.dumps({"agents": {"defaults": defaults}})
        with patch.object(Path, "read_text", return_value=cfg):
            return w.stylist_model()

    def test_the_configured_primary_is_used(self):
        self.assertEqual(self.resolve({
            "models": {"anthropic/claude-haiku-4-5-20251001": {}, "anthropic/claude-sonnet-4-6": {}},
            "model": {"primary": "anthropic/claude-sonnet-4-6"}}), "anthropic/claude-sonnet-4-6")

    def test_a_haiku_primary_is_upgraded_to_an_allowed_stronger_model(self):
        # The user's 2026-09-20 choice was "stronger than the default", not one name.
        self.assertEqual(self.resolve({
            "models": {"anthropic/claude-haiku-4-5-20251001": {}, "anthropic/claude-sonnet-5": {}},
            "model": {"primary": "anthropic/claude-haiku-4-5-20251001"}}), "anthropic/claude-sonnet-5")

    def test_a_primary_outside_the_allowlist_is_not_used(self):
        self.assertEqual(self.resolve({
            "models": {"anthropic/claude-sonnet-4-6": {}},
            "model": {"primary": "anthropic/claude-sonnet-5"}}), "anthropic/claude-sonnet-4-6")

    def test_haiku_only_means_no_override_rather_than_a_dead_run(self):
        self.assertIsNone(self.resolve({
            "models": {"anthropic/claude-haiku-4-5-20251001": {}},
            "model": {"primary": "anthropic/claude-haiku-4-5-20251001"}}))

    def test_an_unreadable_config_means_no_override_rather_than_a_crash(self):
        with patch.object(Path, "read_text", side_effect=OSError("gone")):
            self.assertIsNone(w.stylist_model())


if __name__ == "__main__":
    unittest.main(verbosity=2)
