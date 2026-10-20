#!/usr/bin/env python3
"""Regression tests for the shared relevance policy (email_policy.py).

    python3 test_email_policy.py

Every subject below is a real subject from the user's mailbox that the policy
misread before 2026-09-15 (most were archived by broad filters and classified
"unknown", so nothing guaranteed they would ever surface).
"""
import json
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import email_policy as policy
from statement_email_rules import RULES, gmail_query, identify_provider

FINANCE_SCRIPTS = Path("~/.openclaw/workspace/Skills/afrexai-personal-finance/scripts")


class TestSecurityClass(unittest.TestCase):
    def test_account_access_and_custody_alerts_are_security(self):
        for subject in (
            "Security alert for owner@work.example",          # dropped silently 2026-09-14
            "Your Google Account was recovered successfully",
            "Use 613132 to set up recovery email",
            "Account Recovery Information #129647",
            "You've changed your Online password",
            "Confirm your new withdrawal address",
            "BTC withdrawal initiated",
            "You created a new API key for Exchange A",
            "You asked us to share your data with Plaid",
            "Many files were recently deleted from your OneDrive",
            "Your PhotoApp Account - New Device",
            "[GitHub] Please verify your device",
            "You added an external bank account",
            "You updated your digital wallet",
            "Confirmation: Card is now locked",
            "Confirmation of Card A Mobile® App activity",
            "We're making sure it's you!",
            "Re: Someone tried to reset my password, Ticket ID: 6399279",
        ):
            with self.subTest(subject=subject):
                self.assertEqual(policy.classify_subject(subject), "security")

    def test_payment_failures_are_security(self):
        for subject in (
            "Payment failure: Google Workspace Business Plus for example-startup.com",
            "A message about your recent declined Card B Credit Card transaction",
            "Your payment failed",
        ):
            with self.subTest(subject=subject):
                self.assertEqual(policy.classify_subject(subject), "security")

    def test_security_beats_survey_wording(self):
        self.assertEqual(policy.classify_subject(
            "How did we do?: Someone tried to reset my password"), "security")

    def test_generic_education_is_not_security(self):
        for subject in ("The user, scams are getting more sophisticated",
                        "When Both Sides of Cybersecurity Are AI",
                        "The user, start making safe and secure digital payments",
                        "Your monthly rewards summary",
                        "Paperless confirmation: Notices and Letters"):
            with self.subTest(subject=subject):
                self.assertNotEqual(policy.classify_subject(subject), "security")


class TestMaterialClass(unittest.TestCase):
    def test_records_are_material(self):
        for subject in (
            "A $42.75 transaction was made on your Card A® account",
            "A Friend paid you $75.00",
            "You paid A Friend $75.00",
            "Here's the September 2026 statement you requested",
            "You sent money with Zelle",
            "You've scheduled a Zelle(R) payment",
            "We've got your mobile deposit",
            "Your payment due date is approaching",
            "Large Purchase Approved",
            "We processed your payment",
            "You bought BTC",
            "New Brokerage Document Ready to View",
            "A shipment from order #10004 has been delivered",
            "You've got a package.",
            "Your receipt from Apple.",
            "Your superbill(s) for 8/1/26 to 8/31/26",
            "Work Order has been completed.",
            "Building Towing Starts TOMORROW 7.31.26",
            "Your Insurer A auto renewal is available.",
            "Your telematics program needs your attention.",
            "Example Golf Appointment Is Booked for Tuesday  at 10:00 AM on 8/18/2026",
            "Modified Spirit Airlines Flight Confirmation: QWERTY",
            "Your reservation cancellation for Example Bistro",
            "Reservation Cancellation #40000001 for Example Suites",
        ):
            with self.subTest(subject=subject):
                self.assertEqual(policy.classify_subject(subject), "material")

    def test_statement_variants_and_ride_receipts(self):
        for subject in ("Your HSA statement is now available",
                        "Monthly Utility Statement for Example Apartments",
                        "Important Notice: Your August 2026 Statement",
                        "Statement available for account ending 1234",
                        "Your Card B Credit Card statement is ready!",
                        "Your Saturday evening trip with Uber"):
            with self.subTest(subject=subject):
                self.assertEqual(policy.classify_subject(subject), "material")

    def test_promotions_that_borrow_record_words_are_not_material(self):
        # Both were flagged "material mail in Trash" by the first live run.
        for subject in ("STATEMENT DOORMATS for LESS",
                        "Your Next Great Golf Trip Starts at Pebble Beach"):
            with self.subTest(subject=subject):
                self.assertNotEqual(policy.classify_subject(subject), "material")

    def test_realtime_set_is_unchanged(self):
        # Only the pre-expansion material set pings immediately.
        self.assertTrue(policy.is_realtime("material", "Your HSA statement is now available"))
        self.assertTrue(policy.is_realtime("material", "Trade confirmation available online"))
        self.assertFalse(policy.is_realtime("material", "Your receipt from Apple."))
        self.assertFalse(policy.is_realtime("material", "You've got a package."))
        self.assertTrue(policy.is_realtime("security", "Security alert"))
        self.assertFalse(policy.is_realtime("unknown", "Your statement is ready"))


class TestMarketingClass(unittest.TestCase):
    def test_surveys_and_upsells_beat_material_nouns(self):
        for subject in ("Reminder - Work Order Survey",
                        "Add bags now and save on your flight to Dallas",
                        "Get the seat you really want",
                        "How was Example Bistro?",
                        "The user, Card C Needs Your Feedback",
                        "⭐ ⭐ ⭐ ⭐ ⭐  How many stars would you give shop.example?"):
            with self.subTest(subject=subject):
                self.assertEqual(policy.classify_subject(subject), "marketing")

    def test_engagement_and_product_news_is_marketing(self):
        for subject in ("Important update to My Bank A Deals",
                        "XYZ is a Top Mover on Exchange B!",
                        "Here's what you missed",
                        "Your August Card B Credit Card rewards recap is here👇",
                        "Forgot about your Card B account?",
                        "A new way to deposit crypto is here",
                        "Your recommendations: Complete your setup",
                        "Free samples headed your way!"):
            with self.subTest(subject=subject):
                self.assertEqual(policy.classify_subject(subject), "marketing")

    def test_promotions_label_only_decides_ambiguous_subjects(self):
        self.assertEqual(policy.classify_subject("A fresh month deserves a fresh home",
                                                 ["CATEGORY_PROMOTIONS"]), "marketing")
        self.assertEqual(policy.classify_subject("Your HSA statement is now available",
                                                 ["CATEGORY_PROMOTIONS"]), "material")


class TestRuleEvidence(unittest.TestCase):
    """Policy 5: ≥2 consistent examples; anything else is message-level."""

    def rec(self, **counts):
        return policy.recommend_rule(counts)[0]

    def test_thresholds(self):
        self.assertEqual(self.rec(material=2), policy.ARCHIVE)
        self.assertEqual(self.rec(marketing=2), policy.TRASH)
        self.assertEqual(self.rec(material=1), policy.INSUFFICIENT)
        self.assertEqual(self.rec(marketing=1), policy.INSUFFICIENT)
        self.assertEqual(self.rec(), policy.INSUFFICIENT)

    def test_any_security_forces_message_level_even_with_one_sample(self):
        self.assertEqual(self.rec(security=1), policy.MESSAGE_LEVEL)
        self.assertEqual(self.rec(security=1, marketing=9), policy.MESSAGE_LEVEL)

    def test_marketing_only_requires_no_other_kind(self):
        for other in ("material", "unknown"):
            with self.subTest(other=other):
                self.assertEqual(self.rec(marketing=8, **{other: 1}), policy.MESSAGE_LEVEL)

    def test_unknown_blocks_archive(self):
        self.assertEqual(self.rec(material=14, unknown=1), policy.MESSAGE_LEVEL)
        self.assertEqual(self.rec(unknown=5), policy.MESSAGE_LEVEL)


class TestFilterScope(unittest.TestCase):
    def test_tokens_from_every_criteria_shape(self):
        self.assertEqual(policy.filter_from_tokens({"from": "@p2p.example"}), {"p2p.example"})
        self.assertEqual(policy.filter_from_tokens({"from": "exchangea.example"}), {"exchangea.example"})
        self.assertEqual(policy.filter_from_tokens({"from": "a@x.com"}), {"a@x.com"})
        self.assertEqual(policy.filter_from_tokens(
            {"query": "from:(a@x.com OR b@y.com)"}), {"a@x.com", "b@y.com"})

    def test_trash_rules_never_touch_spam_and_carry_the_guard(self):
        action = policy.junk_filter_action("L_LOW")
        self.assertNotIn("SPAM", action["removeLabelIds"])
        self.assertEqual(action["addLabelIds"], ["L_LOW"])     # filed, never deleted
        self.assertNotIn("TRASH", str(policy.junk_filter_action()))
        self.assertTrue(policy.is_junk_action(action, "L_LOW"))
        self.assertTrue(policy.is_junk_action({"addLabelIds": ["TRASH"]}))          # legacy rules
        self.assertTrue(policy.is_junk_action(policy.junk_filter_action()))         # plan form
        self.assertFalse(policy.is_junk_action({"addLabelIds": ["L_FIN"], "removeLabelIds": ["INBOX"]}, "L_LOW"))
        criteria = policy.trash_filter_criteria("promo@example.com")
        self.assertTrue(policy.has_security_guard(criteria))
        for term in ("security", "password", '"new device"', "withdrawal"):
            self.assertIn(term, criteria["negatedQuery"])

    def test_guard_model_matches_every_security_subject_it_should_spare(self):
        for subject in ("Your PhotoApp Account - New Device", "Security alert",
                        "BTC withdrawal initiated", "Your password was changed",
                        "Please verify your device", "Your card was declined"):
            with self.subTest(subject=subject):
                self.assertTrue(policy.trash_guard_spares(subject))
        self.assertFalse(policy.trash_guard_spares("Free samples headed your way!"))


class TestCoverage(unittest.TestCase):
    """What keeps archived mail visible (task 6)."""

    def disp(self, subject, address, labels=()):
        return policy.backstop_disposition(
            policy.classify_subject(subject), address, subject, labels)

    def test_security_surfaces_from_every_label_and_dedicated_sender(self):
        # Before 2026-09-15 the backstop skipped these whole senders/labels.
        self.assertEqual(self.disp("Security alert: new sign-in",
                                   "southwestairlines@ifly.southwest.com",
                                   ["Travel/Flights"]), "surface")
        self.assertEqual(self.disp("Your password was changed",
                                   "notifications@televisit.example"), "surface")
        self.assertEqual(self.disp("New sign-in to your account",
                                   "no-reply@opentable.com"), "surface")

    def test_dedicated_paths_own_their_slice_only(self):
        self.assertEqual(self.disp("Video appointment reminder for Tuesday, September 15th",
                                   "notifications@televisit.example"), "owned")
        self.assertEqual(self.disp("Your superbill(s) for 8/1/26 to 8/31/26",
                                   "notifications@televisit.example"), "surface")
        self.assertEqual(self.disp("Your reservation confirmation for Example Bistro",
                                   "no-reply@opentable.com"), "owned")
        self.assertEqual(self.disp("How was Example Bistro?",
                                   "no-reply@opentable.com"), "drop")

    def test_card_a_watcher_follows_every_numbered_subdomain(self):
        # Regression: the watcher was pinned to info6.carda.example, so alerts moving
        # to another numbered subdomain would silently stop the ledger.
        for addr in ("alerts@info6.carda.example", "alerts@info7.carda.example"):
            self.assertEqual(policy.dedicated_path_for(
                addr, "A $5.00 transaction was made on your account").id,
                "cron.finance-txn-watch")

    def test_finance_watchers_own_exactly_what_they_announce(self):
        owned = policy.dedicated_path_for
        self.assertEqual(owned("payments@p2p.example", "A Friend paid you $75.00").id,
                         "cron.finance-p2p-watch")
        self.assertIsNone(owned("payments@p2p.example",
                                "Here's the September 2026 statement you requested"))
        self.assertEqual(owned("alerts@info6.carda.example",
                               "A $9.20 transaction was made on your Card A account").id,
                         "cron.finance-txn-watch")
        self.assertIsNone(owned("alerts@info6.carda.example", "Your payment due date is approaching"))
        self.assertEqual(owned("alerts@notify.banka.example", "You sent money with Zelle").id,
                         "cron.finance-zelle-watch")
        self.assertEqual(owned("alerts@welcome.cardc.example",
                               "Large Purchase Approved").id, "cron.finance-card-c-watch")

    def test_travel_mail_is_travel_until_the_catchup_acts(self):
        # Regression (audit D6): every Travel-labelled message used to be
        # "owned" outright, so a gate change the itinerary model ignored had no
        # second owner. The daemon now decides per message, from what the
        # travel catch-up actually handled.
        self.assertEqual(policy.backstop_disposition(
            "material", "a@airline.com", "Gate change: now B12", ["Travel/Flights"]), "travel")
        self.assertEqual(policy.backstop_disposition(
            "security", "a@airline.com", "Security alert", ["Travel/Flights"]), "surface")

    def test_travel_material_needs_a_recognisable_itinerary(self):
        vis = policy.visibility_path
        self.assertEqual(vis("material", "southwestairlines@ifly.southwest.com",
                             "You're going to NYC (LaGuardia) on 09/01 (XYZ789)!",
                             ["Travel/Flights"]), "daemon.travel_label_catchup")
        self.assertEqual(vis("material", "reservations@res-hotelchain.example",
                             "Reservation Cancellation #40000001 for Example Suites",
                             ["Travel/Hotels"]), "daemon.travel_label_catchup")
        # Not itinerary-shaped: the travel catch-up won't act, so the backstop does.
        self.assertEqual(vis("material", "booking@fly.spirit-airlines.com",
                             "Check in for tomorrow's flight!", ["Travel"]), policy.BACKSTOP)
        self.assertEqual(vis("security", "booking@fly.spirit-airlines.com",
                             "Security alert", ["Travel"]), policy.BACKSTOP)


class TestStatementProviderRules(unittest.TestCase):
    EXAMPLES = {
        "card_a": ("Card A <alerts@info6.carda.example>", "Your statement is now available online"),
        "card_b": ("Card B <no-reply@email.cardb.example>", "Your Card B Credit Card statement is ready!"),
        "broker_a": ("Broker A <alerts@brokera.example>",
                     "Your Broker A statement is ready"),
        "hsa_a": ("HSA A <auto_reply@mailer.hsaa.example>",
                  "Your HSA statement is now available"),
        "card_c": ("Card C <alerts@welcome.cardc.example>",
                   "Your September 2026 statement is ready – please review important notices"),
        "card_d": ("Card D <no.reply.alerts@cardd.example>",
                   "Your credit card statement is available"),
        # 2026-10-07: added so a brokerage's quarterly notice is owned by the
        # statement watcher instead of surfacing as a raw FYI.
        "broker_b": ("Broker B <statements@mail.brokerb.example>",
                     "New account statements and disclosures available"),
    }

    def test_provider_subject_pairs_are_exact_and_do_not_cross_match(self):
        owner = next(p for p in policy.DEDICATED_PATHS
                     if p.id == policy.STATEMENT_WATCH_OWNER)
        for provider, (address, subject) in self.EXAMPLES.items():
            with self.subTest(provider=provider):
                self.assertEqual(identify_provider(address, subject), provider)
                self.assertEqual(identify_provider(address, subject.lower()), provider)
                self.assertTrue(owner.owns(address, subject))
                self.assertIsNone(identify_provider(address, "Security alert: " + subject))
                self.assertIsNone(identify_provider(address, subject + " — payment failed"))
                for other, (other_address, _) in self.EXAMPLES.items():
                    if other != provider:
                        self.assertIsNone(identify_provider(other_address, subject))
                        self.assertFalse(owner.owns(other_address, subject))
                domain = RULES[provider]["domains"][0]
                for spoof in (f"alerts@not{domain}", f"alerts@{domain}.example.com", domain):
                    self.assertIsNone(identify_provider(spoof, subject))

    def test_queries_cover_only_requested_provider_templates(self):
        for provider, rule in RULES.items():
            query = gmail_query([provider], 1234567)
            self.assertTrue(query.endswith(" after:1234567"))
            self.assertIn(rule["query"], query)
            self.assertIn(f'from:{rule["domains"][0]}', query)
            phrase = self.EXAMPLES[provider][1]
            self.assertEqual(identify_provider(f'alerts@{rule["domains"][0]}', phrase), provider)
            for other, other_rule in RULES.items():
                if other != provider:
                    self.assertNotIn(other_rule["query"], query)
        query = gmail_query(list(RULES), 0)
        self.assertEqual(query.count(" OR "), len(RULES) - 1)
        for rule in RULES.values():
            self.assertIn(rule["query"], query)
        self.assertEqual(gmail_query(["card_a", "card_a"], 0), gmail_query(["card_a"], 0))
        for providers, after in (([], 0), (["unknown"], 0), ("card_a", 0),
                                 (["card_a"], -1), (["card_a"], True), (["card_a"], "0")):
            with self.subTest(providers=providers, after=after), self.assertRaises(ValueError):
                gmail_query(providers, after)


class TestCardAStatementOwnership(unittest.TestCase):
    """The general monitor yields only to an enabled, healthy statement watcher."""

    SUBJECT = "Your statement is now available online"
    ADDRESS = "alerts@info6.carda.example"
    NOW = datetime(2026, 9, 28, 12, tzinfo=timezone.utc)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state_path = Path(self.tmp.name) / "statement_watch.json"
        patcher = patch.object(policy, "_statement_watch_state_path",
                               return_value=self.state_path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_state(self, **fields):
        self.state_path.write_text(json.dumps(fields))

    def test_heartbeat_requires_enabled_flag_and_recent_utc_time(self):
        for seconds_old, expected in ((0, True), (900, True), (901, False),
                                      (-60, True), (-61, False)):
            with self.subTest(seconds_old=seconds_old):
                stamp = self.NOW - timedelta(seconds=seconds_old)
                self.write_state(enabled=True, successful_poll_at=stamp.isoformat())
                self.assertEqual(policy.statement_watch_active(now=self.NOW), expected)
        self.write_state(enabled=True, successful_poll_at="2026-09-28T12:00:00Z")
        self.assertTrue(policy.statement_watch_active(now=self.NOW))
        for enabled in (False, None, 1, "true"):
            with self.subTest(enabled=enabled):
                self.write_state(enabled=enabled, successful_poll_at=self.NOW.isoformat())
                self.assertFalse(policy.statement_watch_active(now=self.NOW))

    def test_missing_corrupt_unreadable_or_invalid_state_keeps_fallback(self):
        self.assertFalse(policy.statement_watch_active(now=self.NOW))
        for raw in ("{broken", "null", "[]", "true", "{}"):
            with self.subTest(raw=raw):
                self.state_path.write_text(raw)
                self.assertFalse(policy.statement_watch_active(now=self.NOW))
        for stamp in (None, 123, "bad", "2026-09-28T12:00:00",
                      "2026-09-28T07:00:00-05:00"):
            with self.subTest(stamp=stamp):
                self.write_state(enabled=True, successful_poll_at=stamp)
                self.assertFalse(policy.statement_watch_active(now=self.NOW))
        with patch.object(Path, "read_text", side_effect=PermissionError("test")):
            self.assertFalse(policy.statement_watch_active(now=self.NOW))

    def test_exact_card_a_subject_is_owned_only_when_healthy(self):
        self.write_state(enabled=True, successful_poll_at=datetime.now(timezone.utc).isoformat())
        for address in ("statements@carda.example", self.ADDRESS, "alerts@info15.carda.example"):
            for subject in (self.SUBJECT, self.SUBJECT.lower() + ".", self.SUBJECT + "!"):
                with self.subTest(address=address, subject=subject):
                    self.assertEqual(policy.dedicated_path_for(address, subject).id,
                                     policy.STATEMENT_WATCH_OWNER)
        self.assertEqual(policy.backstop_disposition(
            "material", self.ADDRESS, self.SUBJECT), "owned")
        self.assertEqual(policy.visibility_path(
            "material", self.ADDRESS, self.SUBJECT), policy.STATEMENT_WATCH_OWNER)
        self.write_state(enabled=False, successful_poll_at=datetime.now(timezone.utc).isoformat())
        self.assertIsNone(policy.dedicated_path_for(self.ADDRESS, self.SUBJECT))
        self.assertEqual(policy.backstop_disposition(
            "material", self.ADDRESS, self.SUBJECT), "surface")
        self.assertEqual(policy.visibility_path(
            "material", self.ADDRESS, self.SUBJECT), policy.BACKSTOP)
        self.write_state(enabled=True, successful_poll_at="2000-01-01T00:00:00Z")
        self.assertIsNone(policy.dedicated_path_for(self.ADDRESS, self.SUBJECT))

    def test_provider_ownership_requires_provider_enablement(self):
        for enabled_provider in RULES:
            self.write_state(enabled=True, enabled_providers=[enabled_provider],
                             successful_poll_at=datetime.now(timezone.utc).isoformat())
            for provider, (address, subject) in TestStatementProviderRules.EXAMPLES.items():
                with self.subTest(enabled=enabled_provider, provider=provider):
                    owner = policy.dedicated_path_for(address, subject)
                    self.assertEqual(owner.id if owner else None,
                                     policy.STATEMENT_WATCH_OWNER if provider == enabled_provider else None)
                    self.assertEqual(policy.backstop_disposition(
                        "material", address, subject, message_id="new-notice"),
                        "defer" if provider == enabled_provider else "surface")

    def test_legacy_enablement_is_card_a_only_and_invalid_provider_state_falls_back(self):
        self.write_state(enabled=True, successful_poll_at=self.NOW.isoformat())
        self.assertTrue(policy.statement_watch_active("card_a", now=self.NOW))
        self.assertFalse(policy.statement_watch_active("card_b", now=self.NOW))
        for providers in (None, [], "card_a", ["unknown"], [{}]):
            self.write_state(enabled=True, enabled_providers=providers,
                             successful_poll_at=self.NOW.isoformat())
            self.assertFalse(policy.statement_watch_active(now=self.NOW))

    def test_all_provider_acknowledgments_survive_disable_or_outage(self):
        for provider, (address, subject) in TestStatementProviderRules.EXAMPLES.items():
            with self.subTest(provider=provider):
                self.write_state(enabled=False, enabled_providers=[], notices={"cycle": {
                    "provider": provider, "message_ids": ["handled"], "status": "received"}})
                self.assertEqual(policy.dedicated_path_for(
                    address, subject, message_id="handled").id, policy.STATEMENT_WATCH_OWNER)
                self.assertIsNone(policy.dedicated_path_for(address, subject, message_id="new"))
                self.assertEqual(policy.backstop_disposition(
                    "security", address, subject, message_id="handled"), "surface")
    def test_security_payments_and_other_senders_keep_existing_routes(self):
        self.write_state(enabled=True, successful_poll_at=datetime.now(timezone.utc).isoformat())
        for address, subject in (
            ("alerts@carda.example.example.com", self.SUBJECT),
            ("alerts@notcarda.example", self.SUBJECT),
            ("alerts@example.com", self.SUBJECT),
            (self.ADDRESS, "Your statement is now available online — security alert"),
            (self.ADDRESS, "Security alert: " + self.SUBJECT),
            (self.ADDRESS, "Your payment due date is approaching"),
            (self.ADDRESS, "Your statement is ready"),
        ):
            with self.subTest(address=address, subject=subject):
                self.assertIsNone(policy.dedicated_path_for(address, subject))
        self.assertEqual(policy.backstop_disposition(
            "security", self.ADDRESS, self.SUBJECT), "surface")
        self.assertEqual(policy.dedicated_path_for(
            self.ADDRESS, "A $5.00 transaction was made on your account").id,
            "cron.finance-txn-watch")

    def test_per_message_acknowledgment_survives_an_outage(self):
        for extra in ({"notified_at": self.NOW.isoformat()}, {"status": "received"},
                      {"status": "observed_before_activation"}):
            with self.subTest(extra=extra):
                self.write_state(enabled=False, notices={"cycle": {
                    "message_ids": ["mid-1"], "status": "awaiting_upload", **extra}})
                self.assertTrue(policy.statement_watch_handled("mid-1"))
                self.assertFalse(policy.statement_watch_handled("mid-other"))
                self.assertIsNone(policy.dedicated_path_for(self.ADDRESS, self.SUBJECT))
                self.assertEqual(policy.dedicated_path_for(
                    self.ADDRESS, self.SUBJECT, message_id="mid-1").id,
                    policy.STATEMENT_WATCH_OWNER)
                self.assertEqual(policy.backstop_disposition(
                    "material", self.ADDRESS, self.SUBJECT, message_id="mid-1"), "owned")
                self.assertEqual(policy.backstop_disposition(
                    "security", self.ADDRESS, self.SUBJECT, message_id="mid-1"), "surface")

    def test_pending_notice_defers_then_falls_back_without_acknowledgment(self):
        fields = {"notices": {"cycle": {"message_ids": ["mid-1"],
                                        "status": "awaiting_upload"}}}
        self.write_state(enabled=True, successful_poll_at=datetime.now(timezone.utc).isoformat(),
                         **fields)
        self.assertFalse(policy.statement_watch_handled("mid-1"))
        self.assertEqual(policy.backstop_disposition(
            "material", self.ADDRESS, self.SUBJECT, message_id="mid-1"), "defer")
        self.write_state(enabled=True, successful_poll_at="2000-01-01T00:00:00Z", **fields)
        self.assertEqual(policy.backstop_disposition(
            "material", self.ADDRESS, self.SUBJECT, message_id="mid-1"), "surface")

    def test_invalid_notice_state_is_never_an_acknowledgment(self):
        self.assertFalse(policy.statement_watch_handled("mid-1"))
        for notices in ([], None, {"cycle": None}, {"cycle": {
                "message_ids": "mid-1", "status": "received"}}, {"cycle": {
                "message_ids": ["mid-1"], "notified_at": False}}):
            with self.subTest(notices=notices):
                self.write_state(notices=notices)
                self.assertFalse(policy.statement_watch_handled("mid-1"))
        self.state_path.write_text("{broken")
        self.assertFalse(policy.statement_watch_handled("mid-1"))


class TestDedicatedPathsMatchTheirSource(unittest.TestCase):
    """DEDICATED_PATHS describes code in other files; fail when that code moves."""

    WATCHERS = {
        "cron.finance-txn-watch": "finance_txn_watch.py",
        "cron.finance-p2p-watch": "finance_p2p_watch.py",
        "cron.finance-zelle-watch": "finance_zelle_watch.py",
        "cron.finance-card-c-watch": "finance_card_c_watch.py",
    }

    def test_watcher_queries_are_covered_by_their_path(self):
        paths = {p.id: p for p in policy.DEDICATED_PATHS}
        for path_id, filename in self.WATCHERS.items():
            with self.subTest(path=path_id):
                source = (FINANCE_SCRIPTS / filename).read_text()
                query = re.search(r'GMAIL_QUERY = [\'"](.+?)[\'"]\n', source).group(1)
                domain = re.search(r"from:([\w.\-]+)", query).group(1)
                self.assertIn(domain, paths[path_id].sender_domains)

    def test_statement_watch_uses_shared_provider_query_rules(self):
        source = (FINANCE_SCRIPTS / "finance_statement_watch.py").read_text()
        self.assertIn("statement_email_rules", source)
        self.assertIn("gmail_query(", source)
        owner = next(p for p in policy.DEDICATED_PATHS
                     if p.id == policy.STATEMENT_WATCH_OWNER)
        for rule in RULES.values():
            for domain in rule["domains"]:
                self.assertIn(domain, owner.sender_domains)

    def test_long_running_daemon_reloads_statement_rules_when_source_changes(self):
        """Regression: three brokerage notices became raw FYIs because the daemon had
        imported the old provider set before the watcher was enabled."""
        from types import SimpleNamespace
        from unittest.mock import patch
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "statement_email_rules.py"
            source.write_text("old")
            old = SimpleNamespace(
                __file__=str(source),
                RULES={"card_a": {"domains": ("carda.example",)}},
                identify_provider=lambda address, subject: None,
            )
            new = SimpleNamespace(
                __file__=str(source),
                RULES={"card_a": {"domains": ("carda.example",)},
                       "broker_a": {"domains": ("brokera.example",)}},
                identify_provider=lambda address, subject: (
                    "broker_a" if "brokera.example" in address else None),
            )
            with patch.object(policy, "_statement_rules", old), \
                    patch.object(policy, "STATEMENT_RULES", old.RULES), \
                    patch.object(policy, "_STATEMENT_RULES_SIGNATURE", None), \
                    patch.object(policy.importlib, "reload", return_value=new):
                self.assertIsNone(policy._identify_statement_provider(
                    "alerts@brokera.example", "Your Broker A statement is ready"))
                source.write_text("new rules are longer")
                self.assertEqual(policy._identify_statement_provider(
                    "alerts@brokera.example", "Your Broker A statement is ready"),
                    "broker_a")

    def test_daemon_catchup_queries_match(self):
        source = (Path(__file__).parent / "daemon.py").read_text()
        self.assertIn('from:televisit.example subject:"appointment reminder"', source)
        self.assertIn('"from:opentable.com newer_than:2d"', source)



class AppointmentInProse(unittest.TestCase):
    """A person confirming a time in a reply, which the subject gate cannot see.

    2026-10-09: a venue manager wrote "Re: Come see the venue"
    containing "Let's do 3pm Tuesday 10/13 - I've got you on the calendar" and a street
    address. The subject matched nothing and there was no .ics, so the itinerary
    extractor never ran, the mail was filed FYI, and the appointment never reached the
    calendar. The user found it by reading his own inbox.

    The gate needs BOTH a day and a clock time. "Call me at 3" is not a booking and
    "sometime Tuesday" is not one either; requiring both is what keeps this from
    firing the extractor on ordinary correspondence.
    """

    def test_the_email_that_was_missed(self):
        body = ("Let's do 3pm Tuesday 10/13 - I've got you on the calendar.\n\n"
                "We're at 100 Example St, Suite 100 (right next door to the cafe).")
        self.assertTrue(policy.body_looks_like_an_appointment(body))

    def test_other_shapes_a_person_uses(self):
        for body in ("Confirming Thursday at 7:30pm for two",
                     "see you tomorrow at 9am",
                     "Does Oct 14 at 11:00 AM work?",
                     "Booked you in for 6pm on 11/2"):
            self.assertTrue(policy.body_looks_like_an_appointment(body), body)

    def test_a_time_with_no_day_is_not_a_booking(self):
        self.assertFalse(policy.body_looks_like_an_appointment("Can you call me at 3? thanks"))

    def test_a_day_with_no_time_is_not_a_booking(self):
        self.assertFalse(policy.body_looks_like_an_appointment("Lets grab lunch sometime Tuesday"))

    def test_ordinary_mail_does_not_trip_it(self):
        for body in ("Your invoice for September is attached, total $420",
                     "The quarterly report shows 12/13 projects on track",
                     "Thanks for the note, I'll get back to you."):
            self.assertFalse(policy.body_looks_like_an_appointment(body), body)

    def test_it_only_reads_the_top_of_the_email(self):
        """A date buried under a signature and a legal footer is not the subject of
        the mail, and scanning the whole body would fire on every newsletter."""
        body = "Thanks!\n" + ("x" * 2000) + "\nEvent: Friday at 8pm"
        self.assertFalse(policy.body_looks_like_an_appointment(body))

if __name__ == "__main__":
    unittest.main(verbosity=2)
