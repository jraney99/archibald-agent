#!/usr/bin/env python3
"""
Tests for revise_pending.py — the live "reply with your own text" path.

Run directly:  python3 test_revise_pending.py
Collected by run_email_policy_tests.py (globs test_*.py) and by the nightly
unittest discover.

Guards the two properties that matter: a revision must never send, and it must
invalidate the ✅ that would have sent the text the user rejected.
"""
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("revise_pending", HERE / "revise_pending.py")
rp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rp)

SIG = "Best,\n\nthe user\n555.010.0000\nLinkedIn"
ENTRY = {
    "gmail_msg_id": "abc123",
    "thread_id": "abc123",
    "to_email": "Sam@example.com",
    "subject": "Come see the venue",
    "rfc_msg_id": "<x@example.com>",
    "draft": f"Sam,\n\nThanks for the personal touch—I appreciate it.\n\n{SIG}",
    "created_at": 0.0,
    "approval_id": "old-approval",
    "source_tg_msg_id": 13960,
}


class FakeDaemon:
    """Stands in for daemon.py: real _with_signature/_strip_closing semantics,
    fake Telegram and state so no live file or chat is touched."""
    def __init__(self):
        self.state = {"gmail_pending": {"abc123": dict(ENTRY)},
                      "tg_pending": {"13960": dict(ENTRY)}}
        self.sent, self.edited, self.saved = [], [], 0
        self.proposals = []

    # state
    def _load(self): return json.loads(json.dumps(self.state))
    def _save(self, s): self.state = s; self.saved += 1

    # composition — mirrors the real helper
    def _get_signature(self): return SIG
    def _with_signature(self, draft, sig):
        import re
        lines = draft.rstrip().splitlines()
        while lines and (not lines[-1].strip()
                         or re.match(r"^(best|thanks|cheers)[,.!]?$", lines[-1].strip(), re.I)
                         or re.match(r"^[-–—]?\s*owner(\s+user)?[,.!]?$", lines[-1].strip(), re.I)):
            lines.pop()
        return "\n".join(lines).rstrip() + f"\n\n{sig}"

    # telegram
    def _h(self, t): return t
    def _display_name(self, s): return s
    def _tg_edit_message(self, mid, text): self.edited.append((mid, text))
    def _tg_send_with_buttons(self, text, kind, confirm_label=None, skip_label=None, proposal=None):
        self.sent.append(text)
        self.proposals.append(proposal)
        proposal["approval_id"] = "fresh-approval"
        return 99001
    def _gmail(self): raise AssertionError("no Gmail call expected in --text mode")


class ReviseTests(unittest.TestCase):
    def setUp(self):
        self.d = FakeDaemon()
        patcher = patch.object(rp, "_daemon", lambda: self.d)
        patcher.start(); self.addCleanup(patcher.stop)
        patch.object(rp, "_sender_name", lambda d, e: "Sam").start()

    def run_cli(self, *argv):
        return rp.main(list(argv))

    def test_typed_text_replaces_the_body_and_keeps_one_signature(self):
        rc = self.run_cli("--id", "abc123", "--text", "Sam,\n\nMonday at 9 works.")
        self.assertEqual(rc, 0)
        draft = self.d.state["gmail_pending"]["abc123"]["draft"]
        self.assertIn("Monday at 9 works.", draft)
        self.assertNotIn("personal touch", draft)
        self.assertEqual(draft.count("the user"), 1)

    def test_it_never_sends(self):
        self.run_cli("--id", "abc123", "--text", "Sam,\n\nMonday works.")
        # the only outbound call is a Telegram proposal, never Gmail
        self.assertEqual(len(self.d.sent), 1)
        self.assertIn("Draft revised", self.d.sent[0])

    def test_the_stale_button_is_retired_in_place(self):
        self.run_cli("--id", "abc123", "--text", "Sam,\n\nMonday works.")
        self.assertEqual(self.d.edited, [(13960, rp.REPLACED_NOTE)])

    def test_a_fresh_approval_is_issued_for_the_new_text(self):
        self.run_cli("--id", "abc123", "--text", "Sam,\n\nMonday works.")
        stored = self.d.state["gmail_pending"]["abc123"]
        self.assertEqual(stored["approval_id"], "fresh-approval")
        self.assertEqual(stored["source_tg_msg_id"], 99001)

    def test_old_tg_pending_copies_are_replaced_not_left_behind(self):
        self.run_cli("--id", "abc123", "--text", "Sam,\n\nMonday works.")
        tg = self.d.state["tg_pending"]
        self.assertNotIn("13960", tg)
        self.assertIn("99001", tg)
        self.assertEqual(tg["99001"]["draft"],
                         self.d.state["gmail_pending"]["abc123"]["draft"])

    def test_unknown_id_changes_nothing(self):
        rc = self.run_cli("--id", "nope", "--text", "hi")
        self.assertEqual(rc, 1)
        self.assertEqual(self.d.sent, [])
        self.assertEqual(self.d.saved, 0)

    def test_an_identical_revision_is_a_no_op(self):
        same = ENTRY["draft"].split("\n\nBest,")[0]
        rc = self.run_cli("--id", "abc123", "--text", same)
        self.assertEqual(rc, 0)
        self.assertEqual(self.d.sent, [])
        self.assertEqual(self.d.saved, 0)
        self.assertEqual(self.d.edited, [])

    def test_dry_run_touches_nothing(self):
        rc = self.run_cli("--id", "abc123", "--text", "Sam,\n\nNew text.", "--dry-run")
        self.assertEqual(rc, 0)
        self.assertEqual(self.d.sent, [])
        self.assertEqual(self.d.edited, [])
        self.assertEqual(self.d.saved, 0)

    def test_a_telegram_failure_does_not_store_the_draft(self):
        self.d._tg_send_with_buttons = lambda *a, **k: None
        rc = self.run_cli("--id", "abc123", "--text", "Sam,\n\nNew text.")
        self.assertEqual(rc, 2)
        self.assertEqual(self.d.saved, 0)
        self.assertIn("personal touch", self.d.state["gmail_pending"]["abc123"]["draft"])

    def test_text_and_redraft_are_mutually_exclusive(self):
        with self.assertRaises(SystemExit):
            self.run_cli("--id", "abc123", "--text", "x", "--redraft")


if __name__ == "__main__":
    unittest.main(verbosity=2)
