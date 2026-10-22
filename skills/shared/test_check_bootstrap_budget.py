#!/usr/bin/env python3
"""The bootstrap-size alert must say what is actually happening.

2026-10-11: both doctor severities produced one sentence — "found files being
truncated out of every turn's context again (same class of bug as 2026-09-13)".
An `info` finding means nothing is truncated yet, so the user was told the old
incident had recurred while the real state was 65 characters of headroom and no
loss at all. The check exists to be believed; overstating costs it that.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_bootstrap_budget as c


def finding(severity, message="Total bootstrap context is near the configured total limit."):
    return {"checkId": "core/doctor/bootstrap-size", "severity": severity, "message": message}


class Wording(unittest.TestCase):
    def _run(self, findings):
        sent, printed = [], []
        fake = type("R", (), {"stdout": __import__("json").dumps({"findings": findings}),
                              "stderr": "", "returncode": 0})()
        with patch.object(c.subprocess, "run", return_value=fake), \
             patch.object(c, "notify", side_effect=lambda t: sent.append(t)), \
             patch("builtins.print", side_effect=lambda *a, **k: printed.append(a[0] if a else "")):
            c.main()
        return (sent[0] if sent else None), (printed[0] if printed else None)

    def test_info_says_nothing_is_being_dropped(self):
        text, status = self._run([finding("info")])
        self.assertIn("close to its limit", text)
        self.assertIn("Nothing is being dropped yet", text)
        # The 2026-09-13 incident must NOT be invoked when nothing is lost.
        self.assertNotIn("truncated", text)
        self.assertNotIn("2026-09-13", text)
        self.assertIn("near_limit", status)

    def test_warning_does_say_truncated(self):
        text, status = self._run([finding("warning", "AGENTS.md exceeds the per-file limit.")])
        self.assertIn("being truncated", text)
        self.assertIn("2026-09-13", text)
        self.assertIn("truncation_found", status)

    def test_a_finding_with_no_severity_takes_the_loud_wording(self):
        """Fail safe. doctor always sets a severity, but if one ever arrives without
        it, calling a real truncation "near the limit" would hide the exact loss this
        check exists to catch. A runtime-contract test pins this too."""
        text, status = self._run([{"checkId": "core/doctor/bootstrap-size",
                                   "message": "AGENTS.md truncated:true"}])
        self.assertIn("being truncated", text)
        self.assertIn("truncation_found", status)

    def test_an_unknown_severity_takes_the_loud_wording(self):
        text, _ = self._run([finding("critical", "SOUL.md dropped")])
        self.assertIn("being truncated", text)

    def test_a_mixed_batch_takes_the_worse_wording(self):
        text, _ = self._run([finding("info"), finding("warning", "SOUL.md truncated.")])
        self.assertIn("being truncated", text)

    def test_both_wordings_still_say_how_to_fix_it(self):
        for sev in ("info", "warning"):
            text, _ = self._run([finding(sev)])
            self.assertIn("bootstrapTotalMaxChars", text)
            self.assertIn("restart the gateway", text)

    def test_every_finding_message_is_listed(self):
        text, _ = self._run([finding("info", "first thing"), finding("info", "second thing")])
        self.assertIn("- first thing", text)
        self.assertIn("- second thing", text)

    def test_no_finding_sends_nothing(self):
        text, status = self._run([{"checkId": "core/doctor/something-else", "severity": "warning"}])
        self.assertIsNone(text)
        self.assertIn("clean", status)


if __name__ == "__main__":
    unittest.main(verbosity=2)
