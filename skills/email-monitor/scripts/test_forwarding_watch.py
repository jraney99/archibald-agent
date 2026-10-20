#!/usr/bin/env python3
"""Tests for the Gmail forwarding alarm's decision logic."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import forwarding_watch as fw  # noqa: E402

CLEAN = fw.snapshot([], {})
PENDING = fw.snapshot([{"forwardingEmail": "Thief@Evil.example", "verificationStatus": "pending"}], {})
ACTIVE = fw.snapshot(
    [{"forwardingEmail": "thief@evil.example", "verificationStatus": "accepted"}],
    {"enabled": True, "emailAddress": "thief@evil.example", "disposition": "trash"})


class _Req:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class FakeSettingsGmail:
    def __init__(self, addresses=(), auto=None):
        self.addresses, self.auto = list(addresses), auto

    def users(self):
        return self

    def settings(self):
        return self

    def forwardingAddresses(self):
        return self

    def list(self, userId):
        return _Req(lambda: {"forwardingAddresses": self.addresses} if self.addresses else {})

    def getAutoForwarding(self, userId):
        return _Req(lambda: self.auto)


class TestSnapshot(unittest.TestCase):
    def test_reads_both_settings(self):
        snap = fw.read_settings(FakeSettingsGmail(
            [{"forwardingEmail": "a@x.example", "verificationStatus": "accepted"}],
            {"enabled": False}))
        self.assertEqual(snap["addresses"], [{"email": "a@x.example", "status": "accepted"}])
        self.assertFalse(snap["auto_enabled"])

    def test_empty_api_responses_are_clean(self):
        self.assertTrue(fw.is_clean(fw.read_settings(FakeSettingsGmail([], None))))
        self.assertEqual(fw.signature(CLEAN), "")

    def test_signature_ignores_address_order(self):
        a = fw.snapshot([{"forwardingEmail": "b@x.example"}, {"forwardingEmail": "a@x.example"}], {})
        b = fw.snapshot([{"forwardingEmail": "a@x.example"}, {"forwardingEmail": "b@x.example"}], {})
        self.assertEqual(fw.signature(a), fw.signature(b))


class TestEvaluate(unittest.TestCase):
    def test_clean_stays_quiet(self):
        message, watch = fw.evaluate(CLEAN, {}, 1000)
        self.assertIsNone(message)
        self.assertEqual(watch["signature"], "")
        self.assertEqual(watch["last_check"], 1000)

    def test_forwarding_on_first_run_alerts_instead_of_baselining(self):
        message, watch = fw.evaluate(ACTIVE, {}, 1000)
        self.assertIn("forward mail to someone else", message)
        self.assertIn("thief@evil.example", message)
        self.assertIn("Auto-forwarding is ON", message)
        self.assertIn("deletes your copy", message)
        self.assertIn("Settings → Forwarding", message)
        self.assertEqual(watch["alerted_at"], 1000)

    def test_an_unconfirmed_address_is_already_an_alert(self):
        message, _ = fw.evaluate(PENDING, {}, 1000)
        self.assertIn("thief@evil.example (waiting for confirmation)", message)
        self.assertNotIn("Auto-forwarding", message)

    def test_same_state_is_not_repeated_until_a_day_later(self):
        _, watch = fw.evaluate(ACTIVE, {}, 1000)
        self.assertIsNone(fw.evaluate(ACTIVE, watch, 1000 + 3600)[0])
        message, later = fw.evaluate(ACTIVE, watch, 1000 + fw.REALERT_S)
        self.assertIsNotNone(message)
        self.assertEqual(later["alerted_at"], 1000 + fw.REALERT_S)

    def test_a_change_alerts_immediately(self):
        _, watch = fw.evaluate(PENDING, {}, 1000)
        message, _ = fw.evaluate(ACTIVE, watch, 1060)
        self.assertIn("Auto-forwarding is ON", message)

    def test_turning_it_off_sends_one_all_clear(self):
        _, watch = fw.evaluate(ACTIVE, {}, 1000)
        message, watch = fw.evaluate(CLEAN, watch, 2000)
        self.assertEqual(message, fw.CLEAR_TEXT)
        self.assertNotIn("alerted_at", watch)
        self.assertIsNone(fw.evaluate(CLEAN, watch, 3000)[0])

    def test_addresses_are_escaped_for_telegram_html(self):
        snap = fw.snapshot([{"forwardingEmail": "<b>x</b>@evil.example", "verificationStatus": "accepted"}], {})
        message, _ = fw.evaluate(snap, {}, 1)
        self.assertIn("&lt;b&gt;x&lt;/b&gt;@evil.example", message)

    def test_a_successful_read_clears_the_failure_count(self):
        _, watch = fw.evaluate(CLEAN, {"failures": 2}, 1)
        self.assertEqual(watch["failures"], 0)


class TestSchedulingAndFailures(unittest.TestCase):
    def test_due_every_fifteen_minutes(self):
        self.assertTrue(fw.due({}, 1000))
        self.assertFalse(fw.due({"last_check": 1000}, 1000 + fw.CHECK_INTERVAL_S - 1))
        self.assertTrue(fw.due({"last_check": 1000}, 1000 + fw.CHECK_INTERVAL_S))

    def test_failures_alert_after_three_then_daily(self):
        watch, now = {}, 1000
        for expected in (False, False, True, False):
            message, watch = fw.record_failure(watch, "403 <forbidden>", now)
            self.assertEqual(message is not None, expected)
            now += fw.CHECK_INTERVAL_S
        self.assertIn("&lt;forbidden&gt;", fw.record_failure({"failures": 5}, "403 <forbidden>", 10**9)[0])
        message, _ = fw.record_failure(watch, "x", watch["failure_alerted_at"] + fw.REALERT_S)
        self.assertIsNotNone(message)


if __name__ == "__main__":
    unittest.main(verbosity=2)
