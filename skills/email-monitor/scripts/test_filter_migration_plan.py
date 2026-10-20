#!/usr/bin/env python3
"""Tests for the broad-rule → exact-address migration planner (pure step only)."""
import unittest

import email_policy as policy
import filter_migration_plan as planner

LABELS = {"INBOX": "INBOX", "TRASH": "TRASH", "LP": "Records/Packages",
          "LT": "Travel/Flights", "LN": "Records/Notifications", "LF": "Finance"}


def archive_filter(fid, frm, label):
    return {"id": fid, "criteria": {"from": frm},
            "action": {"addLabelIds": [label], "removeLabelIds": ["INBOX"]}}


def m(sender, subject, labels=("Records/Packages",)):
    return {"id": f"{sender}|{subject}", "from": sender, "subject": subject,
            "labels": list(labels), "date": "2026-09-01 10:00"}


LOCKER = archive_filter("F-locker", "@parcellocker.example", "LP")
LOCKER_MAIL = [
    m("Parcel Locker <info@outreach.parcellocker.example>", "Free samples headed your way!",
      ["Records/Packages", "CATEGORY_PROMOTIONS"]),
    m("Parcel Locker <info@outreach.parcellocker.example>", "Free samples headed your way! Sign up today!",
      ["Records/Packages", "CATEGORY_PROMOTIONS"]),
    m("Parcel Locker <support@parcellocker.example>", "You've got a package."),
    m("Parcel Locker <support@parcellocker.example>", "You've got a package."),
    m("Parcel Locker <support@parcellocker.example>", "You've got a package."),
]


def histories(messages):
    out = {}
    for msg in messages:
        out.setdefault(planner.sender_filter.extract_address(msg["from"]), []).append(msg)
    return out


def plan(filters, discovery):
    all_mail = [x for msgs in discovery.values() for x in msgs]
    return planner.build_plan(filters, LABELS, discovery, histories(all_mail),
                              {"info@outreach.parcellocker.example": {"http": "https://u", "mailto": None}})


class TestParcelLockerGap(unittest.TestCase):
    """The confirmed gap: Parcel Locker marketing archived instead of trashed."""

    def setUp(self):
        self.rule = plan([LOCKER], {"F-locker": LOCKER_MAIL})["rules"][0]
        self.by_addr = {s["address"]: s for s in self.rule["senders"]}

    def test_promo_address_gets_guarded_trash_and_unsubscribe(self):
        promo = self.by_addr["info@outreach.parcellocker.example"]
        self.assertEqual(promo["recommendation"], policy.TRASH)
        repl = promo["replacement"]
        self.assertEqual(repl["criteria"]["from"], "info@outreach.parcellocker.example")
        self.assertTrue(policy.has_security_guard(repl["criteria"]))
        self.assertNotIn("SPAM", repl["action"]["removeLabelIds"])
        self.assertEqual(repl["unsubscribe"]["http"], "https://u")

    def test_package_notices_keep_an_exact_archive_rule(self):
        pkg = self.by_addr["support@parcellocker.example"]
        self.assertEqual(pkg["recommendation"], policy.ARCHIVE)
        self.assertEqual(pkg["replacement"]["criteria"], {"from": "support@parcellocker.example"})
        self.assertEqual(pkg["replacement"]["action"],
                         {"addLabelIds": ["LP"], "removeLabelIds": ["INBOX"]})
        self.assertEqual(pkg["visibility_after"], [policy.BACKSTOP])

    def test_rule_is_replaced_by_exact_rules_only(self):
        self.assertEqual(self.rule["rule_action"], "replace")
        for repl in self.rule["recommended_replacement"]:
            self.assertIn("@", repl["criteria"]["from"])

    def test_plan_rows_carry_every_required_field(self):
        for key in ("filter_id", "domain", "exact_senders_observed", "counts",
                    "dedicated_paths", "recommended_replacement", "confidence", "reason"):
            self.assertIn(key, self.rule)
        for key in ("counts", "sample_subjects", "dedicated_paths", "recommendation",
                    "replacement", "confidence", "reason"):
            self.assertIn(key, self.rule["senders"][0])


class TestRecommendations(unittest.TestCase):
    def one(self, frm, label, mail):
        rule = plan([archive_filter("F", frm, label)], {"F": mail})["rules"][0]
        return rule, {s["address"]: s for s in rule["senders"]}

    def test_security_sender_stays_message_level(self):
        rule, senders = self.one("@accounts.google.com", "LN", [
            m("Google <no-reply@accounts.google.com>", "Security alert", ["Records/Notifications"]),
            m("Google <no-reply@accounts.google.com>", "Security alert", ["Records/Notifications"]),
        ])
        s = senders["no-reply@accounts.google.com"]
        self.assertEqual((s["recommendation"], s["confidence"]), (policy.MESSAGE_LEVEL, "high"))
        self.assertIsNone(s["replacement"])
        self.assertEqual(s["visibility_after"], "inbox")
        self.assertEqual(rule["rule_action"], "delete")

    def test_one_security_sample_blocks_marketing_trash(self):
        _, senders = self.one("@cardb.example", "LF", [
            m("Card B <no-reply@em.cardb.example>", "A new device is using your account", ["Finance"]),
            m("Card B <no-reply@em.cardb.example>", "Forgot about your Card B account?", ["Finance"]),
            m("Card B <no-reply@em.cardb.example>", "Your July rewards recap is here", ["Finance"]),
        ])
        self.assertEqual(senders["no-reply@em.cardb.example"]["recommendation"], policy.MESSAGE_LEVEL)

    def test_insufficient_history_is_removed_without_replacement(self):
        rule, senders = self.one("@ups.com", "LP", [m("UPS <pkginfo@ups.com>", "Your package was delivered")])
        s = senders["pkginfo@ups.com"]
        self.assertEqual((s["recommendation"], s["confidence"]), (policy.INSUFFICIENT, "low"))
        self.assertEqual(rule["rule_action"], "delete")

    def test_no_history_at_all_is_low_confidence_delete(self):
        rule = plan([archive_filter("F", "@intercom-vendor.example", "LN")], {"F": []})["rules"][0]
        self.assertEqual((rule["rule_action"], rule["confidence"]), ("delete", "low"))

    def test_travel_records_stay_visible_once_archived(self):
        # Itineraries go to the travel catch-up; a check-in reminder it won't
        # act on now falls to the backstop, so the stream is safe to archive.
        _, senders = self.one("@fly.spirit-airlines.com", "LT", [
            m("Spirit <booking@fly.spirit-airlines.com>", "Check in for tomorrow's flight!", ["Travel/Flights"]),
            m("Spirit <booking@fly.spirit-airlines.com>", "Modified Spirit Airlines Flight Confirmation: QWERTY",
              ["Travel/Flights"]),
        ])
        s = senders["booking@fly.spirit-airlines.com"]
        self.assertEqual(s["recommendation"], policy.ARCHIVE)
        self.assertEqual(set(s["visibility_after"]),
                         {policy.BACKSTOP, "daemon.travel_label_catchup"})

    def test_records_without_any_monitoring_path_are_not_archived(self):
        original = policy.visibility_path
        policy.visibility_path = lambda *a, **k: None   # simulate an uncovered stream
        try:
            _, senders = self.one("@ups.com", "LP", [
                m("UPS <pkginfo@ups.com>", "Your package was delivered"),
                m("UPS <pkginfo@ups.com>", "Your package is out for delivery")])
        finally:
            policy.visibility_path = original
        s = senders["pkginfo@ups.com"]
        self.assertEqual(s["recommendation"], policy.MESSAGE_LEVEL)
        self.assertIn("no backstop or catch-up path", s["reason"])

    def test_dedicated_watcher_is_reported(self):
        _, senders = self.one("@p2p.example", "LF", [
            m("P2P App <payments@p2p.example>", "A Friend paid you $75.00", ["Finance"]),
            m("P2P App <payments@p2p.example>", "Here's the September 2026 statement you requested", ["Finance"]),
        ])
        s = senders["payments@p2p.example"]
        self.assertEqual(s["recommendation"], policy.ARCHIVE)
        self.assertEqual(s["dedicated_paths"], ["cron.finance-p2p-watch"])
        self.assertEqual(set(s["visibility_after"]), {policy.BACKSTOP, "cron.finance-p2p-watch"})


class TestRuleScope(unittest.TestCase):
    def test_gmail_fuzzy_matches_are_listed_and_profiled(self):
        rule = plan([archive_filter("F", "@marriott.com", "LT")], {"F": [
            m("Marriott <reservations@res-marriott.com>",
              "Reservation Confirmation #70000001 for Example Suites", ["Travel/Flights"]),
        ]})["rules"][0]
        self.assertEqual(rule["gmail_fuzzy_matches"], ["reservations@res-marriott.com"])
        self.assertEqual(rule["senders"][0]["match"], "gmail-fuzzy")

    def test_overlapping_rules_create_one_replacement(self):
        mail = [m("Utility <billing@notifications.utility.example>", "Thanks for your payment, the user"),
                m("Utility <billing@notifications.utility.example>", "The user, your payments are now automatic")]
        result = plan([archive_filter("A", "@utility.example", "LP"),
                       archive_filter("B", "@notifications.utility.example", "LP")],
                      {"A": mail, "B": mail})
        self.assertEqual(result["replacement_filters"], 1)
        self.assertIn("overlapping rule A", result["rules"][1]["senders"][0]["note"])

    def test_existing_exact_filter_is_not_duplicated(self):
        mail = [m("A Contact <contact3@example.org>", "Order #10004 confirmed"),
                m("A Contact <contact3@example.org>", "A shipment from order #10004 has been delivered")]
        result = plan([archive_filter("E", "contact3@example.org", "LP"),
                       archive_filter("B", "@shop.example", "LP")], {"B": mail})
        self.assertEqual(result["broad_archive_rules"], 1)
        self.assertEqual(result["replacement_filters"], 0)
        self.assertIn("already named by exact filter(s) E", result["rules"][0]["senders"][0]["note"])


class TestApplySafety(unittest.TestCase):
    """apply_filter_migration.py must only ever execute the approved plan."""

    def setUp(self):
        import apply_filter_migration as apply
        self.apply = apply
        self.plan = plan([LOCKER], {"F-locker": LOCKER_MAIL})
        self.ops = apply.operations(self.plan)

    def test_operations_mirror_the_plan(self):
        self.assertEqual([d["id"] for d in self.ops["delete"]], ["F-locker"])
        self.assertEqual(sorted(c["address"] for c in self.ops["create"]),
                         ["info@outreach.parcellocker.example", "support@parcellocker.example"])
        self.assertEqual([u["address"] for u in self.ops["unsubscribe"]],
                         ["info@outreach.parcellocker.example"])
        for c in self.ops["create"]:
            self.assertNotIn("SPAM", c["action"].get("removeLabelIds", []))

    def test_changed_or_missing_live_rule_blocks_execution(self):
        self.assertEqual(self.apply.drift(self.ops, [LOCKER]), [])
        changed = dict(LOCKER, action={"addLabelIds": ["LT"], "removeLabelIds": ["INBOX"]})
        self.assertTrue(self.apply.drift(self.ops, [changed]))
        self.assertTrue(self.apply.drift(self.ops, []))

    def test_identical_filter_is_reused_not_duplicated(self):
        existing = {"id": "X1", "criteria": {"from": "support@parcellocker.example"},
                    "action": {"addLabelIds": ["LP"], "removeLabelIds": ["INBOX"]}}
        pkg = next(c for c in self.ops["create"] if c["address"] == "support@parcellocker.example")
        self.assertEqual(self.apply.already_present(pkg, [existing]), "X1")

    def test_gmail_normalised_trash_filter_counts_as_present(self):
        # Regression (2026-09-16 live run): Gmail stores a Trash rule without
        # removeLabelIds:["INBOX"], so the 4 new Trash filters verified as
        # "missing" and a re-run would have created duplicates.
        promo = next(c for c in self.ops["create"]
                     if c["address"] == "info@outreach.parcellocker.example")
        # New junk rules file under Low Priority; the normalisation still has
        # to hold for a Trash rule, so build one explicitly.
        self.assertNotIn("TRASH", str(promo["action"]))
        promo = dict(promo, action={"addLabelIds": ["TRASH"], "removeLabelIds": ["INBOX"]})
        as_gmail_stores_it = {"id": "G1", "criteria": promo["criteria"],
                              "action": {"addLabelIds": ["TRASH"]}}
        self.assertEqual(self.apply.already_present(promo, [as_gmail_stores_it]), "G1")
        self.assertEqual(self.apply.verify_end_state(
            {"create": [promo], "delete": []}, [as_gmail_stores_it]), [])

    def test_end_state_verification(self):
        self.assertTrue(self.apply.verify_end_state(self.ops, [LOCKER]))
        done = [{"id": f"N{i}", "criteria": c["criteria"], "action": c["action"]}
                for i, c in enumerate(self.ops["create"])]
        self.assertEqual(self.apply.verify_end_state(self.ops, done), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
