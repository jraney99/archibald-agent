#!/usr/bin/env python3
"""Tests for learning filters for NEW senders (sender_ledger.py).

    python3 test_sender_ledger.py

The ledger only ever produces a *candidate*; every safety gate that guards a
hand-taught sender still applies afterwards, so these tests pin the two things
the ledger alone decides: when a sender is recurring enough to ask about, and
where its mail would file.
"""
import json
import tempfile
import time
import unittest
from pathlib import Path

import email_policy as policy
import sender_filter as sf
import sender_ledger as ledger

CONFIG = sf.load_config(Path(__file__).resolve().parent.parent / "senders.json")
DAY = 86400


def feed(subjects, address="orders@newvendor.com", start=0.0, step=3 * DAY, labels=None):
    """Observe `subjects` from one sender, `step` apart."""
    book = {}
    for i, subject in enumerate(subjects):
        ledger.observe(book, address, subject, labels, now=start + i * step)
    return book


class TestRecurrence(unittest.TestCase):
    def test_a_single_message_is_not_a_pattern(self):
        book = feed(["Your order #1 confirmed"])
        self.assertFalse(ledger.qualifies(ledger.entry_for(book, "orders@newvendor.com"),
                                          now=DAY))

    def test_a_one_day_burst_is_not_a_pattern(self):
        book = feed(["Your order #1 confirmed", "Your order #2 confirmed",
                      "Your order #3 confirmed"], step=60)
        self.assertFalse(ledger.qualifies(ledger.entry_for(book, "orders@newvendor.com"),
                                          now=300))

    def test_one_orders_lifecycle_is_not_a_recurring_relationship(self):
        # order confirmed → shipped → delivered, all within days, is a single
        # transaction; it must not earn a permanent filter on its own.
        book = feed(["Order #10004 confirmed", "A shipment from order #10004 is on the way",
                      "A shipment from order #10004 has been delivered"], step=2 * DAY)
        self.assertFalse(ledger.qualifies(ledger.entry_for(book, "orders@newvendor.com"),
                                          now=4 * DAY))

    def test_recurring_over_weeks_qualifies(self):
        book = feed(["Your order #1 confirmed", "Your order #2 confirmed",
                      "Your order #3 confirmed"], step=7 * DAY)
        entry = ledger.entry_for(book, "orders@newvendor.com")
        self.assertTrue(ledger.qualifies(entry, now=14 * DAY))
        self.assertEqual(entry["count"], 3)

    def test_counts_use_the_shared_classes(self):
        book = feed(["Your order #1 confirmed", "50% off everything",
                      "Security alert: new device"])
        kinds = ledger.entry_for(book, "orders@newvendor.com")["kinds"]
        self.assertEqual((kinds["material"], kinds["marketing"], kinds["security"]), (1, 1, 1))

    def test_quiet_senders_are_retired_and_the_ledger_stays_bounded(self):
        book = feed(["Your order #1 confirmed"], start=0.0)
        ledger.observe(book, "new@other.com", "Hello", now=200 * DAY)
        self.assertNotIn("orders@newvendor.com", book)
        book = {}
        for i in range(ledger.MAX_ENTRIES + 25):
            ledger.observe(book, f"s{i}@x.com", "Your order confirmed", now=i)
        self.assertLessEqual(len(book), ledger.MAX_ENTRIES)


class TestLedgerFile(unittest.TestCase):
    def test_load_and_save_follow_a_repointed_ledger_file(self):
        # Regression: load(path=LEDGER_FILE) bound the default at definition
        # time, so redirecting LEDGER_FILE was silently ignored and the tests
        # read and wrote the user's real ledger.
        original = ledger.LEDGER_FILE
        before = original.read_bytes() if original.exists() else None
        ledger.LEDGER_FILE = Path(tempfile.mkdtemp()) / "ledger.json"
        try:
            self.assertEqual(ledger.load(), {})
            book = {}
            ledger.observe(book, "a@b.com", "Your package was delivered")
            ledger.save(book)
            self.assertTrue(ledger.LEDGER_FILE.exists())
            self.assertIn("a@b.com", ledger.load())
        finally:
            ledger.LEDGER_FILE = original
        # The live daemon writes the real ledger, so compare contents rather
        # than assuming it doesn't exist.
        after = original.read_bytes() if original.exists() else None
        self.assertEqual(before, after, "the real ledger must not be touched by tests")

    def test_a_corrupt_ledger_degrades_to_empty(self):
        original = ledger.LEDGER_FILE
        ledger.LEDGER_FILE = Path(tempfile.mkdtemp()) / "ledger.json"
        ledger.LEDGER_FILE.write_text("{ not json")
        try:
            self.assertEqual(ledger.load(), {})
        finally:
            ledger.LEDGER_FILE = original


class TestLabelRouting(unittest.TestCase):
    def label(self, subjects):
        inferred = ledger.infer_label(subjects)
        return inferred[0] if inferred else None

    def test_label_needs_two_agreeing_subjects(self):
        self.assertIsNone(self.label(["Your package is out for delivery"]))
        self.assertEqual(self.label(["Your package is out for delivery",
                                     "Tracking number for your shipment"]),
                         "Records/Packages")

    def test_routes_to_the_precise_leaf_not_an_areas_most_common_label(self):
        # Regression: routing by area filed hotel bookings under Travel/Flights,
        # because airlines are the most common travel sender.
        self.assertEqual(self.label([
            "Reservation Confirmation #70000001 for Example Suites by Marriott",
            "Your Aug 01 - Aug 02 Stay at Example Hotel Downtown"]), "Travel/Hotels")
        self.assertEqual(self.label(["Check in for tomorrow's flight!",
                                     "Your boarding pass is ready"]), "Travel/Flights")

    def test_ambiguous_senders_are_not_routed(self):
        self.assertIsNone(self.label(["Hello", "Checking in", "Quick question"]))
        # A restaurant booking is not a trip — no label fits, so none is chosen.
        self.assertIsNone(self.label(["Your reservation confirmation for Example Bistro",
                                      "Your reservation cancellation for Example Cafe"]))

    def test_a_tie_between_labels_routes_nowhere(self):
        self.assertIsNone(ledger.infer_label(
            ["Tracking number for your shipment", "Tracking number attached",
             "Your order confirmation", "Your order #12 confirmed"]))

    def test_money_senders_are_never_learned(self):
        # policy 6: a new financial sender must be added deliberately, or a
        # lookalike domain could earn a rule that hides it from the inbox.
        self.assertIsNone(self.label(["Your statement is ready",
                                      "A $42.00 transaction was made on your account",
                                      "Your payment was received"]))

    def test_a_label_outside_owners_taxonomy_is_never_invented(self):
        entry = {"subjects": ["Your package is out for delivery",
                              "Tracking number for your shipment"]}
        self.assertIsNone(ledger.route(entry, {"senders": [{"label": "Other"}]}))


class TestLearnedVerdict(unittest.TestCase):
    def verdict(self, subjects, **kw):
        kw.setdefault("step", 7 * DAY)
        book = feed(subjects, **kw)
        return ledger.learned_verdict(book, kw.get("address", "orders@newvendor.com"),
                                      CONFIG, now=len(subjects) * 7 * DAY)

    def test_recurring_records_produce_a_candidate(self):
        v = self.verdict(["Your package has been delivered",
                          "Your package is out for delivery",
                          "Tracking number for your shipment"])
        self.assertEqual((v["label"], v["area"], v["source"]),
                         ("Records/Packages", "packages", "learned"))
        self.assertEqual(v["name"], "newvendor.com")

    def test_half_marketing_is_mixed_and_earns_nothing(self):
        # Regression: records were merely the largest bucket, so a 50/50
        # records-and-marketing sender was nominated.
        self.assertIsNone(self.verdict([
            "Your package is out for delivery", "Tracking number for your shipment",
            "50% off everything", "Last chance: save 20%"]))

    def test_marketing_senders_are_left_to_the_unsubscribe_flow(self):
        self.assertIsNone(self.verdict(["50% off everything", "Last chance: save 20%",
                                        "Free shipping this weekend"]))

    def test_any_security_history_blocks_a_learned_rule(self):
        self.assertIsNone(self.verdict(["Your order #1 confirmed", "Your order #2 confirmed",
                                        "Security alert: new sign-in", "Your order #3 confirmed"]))

    def test_unclear_senders_produce_nothing(self):
        self.assertIsNone(self.verdict(["Hi the user", "Following up", "Any thoughts?"]))

    def test_not_yet_recurring_produces_nothing(self):
        book = feed(["Your order #1 confirmed", "Your order #2 confirmed"])
        self.assertIsNone(ledger.learned_verdict(book, "orders@newvendor.com", CONFIG,
                                                 now=3 * DAY))


class TestLearningWriteBack(unittest.TestCase):
    """A confirmed learned sender teaches senders.json for next time."""

    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "learned_senders.json"

    def test_remember_then_recognise_the_whole_domain(self):
        self.assertTrue(sf.remember_learned_sender(
            "newvendor.com", "newvendor.com", "Records/Packages", "packages", self.path))
        entries = json.loads(self.path.read_text())
        self.assertEqual(entries[0]["domains"], ["newvendor.com"])
        # A different address at that domain is now a known sender.
        config = {"senders": entries}
        self.assertEqual(
            sf.classify_sender("shipping@newvendor.com", "anything", config)["label"],
            "Records/Packages")

    def test_shared_sending_platforms_are_never_learned(self):
        # Regression: learning shopifyemail.com from one store would have filed
        # every other store on the platform the same way.
        for domain in ("shopifyemail.com", "sendgrid.net", "us14.mcsv.net"):
            with self.subTest(domain=domain):
                self.assertFalse(sf.remember_learned_sender(
                    "store", domain, "Records/Receipts", "shopping", self.path))
        self.assertFalse(self.path.exists())

    def test_the_same_domain_is_never_recorded_twice(self):
        sf.remember_learned_sender("x", "newvendor.com", "Records/Packages", "packages", self.path)
        self.assertFalse(sf.remember_learned_sender(
            "x", "newvendor.com", "Records/Receipts", "shopping", self.path))
        self.assertEqual(len(json.loads(self.path.read_text())), 1)

    def test_curated_senders_still_win(self):
        merged = sf.load_config()
        card_d = [e for e in merged["senders"] if "cardd.example" in e.get("domains", [])]
        self.assertTrue(card_d and card_d[0]["label"] == "Finance/Credit Cards/Card D")


class TestPolicyStillGovernsLearnedSenders(unittest.TestCase):
    """The ledger proposes; email_policy still decides."""

    def test_a_candidate_with_thin_live_history_is_refused(self):
        self.assertEqual(sf.recommend_sender_policy({"total": 1, "material": 1}, known=True),
                         "unknown")

    def test_a_candidate_with_mixed_live_history_is_refused(self):
        self.assertEqual(sf.recommend_sender_policy(
            {"total": 5, "material": 3, "marketing": 2}, known=True), "mixed")

    def test_only_consistent_records_earn_the_rule(self):
        self.assertEqual(sf.recommend_sender_policy({"total": 4, "material": 4}, known=True),
                         "archive")


if __name__ == "__main__":
    unittest.main(verbosity=2)
