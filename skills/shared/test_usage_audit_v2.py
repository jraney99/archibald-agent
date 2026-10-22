#!/usr/bin/env python3

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import usage_audit_v2 as audit


class UsageAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "agents/main/sessions").mkdir(parents=True)
        (self.root / "logs").mkdir()
        self.since = datetime(2026, 10, 1, tzinfo=timezone.utc)

    def tearDown(self):
        self.tmp.cleanup()

    def write_session(self, name, rows):
        path = self.root / "agents/main/sessions" / name
        path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")

    def assistant(self, ident, model, usage, day="2026-10-02T12:00:00Z", content=None):
        return {"id": ident, "type": "message", "timestamp": day, "message": {"role": "assistant", "model": model, "usage": usage, "content": content or []}}

    def test_dedupes_archives_and_reports_coverage_gaps(self):
        usage = {"input": 10, "output": 20, "cacheRead": 100, "cacheWrite": 200, "cacheWrite1h": 50}
        row = self.assistant("same", "claude-sonnet-4-6", usage, content=[{"type": "toolCall", "id": "web1", "name": "web_search", "arguments": {"q": "x"}}])
        compaction = {"id": "compact1", "type": "compaction", "timestamp": "2026-10-02T13:00:00Z"}
        self.write_session("a.jsonl", [row, compaction])
        self.write_session("a.jsonl.reset.1", [row, compaction])
        result = audit.summarize(self.root, self.since)
        group = result["groups"]["conversation:claude-sonnet-4-6"]
        self.assertEqual(group["calls"], 1)
        self.assertEqual(group["cacheWrite1h"], 50)
        self.assertEqual(result["compactions_without_usage"], 1)
        self.assertEqual(result["unpriced_web_calls"], {"web_search": 1})
        self.assertIn("compaction_usage_missing", result["lower_bound_reasons"])
        self.assertIn("web_provider_cost_missing", result["lower_bound_reasons"])

    def test_unknown_model_is_explicitly_unpriced(self):
        self.write_session("u.jsonl", [self.assistant("u1", "unknown-model", {"input": 100, "output": 20, "cacheRead": 0, "cacheWrite": 0})])
        result = audit.summarize(self.root, self.since)
        self.assertEqual(result["unpriced_calls"], 1)
        self.assertEqual(result["unpriced_tokens"], 120)
        self.assertIn("unknown_model_pricing", result["lower_bound_reasons"])

    def test_daily_rollup_and_haiku_55_tiers(self):
        low = {"input": 1000, "output": 100, "cacheRead": 10_000, "cacheWrite": 20_000}
        high = {"input": 1000, "output": 100, "cacheRead": 90_000, "cacheWrite": 20_000}
        self.write_session("h.jsonl", [
            self.assistant("h1", "claude-haiku-5-5", low, "2026-10-02T12:00:00Z"),
            self.assistant("h2", "claude-haiku-5-5", high, "2026-10-03T12:00:00Z"),
        ])
        result = audit.summarize(self.root, self.since)
        self.assertEqual(set(result["daily"]), {"2026-10-02", "2026-10-03"})
        self.assertAlmostEqual(audit.cost("claude-haiku-5-5", low), 0.00275)
        self.assertAlmostEqual(audit.cost("claude-haiku-5-5", high), 0.01775)

    def test_direct_log_and_malformed_record(self):
        rows = [
            json.dumps({"ts": 1790942400, "script": "x.py:f", "model": "claude-sonnet-5-5", "input_tokens": 100, "output_tokens": 10}),
            "not-json",
        ]
        (self.root / "logs/api_usage.jsonl").write_text("\n".join(rows))
        result = audit.summarize(self.root, self.since)
        self.assertIn("direct:x.py:f:claude-sonnet-5-5", result["groups"])
        self.assertEqual(len(result["read_errors"]), 1)

    def test_unicode_line_separator_inside_json_is_not_a_record_boundary(self):
        row = self.assistant(
            "unicode-1",
            "claude-sonnet-4-6",
            {"input": 10, "output": 20, "cacheRead": 0, "cacheWrite": 0},
            content=[{"type": "text", "text": "alpha\u2028beta"}],
        )
        path = self.root / "agents/main/sessions/unicode.jsonl"
        path.write_text(json.dumps(row, ensure_ascii=False) + "\n")
        result = audit.summarize(self.root, self.since)
        self.assertEqual(result["read_errors"], [])
        self.assertEqual(result["groups"]["conversation:claude-sonnet-4-6"]["calls"], 1)

    def test_preserves_cron_and_heartbeat_categories(self):
        usage = {"input": 10, "output": 20, "cacheRead": 0, "cacheWrite": 0}
        self.write_session("cron-id.jsonl", [self.assistant("cron-1", "claude-sonnet-4-6", usage)])
        self.write_session("beat-id.jsonl", [self.assistant("beat-1", "claude-sonnet-4-6", usage)])
        (self.root / "agents/main/sessions/cron-id.trajectory.jsonl").write_text(
            json.dumps({"sessionKey": "agent:main:cron:job"}) + "\n"
        )
        (self.root / "agents/main/sessions/beat-id.trajectory.jsonl").write_text(
            json.dumps({"sessionKey": "agent:main:main:heartbeat"}) + "\n"
        )
        result = audit.summarize(self.root, self.since)
        self.assertEqual(result["groups"]["cron:claude-sonnet-4-6"]["calls"], 1)
        self.assertEqual(result["groups"]["heartbeat:claude-sonnet-4-6"]["calls"], 1)


if __name__ == "__main__":
    unittest.main()
