#!/usr/bin/env python3
"""
Tests for recurring-sender auto-filing detection + dedup (sender_filter.py).

    python3 test_sender_filter.py

Plain unittest, stdlib only. Network helpers (gmail_service / create_filter /
backfile_inbox) are not exercised here — only the pure detection and state logic.
Importing sender_filter must stay side-effect-free.
"""
import unittest
from pathlib import Path

import sender_filter as ff

CFG = ff.load_config(Path(__file__).resolve().parent.parent / "senders.json")


class TestAddressExtraction(unittest.TestCase):
    def test_display_name_form(self):
        self.assertEqual(
            ff.extract_address("Card D <no.reply.alerts@cardd.example>"),
            "no.reply.alerts@cardd.example",
        )

    def test_bare_address(self):
        self.assertEqual(ff.extract_address("info6.carda.example@x"), "info6.carda.example@x".lower())

    def test_lowercased(self):
        self.assertEqual(ff.extract_address("<Foo@Bar.COM>"), "foo@bar.com")


class TestKnownInstitutions(unittest.TestCase):
    def test_card_d_alert_is_card_label(self):
        v = ff.classify_sender("no.reply.alerts@cardd.example", "You made a payment", CFG)
        self.assertEqual(v["label"], "Finance/Credit Cards/Card D")   # one folder per account
        self.assertEqual(v["source"], "known")

    def test_card_d_subdomain_matches(self):
        v = ff.classify_sender("marketing@mail.cardd.example", "Welcome to your card", CFG)
        self.assertIsNotNone(v)
        self.assertEqual(v["name"], "Card D")

    def test_broker_b_files_to_investing(self):
        v = ff.classify_sender("alerts@brokerb.example", "Your statement is ready", CFG)
        self.assertEqual(v["label"], "Finance/Investing/Broker B")   # one folder per account

    def test_card_c_alias_domain(self):
        v = ff.classify_sender("no-reply@member.cardc.example", "hi", CFG)
        self.assertEqual(v["label"], "Finance/Credit Cards/Card C")   # one folder per account

    def test_unknown_newsletter_sender_ignored(self):
        self.assertIsNone(ff.classify_sender("newsletter@substack.com", "Weekly digest", CFG))


class TestNonFinanceAreas(unittest.TestCase):
    """The generalised config files any recognised recurring sender, not just
    financial institutions — each to its own label, with an `area` for the
    proposal emoji."""

    def test_airline_files_to_travel(self):
        v = ff.classify_sender("notify@ifly.southwest.com", "Your flight reminder", CFG)
        self.assertEqual(v["label"], "Travel/Flights")
        self.assertEqual(v["area"], "travel")
        self.assertEqual(v["source"], "known")

    def test_carrier_files_to_packages(self):
        v = ff.classify_sender("pkginfo@ups.com", "Your package is on the way", CFG)
        self.assertEqual(v["label"], "Records/Packages")
        self.assertEqual(v["area"], "packages")

    def test_esign_files_to_documents(self):
        v = ff.classify_sender("dse_na4@docusign.net", "Completed: lease", CFG)
        self.assertEqual(v["label"], "Records/Documents")

    def test_property_manager_files_to_home_building(self):
        v = ff.classify_sender("no-reply@resident-portal.example", "Payment received", CFG)
        self.assertEqual(v["label"], "Home/Building")
        self.assertEqual(v["area"], "home")

    def test_legacy_institutions_key_still_classifies(self):
        # A config using the old `institutions` key (pre-generalisation) must
        # still work — _config_senders() accepts either key.
        legacy = {"institutions": [{"name": "X", "domains": ["x.com"], "label": "L"}]}
        v = ff.classify_sender("a@x.com", "hi", legacy)
        self.assertEqual(v["label"], "L")

    def test_person_at_bank_domain_still_matches_domain(self):
        # match_institution is domain-based; the SAFETY comes from the filter being
        # scoped to the exact address, not from refusing to classify here.
        v = ff.classify_sender("banker@cardd.example", "Coffee meeting", CFG)
        self.assertIsNotNone(v)  # classified...
        # ...but the heuristic path would NOT have fired on this subject, and the
        # created filter targets banker@cardd.example only — never cardd.example.


class TestHeuristic(unittest.TestCase):
    def test_unknown_bank_statement_subject_fires(self):
        v = ff.classify_sender(
            "estatements@newregionalbank.com", "Your monthly statement is ready", CFG)
        self.assertIsNotNone(v)
        self.assertEqual(v["source"], "heuristic")
        self.assertEqual(v["label"], "Finance")

    def test_unknown_domain_non_financial_subject_ignored(self):
        self.assertIsNone(ff.classify_sender(
            "hello@newregionalbank.com", "Check out our new mobile app!", CFG))

    def test_heuristic_requires_automated_local_part(self):
        # personal-looking local part + financial subject → still ignored
        self.assertIsNone(ff.classify_sender(
            "jane.doe@somebank.com", "your statement is ready", CFG))


class TestHistoryPolicy(unittest.TestCase):
    def test_mixed_security_and_marketing_refuses_blanket_filter(self):
        profile = {"total": 8, "security": 2, "marketing": 2, "material": 4}
        self.assertEqual(ff.recommend_sender_policy(profile, known=True), "mixed")

    def test_homogeneous_marketing_recommends_cleanup(self):
        profile = {"total": 3, "security": 0, "marketing": 3, "material": 0}
        self.assertEqual(ff.recommend_sender_policy(profile, known=True), "marketing")

    def test_homogeneous_known_records_recommend_archive(self):
        profile = {"total": 4, "security": 0, "marketing": 0, "material": 4}
        self.assertEqual(ff.recommend_sender_policy(profile, known=True), "archive")

    def test_single_message_is_not_enough_for_blanket_archive(self):
        profile = {"total": 1, "security": 0, "marketing": 0, "material": 1}
        self.assertEqual(ff.recommend_sender_policy(profile, known=True), "unknown")

    def test_any_security_history_blocks_marketing_filter(self):
        profile = {"total": 3, "security": 1, "marketing": 2, "material": 0}
        self.assertEqual(ff.recommend_sender_policy(profile, known=False), "mixed")

    def test_unclassified_history_never_earns_an_archive_rule(self):
        # Regression: unknown subjects used to count as "transactional", so two
        # ordinary notes from a person at a known domain (banker@cardd.example)
        # earned a one-tap "file this sender forever" proposal.
        profile = {"total": 2, "security": 0, "marketing": 0, "material": 0, "unknown": 2}
        self.assertNotEqual(ff.recommend_sender_policy(profile, known=True), "archive")
        profile = {"total": 5, "material": 4, "unknown": 1}
        self.assertEqual(ff.recommend_sender_policy(profile, known=True), "mixed")

    def test_unrecognised_sender_is_never_archived(self):
        profile = {"total": 4, "material": 4}
        self.assertEqual(ff.recommend_sender_policy(profile, known=False), "unknown")


class FakeGmail:
    """Just enough of the Gmail API client for the history/filter helpers."""

    def __init__(self, messages=()):
        self.messages_by_id = {m["id"]: m for m in messages}
        self.list_calls, self.created_filters, self.modified = [], [], []

    def users(self):
        return self

    def settings(self):
        return self

    def filters(self):
        return self

    def messages(self):
        return self

    def labels(self):
        class _Labels:
            def list(self, userId):
                return _Exec({"labels": [{"id": "L_LOW", "name": "Low Priority"}]})
        return _Labels()

    def list(self, **kw):
        self.list_calls.append(kw)
        ids = [{"id": mid} for mid in self.messages_by_id]
        return _Exec({"messages": ids})

    def get(self, userId, id, **kw):
        return _Exec(self.messages_by_id[id])

    def create(self, userId, body):
        self.created_filters.append(body)
        return _Exec({"id": "F-new"})

    def batchModify(self, userId, body):
        self.modified.append(body)
        return _Exec({})


class _Exec:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


def gmail_msg(mid, sender, subject, labels=("INBOX",)):
    return {"id": mid, "labelIds": list(labels), "payload": {"headers": [
        {"name": "From", "value": sender}, {"name": "Subject", "value": subject}]}}


class TestProfileSenderHistory(unittest.TestCase):
    def test_profile_uses_shared_classes_and_exact_address(self):
        svc = FakeGmail([
            gmail_msg("1", "Exchange A <noreply@exchangea.example>", "You bought BTC"),
            gmail_msg("2", "Exchange A <noreply@exchangea.example>", "Confirm your new withdrawal address"),
            # Gmail's from: also matches display names — this row must not count.
            gmail_msg("3", "noreply@exchangea.example via Survey <x@survey.io>", "Rate us!"),
        ])
        profile = ff.profile_sender_history(svc, "noreply@exchangea.example")
        self.assertEqual(profile["total"], 2)
        self.assertEqual((profile["security"], profile["material"], profile["marketing"]), (1, 1, 0))
        call = svc.list_calls[0]
        self.assertTrue(call["includeSpamTrash"])
        self.assertIn("-in:spam", call["q"])
        self.assertIn("newer_than:90d", call["q"])
        self.assertEqual(call["maxResults"], 30)


class TestTrashFilterSafety(unittest.TestCase):
    def test_trash_filter_never_removes_spam(self):
        # Regression: removeLabelIds included SPAM ("Never send it to Spam"),
        # so every one-tap unsubscribe re-created a spam bypass (policy 7).
        svc = FakeGmail()
        ff.create_junk_filter(svc, "promo@example.com")
        body = svc.created_filters[0]
        self.assertEqual(body["action"]["addLabelIds"], ["L_LOW"])   # junk is filed, never deleted
        self.assertNotIn("SPAM", body["action"].get("removeLabelIds", []))
        self.assertEqual(body["criteria"]["from"], "promo@example.com")

    def test_trash_filter_and_backlog_spare_security_mail(self):
        # Regression: noreply@photoapp.example's Trash rule trashed "Your PhotoApp Account -
        # New Device". Rule and backlog query both exclude security subjects.
        svc = FakeGmail()
        ff.create_junk_filter(svc, "noreply@photoapp.example")
        self.assertIn('"new device"', svc.created_filters[0]["criteria"]["negatedQuery"])
        q = ff.backlog_trash_query("noreply@photoapp.example")
        self.assertTrue(q.startswith("in:inbox from:noreply@photoapp.example -subject:("))


    def test_existing_junk_in_the_inbox_is_moved_not_deleted(self):
        svc = FakeGmail([{"id": "m1"}, {"id": "m2"}])
        self.assertEqual(ff.file_junk_from_sender(svc, "promo@example.com"), 2)
        self.assertEqual(svc.modified[0]["addLabelIds"], ["L_LOW"])
        self.assertEqual(svc.modified[0]["removeLabelIds"], ["INBOX"])
        self.assertNotIn("TRASH", str(svc.modified))


class TestDedup(unittest.TestCase):
    def test_created_blocks_reproposal(self):
        st = {}
        ff.mark_created("a@bank.com", "F1", st)
        self.assertEqual(ff.already_handled("a@bank.com", st), "filter already created")

    def test_declined_blocks_reproposal(self):
        st = {}
        ff.mark_declined("a@bank.com", st)
        self.assertEqual(ff.already_handled("a@bank.com", st), "declined by the user")

    def test_proposed_blocks_reproposal(self):
        st = {}
        ff.mark_proposed("a@bank.com", st)
        self.assertEqual(ff.already_handled("a@bank.com", st), "proposal already pending")

    def test_fresh_address_not_handled(self):
        self.assertIsNone(ff.already_handled("new@bank.com", {}))

    def test_mark_created_clears_proposed(self):
        st = {}
        ff.mark_proposed("a@bank.com", st)
        ff.mark_created("a@bank.com", "F1", st)
        self.assertNotIn("a@bank.com", ff._block(st)["proposed"])

    def test_expire_stale_proposals_clears_dead_dedup_entries(self):
        st = {
            "finance_filter": {"proposed": {"old@bank.com": 10, "live@bank.com": 10}},
            "unsub_filter": {"proposed": {"promo@example.com": 20}},
        }
        cleared = ff.expire_stale_proposals(
            st, now=1000, ttl=100, active_filter="live@bank.com")
        self.assertCountEqual(cleared, ["old@bank.com", "promo@example.com"])
        self.assertEqual(st["finance_filter"]["proposed"], {"live@bank.com": 10})
        self.assertEqual(st["unsub_filter"]["proposed"], {})


class TestExistingFilterCoverage(unittest.TestCase):
    def test_exact_address_covered(self):
        self.assertTrue(ff.existing_filter_covers("x@cardd.example", {"x@cardd.example"}))

    def test_domain_covered(self):
        self.assertTrue(ff.existing_filter_covers("new@info6.carda.example", {"carda.example"}))
        self.assertTrue(ff.existing_filter_covers("new@carda.example", {"carda.example"}))

    def test_unrelated_not_covered(self):
        self.assertFalse(ff.existing_filter_covers("x@cardd.example", {"carda.example", "cardc.example"}))

    def test_cache_ttl(self):
        st = {}
        ff.store_filter_cache(st, {"carda.example"})
        self.assertEqual(ff.cached_filter_addresses(st), {"carda.example"})
        ff._block(st)["filter_cache"]["ts"] = 0  # force stale
        self.assertIsNone(ff.cached_filter_addresses(st))


class TestUnsub(unittest.TestCase):
    def test_parse_list_unsubscribe_both(self):
        h = "<https://ex.com/u?t=abc>, <mailto:unsub@ex.com?subject=x>"
        p = ff.parse_list_unsubscribe(h)
        self.assertEqual(p["http"], "https://ex.com/u?t=abc")
        self.assertEqual(p["mailto"], "unsub@ex.com?subject=x")

    def test_parse_list_unsubscribe_http_only(self):
        p = ff.parse_list_unsubscribe("<https://ex.com/u>")
        self.assertEqual(p["http"], "https://ex.com/u")
        self.assertIsNone(p["mailto"])

    def test_parse_list_unsubscribe_empty(self):
        p = ff.parse_list_unsubscribe("")
        self.assertEqual(p, {"http": None, "mailto": None})

    def test_unsub_dedup(self):
        st = {}
        self.assertIsNone(ff.unsub_already_handled("a@x.com", st))
        ff.unsub_mark_proposed("a@x.com", st)
        self.assertEqual(ff.unsub_already_handled("a@x.com", st), "proposal already pending")
        ff.unsub_mark_declined("a@x.com", st)
        self.assertEqual(ff.unsub_already_handled("a@x.com", st), "kept by the user")
        ff.unsub_mark_created("b@x.com", "F9", st)
        self.assertEqual(ff.unsub_already_handled("b@x.com", st), "already unsubscribed")

    def test_unsub_block_separate_from_finance_block(self):
        st = {}
        ff.unsub_mark_declined("a@x.com", st)
        # the finance-filter flow must not see it as declined
        self.assertIsNone(ff.already_handled("a@x.com", st))

    def test_try_unsubscribe_http_no_link(self):
        self.assertIn("filter only", ff.try_unsubscribe_http("<mailto:u@x.com>"))


class TestRoutineReceiptsStayQuiet(unittest.TestCase):
    """The user 2026-10-01: "I am still getting notified about my recurring crypto
    buys. I used to have these skip my inbox."

    noreply@exchangea.example is caught by the no-reply L1 gate, but a KNOWN FINANCE
    sender overrides that gate — right for a statement or a fraud notice, wrong
    for a DCA receipt that fires three times a day. Registering Exchange A as a
    finance sender (2026-09-21, for the DCA report) is what started pushing
    "You bought BTC" through. `routine_re` lets a sender mark its own paperwork:
    still filed, still labelled, no longer loud."""

    CONFIG = {"senders": [{"name": "Exchange A", "area": "finance",
                           "domains": ["exchangea.example"], "label": "Finance/Crypto/Exchange A",
                           "routine_re": "^You (bought|sold) "}]}

    def verdict(self, subject):
        return ff.classify_sender("noreply@exchangea.example", subject, self.CONFIG)

    def test_the_routine_pattern_reaches_the_caller(self):
        self.assertEqual(self.verdict("You bought BTC")["routine_re"], "^You (bought|sold) ")

    def test_routine_receipts_are_still_filed_to_their_label(self):
        # Quiet must never mean unfiled — the label is what he actually relies on.
        for subject in ("You bought BTC", "You sold ETH"):
            with self.subTest(subject=subject):
                self.assertEqual(self.verdict(subject)["label"], "Finance/Crypto/Exchange A")

    def test_a_sender_without_the_field_is_unchanged(self):
        config = {"senders": [{"name": "Card B", "area": "finance",
                               "domains": ["cardb.example"], "label": "Finance/Crypto/Card B"}]}
        self.assertIsNone(
            ff.classify_sender("noreply@cardb.example", "You bought BTC", config)["routine_re"])


if __name__ == "__main__":
    unittest.main()
