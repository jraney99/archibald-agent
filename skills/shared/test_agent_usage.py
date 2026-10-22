#!/usr/bin/env python3
"""agent_usage.summarize reads transcripts only — never OpenClaw's transient session
lock or the trajectory file (2026-10-03: a `.jsonl.lock` written mid-turn was parsed
as a transcript and reported to the user as a usage read error). Fixture root; no live state.

    python3 test_agent_usage.py
"""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_usage  # noqa: E402

NOW = datetime(2026, 10, 3, 14, 0, tzinfo=timezone.utc)


def record(ts, usage, model="claude-sonnet-4-6"):
    return json.dumps({"id": "r" + ts.isoformat(), "timestamp": ts.isoformat().replace("+00:00", "Z"),
                       "message": {"role": "assistant", "model": model, "usage": usage}})


class Summarize(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.sessions = self.root / "agents/main/sessions"; self.sessions.mkdir(parents=True)
        usage = {"input": 10, "output": 20, "cacheRead": 30000, "cacheWrite": 400}
        (self.sessions / "abc.jsonl").write_text(
            json.dumps({"type": "session", "id": "abc"}) + "\n" + record(NOW - timedelta(hours=1), usage) + "\n")

    def test_a_session_write_lock_and_the_trajectory_are_not_transcripts(self):
        (self.sessions / "abc.jsonl.lock").write_text("pid 79364\n")              # OpenClaw's transient lock
        (self.sessions / "abc.trajectory.jsonl").write_text('{"sessionKey": "agent:main:telegram"}\n')
        report = agent_usage.summarize(NOW - timedelta(days=1), root=self.root)
        self.assertEqual(report["read_errors"], [])
        self.assertEqual(sum(g["calls"] for g in report["groups"].values()), 1)

    def test_reset_and_deleted_archives_still_count(self):
        # A daily reset moves yesterday's whole transcript into `<id>.jsonl.reset.<ts>`;
        # that is where most of a day's spend lives, so it must stay in the total.
        usage = {"input": 10, "output": 20, "cacheRead": 90000, "cacheWrite": 100000}
        (self.sessions / "old.jsonl.reset.2026-10-03T12-28-24.325Z").write_text(
            json.dumps({"type": "session", "id": "old"}) + "\n" + record(NOW - timedelta(hours=10), usage) + "\n")
        (self.sessions / "gone.jsonl.deleted.2026-10-03T01-00-00.000Z").write_text(
            record(NOW - timedelta(hours=12), usage) + "\n")
        report = agent_usage.summarize(NOW - timedelta(days=1), root=self.root)
        self.assertEqual(report["read_errors"], [])
        self.assertEqual(sum(g["calls"] for g in report["groups"].values()), 3)
        self.assertGreater(report["estimated_cost_usd"], 0.7)

    def test_a_corrupt_transcript_is_still_reported(self):
        (self.sessions / "bad.jsonl").write_text("{not json\n")
        report = agent_usage.summarize(NOW - timedelta(days=1), root=self.root)
        self.assertEqual(report["read_errors"], ["bad.jsonl: JSONDecodeError"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
