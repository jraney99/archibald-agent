#!/usr/bin/env python3
"""A sender on the suppress list is filed, never announced.

2026-10-14, the user on a 10-line FYI digest: "there is a lot of useless clutter in
this". Six of the ten were noise — two golf-venue booking mails for a
reservation he had already confirmed by tapping a Telegram button (and a club he
had just cancelled), and four TestFlight builds of apps he is developing. The
inbox filter already archived them; the digest is a SEPARATE path and had no
sender-level suppression at all.

What must stay true: this silences a digest line, never a tier. Security, action
and realtime mail never reach _queue_fyi, so nothing here can hide an alert.

    python3 test_fyi_suppression.py
"""
import json
import re
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
SPEC = _HERE.parent / "fyi_suppressed_senders.json"
DAEMON = (_HERE / "daemon.py").read_text()


def suppressed() -> list:
    return json.loads(SPEC.read_text())["suppressed"]


class TheListIsHonest(unittest.TestCase):
    def test_every_entry_has_a_reason(self):
        missing = [e["sender"] for e in suppressed() if not str(e.get("why", "")).strip()]
        self.assertEqual(missing, [], f"suppressed with no reason given: {missing}")

    def test_every_entry_is_an_address_not_a_domain(self):
        """A domain would silence senders nobody has looked at."""
        for e in suppressed():
            self.assertRegex(e["sender"], r"^[^@\s]+@[^@\s]+\.[^@\s]+$", e["sender"])

    def test_the_senders_owner_named_are_on_it(self):
        addrs = {e["sender"] for e in suppressed()}
        self.assertIn("testflight_no_reply@email.apple.com", addrs)
        self.assertIn("golfvenue@mailing.playbypoint.com", addrs)


class ItOnlySilencesTheDigest(unittest.TestCase):
    def test_the_check_lives_in_queue_fyi_and_nowhere_else(self):
        """If this ever moved up into classification it would change a TIER, and
        could then swallow an action or security mail."""
        self.assertEqual(DAEMON.count("FYI_SUPPRESSED"), 2,
                         "FYI_SUPPRESSED is referenced outside _queue_fyi")
        body = DAEMON.split("def _queue_fyi")[1].split("\ndef ")[0]
        self.assertIn("FYI_SUPPRESSED", body)

    def test_it_returns_before_queueing_and_logs_why(self):
        body = DAEMON.split("def _queue_fyi")[1].split("\ndef ")[0]
        gate = body.split("FYI_SUPPRESSED")[1][:200]
        self.assertIn("return", gate)
        self.assertIn("suppressed", gate.lower())

    def test_nothing_touches_the_tier_or_the_filter(self):
        body = DAEMON.split("def _queue_fyi")[1].split("\ndef ")[0]
        for forbidden in ("tier =", "_remember_announced", "archive", "trash"):
            self.assertNotIn(forbidden, body, f"_queue_fyi now does more than queue ({forbidden})")


class ItFailsOpen(unittest.TestCase):
    def test_a_missing_file_suppresses_nothing(self):
        src = DAEMON.split("def _load_fyi_suppressed")[1].split("\ndef ")[0]
        self.assertIn("FileNotFoundError", src)
        self.assertIn("return set()", src)

    def test_an_unreadable_file_suppresses_nothing_and_says_so(self):
        src = DAEMON.split("def _load_fyi_suppressed")[1].split("\ndef ")[0]
        tail = src.split("except Exception")[1]
        self.assertIn("print(", tail)
        self.assertIn("set()", tail)


if __name__ == "__main__":
    unittest.main(verbosity=2)
