#!/usr/bin/env python3
"""Regression tests for the read-only Gmail filter policy auditor."""
import unittest

import email_filter_audit as audit
import email_policy as policy

LABELS = {"INBOX": "INBOX", "SPAM": "SPAM", "TRASH": "TRASH", "L1": "Finance",
          "L2": "Finance/Credit Cards", "LT": "Travel/Flights", "LR": "Records/Packages"}


def filt(fid, from_value=None, add=(), remove=(), query=None, negated=None):
    criteria = {}
    if from_value:
        criteria["from"] = from_value
    if query:
        criteria["query"] = query
    if negated:
        criteria["negatedQuery"] = negated
    return {"id": fid, "criteria": criteria,
            "action": {"addLabelIds": list(add), "removeLabelIds": list(remove)}}


def msg(sender, subject, labels, mid=None):
    return {"id": mid or f"{sender}|{subject}", "from": sender, "subject": subject,
            "labels": labels}


class TestFilterAudit(unittest.TestCase):
    def base_filters(self):
        return [filt("substack", "@substack.com", add=["TRASH"])]

    def codes(self, report, level=None):
        return [x["code"] for x in report["issues"] if level in (None, x["level"])]

    def run_audit(self, extra_filters=(), messages=(), samples=None, daemon=None):
        return audit.audit(self.base_filters() + list(extra_filters), LABELS,
                           list(messages), samples or {}, daemon)

    # ── structural rules ──────────────────────────────────────────────────
    def test_required_substack_trash_rule_passes(self):
        report = self.run_audit()
        self.assertNotIn("required-filter-missing", self.codes(report))
        self.assertEqual(report["status"], "PASS")

    def test_duplicate_normalizes_at_prefix(self):
        fs = [filt("a", "example.com", add=["TRASH"]), filt("b", "@example.com", add=["TRASH"])]
        self.assertIn("duplicate-filter", self.codes(self.run_audit(fs)))

    def test_protected_finance_domain_trash_is_error(self):
        report = self.run_audit([filt("bad", "@custodya.example", add=["TRASH"])])
        self.assertIn("protected-domain-trash", self.codes(report, "error"))
        self.assertEqual(report["status"], "FAIL")

    def test_spam_bypass_is_an_error(self):
        # Regression: removing SPAM (Gmail's "Never send it to Spam") was only a
        # warning, so the audit passed while spam protection was overridden.
        report = self.run_audit([filt("s", "promo@example.com", add=["TRASH"],
                                      remove=["SPAM"])])
        self.assertIn("spam-bypass", self.codes(report, "error"))
        self.assertEqual(report["status"], "FAIL")

    def test_broad_domain_archive_is_a_gap(self):
        # Regression: broad archive rules were warnings, so 64 of them passed.
        report = self.run_audit([filt("broad", "@example.com", add=["L1"], remove=["INBOX"])])
        self.assertIn("broad-domain-archive", self.codes(report, "gap"))
        self.assertEqual(report["status"], "FAIL")

    def test_exact_address_archive_is_not_broad(self):
        report = self.run_audit([filt("ok", "alerts@info.carda.example", add=["L2"],
                                      remove=["INBOX"])])
        self.assertNotIn("broad-domain-archive", self.codes(report))

    def test_trash_rules_need_the_security_guard(self):
        bare = self.run_audit([filt("t", "noreply@photoapp.example", add=["TRASH"])])
        self.assertIn("trash-without-security-guard", self.codes(bare, "warning"))
        guarded = self.run_audit([filt("t", "noreply@photoapp.example", add=["TRASH"],
                                       negated=policy.TRASH_SECURITY_GUARD)])
        self.assertNotIn("trash-without-security-guard", self.codes(guarded))

    # ── what the filters actually matched ─────────────────────────────────
    def test_relevant_mail_in_trash_without_a_filter_is_a_warning(self):
        messages = [
            msg("Broker C <noreply@brokerc.example>", "Your recent login to Broker C", ["TRASH"]),
            msg("Marketplace <accountsecurity@marketplace.example>", "New Marketplace login on iPhone", ["TRASH"]),
            msg("Broker <records@broker.com>", "Your trade confirmation", ["TRASH"]),
            msg("Health Portal <hello@patient.healthportal.example>",
                "Your lab appointment is updated", ["TRASH"]),
            msg("VPN Service <no-reply@vpn.example>",
                "Dark Web Monitor alert: New data exposure detected", ["TRASH"]),
            msg("Southwest <southwestairlines@iluv.southwest.com>",
                "Your New York trip (XYZ789) is almost here!", ["TRASH"]),
        ]
        report = self.run_audit(messages=messages)
        # No Trash filter matches these — most likely deleted by hand.
        self.assertEqual(self.codes(report, "warning").count("relevant-mail-in-trash"), 6)
        self.assertNotIn("relevant-mail-trashed", self.codes(report))

    def test_trash_filter_sample_finds_trashed_security_alert(self):
        # Regression: noreply@photoapp.example's Trash rule trashed "Your PhotoApp Account -
        # New Device"; the 500-message recent sample never reached it.
        fs = [filt("photoapp", "noreply@photoapp.example", add=["TRASH"])]
        sample = [msg("PhotoApp <noreply@photoapp.example>", "Your PhotoApp Account - New Device", ["TRASH"])]
        report = self.run_audit(fs, samples={"photoapp": sample})
        self.assertIn("relevant-mail-trashed", self.codes(report, "error"))
        self.assertIn("mixed-sender-trash", self.codes(report, "error"))

    def test_guarded_trash_rule_does_not_match_security_mail(self):
        # After the photo-app rebuild the guard keeps "New Device" out of Trash; the
        # message already trashed by the old rule is only a warning.
        fs = [filt("photoapp2", "noreply@photoapp.example", add=["TRASH"], remove=["INBOX"],
                   negated=policy.TRASH_SECURITY_GUARD)]
        sample = [msg("PhotoApp <noreply@photoapp.example>", "Your PhotoApp Account - New Device", ["TRASH"])]
        report = self.run_audit(fs, samples={"photoapp2": sample})
        self.assertNotIn("mixed-sender-trash", self.codes(report))
        self.assertNotIn("relevant-mail-trashed", self.codes(report))
        self.assertIn("relevant-mail-in-trash", self.codes(report, "warning"))

    def test_repeated_surveys_in_inbox_are_a_gap(self):
        messages = [
            msg("Survey <feedback@example.com>", "How was your experience?", ["INBOX"]),
            msg("Survey <feedback@example.com>", "Reminder: take our survey", ["INBOX"]),
        ]
        self.assertIn("recurring-clutter-inbox", self.codes(self.run_audit(messages=messages)))

    def test_mixed_sender_trash_filter_fails(self):
        fs = [filt("bad", "noreply@broker.com", add=["TRASH"])]
        sample = [msg("Broker <noreply@broker.com>", "Security alert: new device", ["INBOX"]),
                  msg("Broker <noreply@broker.com>", "Limited time offer", ["TRASH"])]
        self.assertIn("mixed-sender-trash", self.codes(self.run_audit(fs, samples={"bad": sample})))

    def test_security_sender_archive_is_a_gap(self):
        fs = [filt("bad", "noreply@broker.com", remove=["INBOX"], add=["L1"])]
        sample = [msg("Broker <noreply@broker.com>", "Security alert: new device", ["Finance"]),
                  msg("Broker <noreply@broker.com>", "Limited time offer", ["Finance"])]
        report = self.run_audit(fs, samples={"bad": sample})
        self.assertIn("security-sender-auto-filed", self.codes(report, "gap"))

    def test_marketing_only_sender_should_not_be_finance_archived(self):
        fs = [filt("survey", "feedback@bank.com", add=["L1"], remove=["INBOX"])]
        sample = [msg("Bank <feedback@bank.com>", "How was your experience?", ["Finance"]),
                  msg("Bank <feedback@bank.com>", "Reminder: take our survey", ["Finance"])]
        codes = self.codes(self.run_audit(fs, samples={"survey": sample}))
        self.assertIn("marketing-auto-filed", codes)
        self.assertIn("finance-marketing-filed", codes)

    def test_mixed_purpose_auto_file_and_marketing_under_finance(self):
        # A bank's marketing address: card welcome (material) + ride promo under
        # Finance/Credit Cards — the old audit only knew security+marketing mixes.
        fs = [filt("card-d", query="from:(marketing@mail.cardd.example)", add=["L2"], remove=["INBOX"])]
        sample = [
            msg("Card D <marketing@mail.cardd.example>", "Get 50% off your next 2 rides",
                ["Finance/Credit Cards", "CATEGORY_PROMOTIONS"]),
            msg("Card D <marketing@mail.cardd.example>",
                "The user, welcome to your Card D Travel Rewards Credit Card.",
                ["Finance/Credit Cards"]),
        ]
        codes = self.codes(self.run_audit(fs, samples={"card-d": sample}), "gap")
        self.assertIn("mixed-purpose-auto-file", codes)
        self.assertIn("finance-marketing-filed", codes)

    def test_unverified_records_are_only_a_warning(self):
        fs = [filt("ins", "service@insurera.example", add=["LR"], remove=["INBOX"])]
        sample = [msg("Insurer A <service@insurera.example>", "Your bill is available.",
                      ["Records/Packages"]),
                  msg("Insurer A <service@insurera.example>",
                      "We're updating your telematics program consent", ["Records/Packages"])]
        report = self.run_audit(fs, samples={"ins": sample})
        self.assertIn("unverified-auto-file", self.codes(report, "warning"))
        self.assertEqual(report["status"], "PASS")

    def test_gmail_fuzzy_matches_are_judged_too(self):
        # `@marriott.com` also files reservations@res-marriott.com in Gmail.
        fs = [filt("mar", "@marriott.com", add=["LT"], remove=["INBOX"])]
        sample = [msg("Marriott <reservations@res-marriott.com>", "Limited time offer", ["Travel/Flights"]),
                  msg("Marriott <reservations@res-marriott.com>", "Last chance: save 20%", ["Travel/Flights"])]
        self.assertIn("marketing-auto-filed",
                      self.codes(self.run_audit(fs, samples={"mar": sample})))

    # ── coverage ───────────────────────────────────────────────────────────
    def test_archived_travel_update_is_now_covered_by_the_backstop(self):
        fs = [filt("sp", "booking@fly.spirit-airlines.com", add=["LT"], remove=["INBOX"])]
        sample = [msg("Spirit <booking@fly.spirit-airlines.com>",
                      "Check in for tomorrow's flight!", ["Travel/Flights"])]
        report = self.run_audit(fs, samples={"sp": sample})
        self.assertNotIn("monitoring-coverage-missing", self.codes(report))

    def test_a_record_with_no_monitoring_path_is_a_coverage_gap(self):
        fs = [filt("x", "notices@example.com", add=["LR"], remove=["INBOX"])]
        sample = [msg("X <notices@example.com>", "Your package was delivered", ["Records/Packages"])]
        original = policy.visibility_path
        policy.visibility_path = lambda *a, **k: None   # simulate a path that covers nothing
        try:
            report = self.run_audit(fs, samples={"x": sample})
        finally:
            policy.visibility_path = original
        self.assertIn("monitoring-coverage-missing", self.codes(report, "gap"))

    def test_covered_or_inbox_mail_is_not_a_coverage_gap(self):
        fs = [filt("sw", "southwestairlines@ifly.southwest.com", add=["LT"], remove=["INBOX"])]
        sample = [msg("SW <southwestairlines@ifly.southwest.com>",
                      "You're going to NYC (LaGuardia) on 09/01 (XYZ789)!", ["Travel/Flights"]),
                  msg("SW <southwestairlines@ifly.southwest.com>",
                      "Check in for tomorrow's flight!", ["Travel/Flights", "INBOX"])]
        self.assertNotIn("monitoring-coverage-missing",
                         self.codes(self.run_audit(fs, samples={"sw": sample})))

    def test_stale_or_stopped_daemon_is_flagged(self):
        stale = self.run_audit(daemon={"running": True, "pid": 1, "stale": True})
        self.assertIn("daemon-stale", self.codes(stale, "gap"))
        down = self.run_audit(daemon={"running": False})
        self.assertIn("daemon-not-running", self.codes(down, "error"))

    def test_hsa_statement_beats_promotions_label(self):
        fs = [filt("hsa", "auto_reply@mailer.hsaa.example", add=["TRASH"])]
        sample = [msg("HSA A <auto_reply@mailer.hsaa.example>",
                      "Your HSA statement is now available",
                      ["CATEGORY_PROMOTIONS", "TRASH"])]
        report = self.run_audit(fs, samples={"hsa": sample})
        self.assertIn("relevant-mail-trashed", self.codes(report, "error"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
