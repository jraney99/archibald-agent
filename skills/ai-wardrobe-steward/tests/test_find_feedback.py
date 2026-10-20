#!/usr/bin/env python3
"""👍/👎 on a scan find. Sandboxed, no sends.

2026-10-13: 18 recommendations had been surfaced and every outcome was "unknown". The user:
"im just not going to remember to send them via telegram" — so the finds he already receives
carry the feedback instead. Liking is NOT buying; the purchase watcher owns that fact.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

SANDBOX = tempfile.TemporaryDirectory()
os.environ["OPENCLAW_HOME"] = SANDBOX.name
os.environ["WARDROBE_NO_SEND"] = "1"
os.environ["TELEGRAM_SEND_DISABLED"] = "1"
SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS.parent.parent / "shared"))
sys.path.insert(0, str(SCRIPTS))
import send_find as sf  # noqa: E402
import find_feedback as ff  # noqa: E402

SB = Path(SANDBOX.name)
ff.DECISIONS = SB / "purchase_decisions.json"
ff.SIGNALS = SB / "style_signals.json"
assert str(sf.STORE).startswith(SANDBOX.name) and str(ff.STORE).startswith(SANDBOX.name)


class SendTests(unittest.TestCase):
    def test_one_row_per_find_with_its_own_token(self):
        records, kb = sf.build([("decision-1", "S&L cream crewneck"), ("decision-2", "PM navy sweater vest")])
        self.assertEqual(len(kb["inline_keyboard"]), 2)
        tokens = list(records)
        self.assertEqual(len(set(tokens)), 2, "each find gets its own button id")
        self.assertEqual([b["callback_data"] for b in kb["inline_keyboard"][0]],
                         [f"confirm:find-{tokens[0]}", f"skip:find-{tokens[0]}"])
        self.assertTrue(kb["inline_keyboard"][0][0]["text"].startswith("👍 S&L cream"))
        self.assertEqual(records[tokens[0]]["decision_id"], "decision-1")

    def test_at_most_three_finds(self):
        records, kb = sf.build([(f"d{i}", f"item {i}") for i in range(6)])
        self.assertEqual(len(kb["inline_keyboard"]), 3)

    def test_a_malformed_find_is_refused(self):
        with self.assertRaises(SystemExit):
            sf.main(["--text", "x", "--find", "no-pipe-here", "--dry-run"])
        with self.assertRaises(SystemExit):
            sf.main(["--text", "x", "--dry-run"])

    def test_a_find_whose_colour_was_not_measured_cannot_be_sent(self):
        """The user asked for a guarantee that his palette is checked on every find (2026-10-13).
        The cron used to say "when colour matters", which is judgment, not a guarantee —
        so the gate lives here, where it cannot be reasoned around."""
        for bad in ("d1|cream crewneck|S&L|merino",          # no colour at all
                    "d1|cream crewneck|S&L|merino|Cream",     # a NAME, not a measurement
                    "d1|cream crewneck|S&L|merino|#GGG"):     # not a hex
            with self.assertRaises(SystemExit, msg=bad):
                sf.main(["--text", "x", "--find", bad, "--dry-run"])

    def test_a_measured_hex_passes_and_so_does_a_deliberate_n_a(self):
        for ok in ("d1|cream crewneck|S&L|merino|#D2C7BB", "d1|madras check|S&L|cotton|n/a"):
            self.assertEqual(sf.main(["--text", "x", "--find", ok, "--dry-run"]), 0, ok)


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        records, _ = sf.build([("decision-1", "S&L cream crewneck"), ("decision-2", "PM navy vest")])
        self.tokens = list(records)
        for r in records.values():
            r["source_tg_msg_id"] = 77
        sf.STORE.parent.mkdir(parents=True, exist_ok=True)
        json.dump(records, open(sf.STORE, "w"))
        json.dump([{"id": "decision-1", "category": "sweater", "user_action": "unknown"}], open(ff.DECISIONS, "w"))
        json.dump([], open(ff.SIGNALS, "w"))

    def test_like_records_taste_and_a_signal_but_not_a_purchase(self):
        text, mid, _ = ff.record(self.tokens[0], "like")
        self.assertTrue(text.startswith("👍 Noted")); self.assertEqual(mid, 77)
        d = json.load(open(ff.DECISIONS))[0]
        self.assertEqual(d["taste"], "liked")
        self.assertEqual(d["user_action"], "unknown", "liking is not buying")
        sig = json.load(open(ff.SIGNALS))[0]
        self.assertEqual((sig["action"], sig["source"], sig["category"]), ("liked", "shopping-find-feedback", "sweater"))

    def test_dislike_and_second_tap(self):
        text, _, _ = ff.record(self.tokens[1], "dislike")
        self.assertTrue(text.startswith("👎 Noted"))
        self.assertTrue(ff.record(self.tokens[1], "like")[0].startswith("No open find"), "answered once only")

    def test_an_unknown_find_changes_nothing(self):
        text, mid, store = ff.record("ffffffffffff", "like")
        self.assertTrue(text.startswith("No open find")); self.assertIsNone(mid); self.assertIsNone(store)
        self.assertEqual(json.load(open(ff.SIGNALS)), [])

    def test_only_the_answered_find_loses_its_buttons(self):
        ff.record(self.tokens[0], "like")
        store = json.load(open(sf.STORE))
        kb = ff.keyboard_after(store)["inline_keyboard"]
        answered = [r for r in kb if len(r) == 1]
        still_open = [r for r in kb if len(r) == 2]
        self.assertEqual(len(answered), 1); self.assertEqual(len(still_open), 1)
        self.assertIn("✓", answered[0][0]["text"])
        self.assertTrue(still_open[0][0]["callback_data"].endswith(self.tokens[1]))


if __name__ == "__main__":
    unittest.main()
