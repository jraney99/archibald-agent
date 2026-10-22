#!/usr/bin/env python3
"""
Tests for compact_session.py (2026-09-30).

Two failures are being prevented, and they pull in opposite directions:

  - Compaction now runs every 6 hours, more often than the user talks, so "nothing
    new to compact" is the normal quiet-afternoon outcome. The CLI exits 1 for
    it, which raised a "cron job failed" alert and teaches him to ignore real ones.
  - But `sessions compact` returns {"ok": true, ..., "reason": "no sessionId"}
    with exit code 0 for a session key that does not exist. Trusting `ok` or the
    exit code would let a typo'd key report success forever while compaction
    silently never ran — invisible except as the cost creeping back.

So: success is decided by what happened, not by how the CLI exited.

    python3 test_compact_session.py
"""
import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SHARED = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("compact_session", SHARED / "compact_session.py")
cs = importlib.util.module_from_spec(spec)
sys.modules["compact_session"] = cs
spec.loader.exec_module(cs)


def cli(stdout, code=0):
    return subprocess.CompletedProcess([], code, stdout=json.dumps(stdout), stderr="")


class TestOutcome(unittest.TestCase):
    def run_with(self, completed):
        with patch.object(cs.subprocess, "run", return_value=completed), \
             patch.object(sys, "argv", ["compact_session.py"]):
            return cs.main()

    def test_a_real_compaction_passes(self):
        self.assertEqual(self.run_with(cli({"ok": True, "compacted": True})), 0)

    def test_nothing_new_to_compact_passes(self):
        # The quiet-afternoon case the CLI reports as exit 1.
        for reason in ("Already compacted", "Nothing to compact", "No prior history"):
            with self.subTest(reason=reason):
                self.assertEqual(
                    self.run_with(cli({"ok": False, "compacted": False, "reason": reason}, code=1)), 0)

    def test_reason_matching_ignores_case_and_spacing(self):
        self.assertEqual(
            self.run_with(cli({"ok": False, "compacted": False, "reason": "  ALREADY COMPACTED "}, 1)), 0)

    def test_a_session_key_that_does_not_exist_fails(self):
        # Exit 0 AND ok:true, but nothing was compacted — the silent-miss case.
        self.assertEqual(
            self.run_with(cli({"ok": True, "compacted": False, "reason": "no sessionId"}, code=0)), 1)

    def test_an_unrecognised_reason_fails(self):
        self.assertEqual(
            self.run_with(cli({"ok": False, "compacted": False, "reason": "session is locked"}, 1)), 1)

    def test_unparseable_output_fails(self):
        completed = subprocess.CompletedProcess([], 0, stdout="<html>gateway down</html>", stderr="")
        self.assertEqual(self.run_with(completed), 1)

    def test_empty_output_fails(self):
        self.assertEqual(self.run_with(subprocess.CompletedProcess([], 0, stdout="", stderr="")), 1)

    def test_it_compacts_the_live_telegram_session_by_default(self):
        with patch.object(cs.subprocess, "run", return_value=cli({"ok": True, "compacted": True})) as run, \
             patch.object(sys, "argv", ["compact_session.py"]):
            cs.main()
        self.assertIn("agent:main:telegram:direct:1000000001", run.call_args[0][0])


class TestSizeTrigger(unittest.TestCase):
    """--when-over N (2026-10-03): compact only when the live session's last prompt
    exceeded N tokens. Fixture agents root; the real transcripts are never read."""

    KEY = "agent:main:telegram:direct:1000000001"

    def setUp(self):
        import tempfile
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.sessions = Path(tmp.name) / "main" / "sessions"; self.sessions.mkdir(parents=True)
        (self.sessions / "sessions.json").write_text(json.dumps({self.KEY: {"sessionId": "abc"}}))
        p = patch.object(cs, "AGENTS_ROOT", Path(tmp.name)); p.start(); self.addCleanup(p.stop)

    def transcript(self, *usages):
        lines = [json.dumps({"type": "session", "id": "abc"})]
        for u in usages:
            lines.append(json.dumps({"timestamp": "2026-10-03T14:00:00Z",
                                     "message": {"role": "assistant", "model": "claude-sonnet-4-6", "usage": u}}))
            lines.append(json.dumps({"timestamp": "2026-10-03T14:00:01Z",
                                     "message": {"role": "assistant", "model": "delivery-mirror", "content": []}}))
        (self.sessions / "abc.jsonl").write_text("\n".join(lines) + "\n")

    def run_when_over(self, threshold, completed=None):
        with patch.object(cs.subprocess, "run", return_value=completed or cli({"ok": True, "compacted": True})) as run, \
             patch.object(sys, "argv", ["compact_session.py", "--when-over", str(threshold)]):
            code = cs.main()
        return code, run

    def test_the_last_prompt_size_is_input_plus_cache_read_plus_cache_write(self):
        self.transcript({"input": 5, "cacheRead": 32000, "cacheWrite": 1000},
                        {"input": 10, "cacheRead": 32133, "cacheWrite": 75310})
        self.assertEqual(cs.context_tokens("main", self.KEY), (107453, "ok"))

    def test_under_the_threshold_nothing_is_called(self):
        self.transcript({"input": 10, "cacheRead": 32133, "cacheWrite": 888})
        code, run = self.run_when_over(64000)
        self.assertEqual(code, 0); run.assert_not_called()

    def test_over_the_threshold_compacts(self):
        self.transcript({"input": 10, "cacheRead": 32133, "cacheWrite": 75310})
        code, run = self.run_when_over(64000)
        self.assertEqual(code, 0); run.assert_called_once()
        self.assertIn("sessions", run.call_args[0][0]); self.assertIn("compact", run.call_args[0][0])

    def test_a_turn_in_progress_is_left_alone(self):
        self.transcript({"input": 10, "cacheRead": 32133, "cacheWrite": 75310})
        (self.sessions / "abc.jsonl.lock").write_text("pid 1\n")
        code, run = self.run_when_over(64000)
        self.assertEqual(code, 0); run.assert_not_called()

    def test_a_fresh_session_with_no_calls_is_skipped(self):
        self.transcript()
        code, run = self.run_when_over(64000)
        self.assertEqual(code, 0); run.assert_not_called()

    def test_a_key_missing_from_the_index_fails_loudly(self):
        self.transcript({"input": 10, "cacheRead": 32133, "cacheWrite": 75310})
        with patch.object(cs.subprocess, "run") as run, \
             patch.object(sys, "argv", ["compact_session.py", "--when-over", "64000", "--key", "agent:main:typo"]):
            self.assertEqual(cs.main(), 1)
        run.assert_not_called()

    def test_nothing_new_to_compact_is_still_a_quiet_success(self):
        self.transcript({"input": 10, "cacheRead": 32133, "cacheWrite": 75310})
        code, _ = self.run_when_over(64000, cli({"ok": False, "compacted": False, "reason": "Already compacted"}, 1))
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
