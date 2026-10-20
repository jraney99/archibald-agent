#!/usr/bin/env python3
"""The user can ask for an email, and it still takes his tap to send.

2026-10-13: asked to tell a golf venue he'd be 15 minutes late, the agent had no
route at all — every send path needed the daemon to have drafted a reply first.
The queue and buttons existed; nothing could write into them. These tests pin
the handle, and that a one-off note never becomes CRM history.

    python3 test_propose_email.py
"""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent.parent / "shared"))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


pe = _load("propose_email", _HERE / "propose_email.py")


class Validation(unittest.TestCase):
    def run_it(self, argv):
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = pe.main(argv)
        return code, json.loads(buf.getvalue())

    def test_a_bad_address_is_refused(self):
        code, out = self.run_it(["--to", "not-an-email", "--subject", "x",
                                 "--body", "hi", "--dry-run"])
        self.assertEqual(code, 1)
        self.assertEqual(out["status"], "bad_recipient")

    def test_an_empty_body_is_refused(self):
        code, out = self.run_it(["--to", "a@b.com", "--subject", "x",
                                 "--body", "   ", "--dry-run"])
        self.assertEqual(code, 1)
        self.assertEqual(out["status"], "empty_body")

    def test_a_dry_run_queues_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            pe.PENDING = Path(tmp) / "pending_crm.json"
            code, out = self.run_it(["--to", "a@b.com", "--subject", "x",
                                     "--body", "hi", "--dry-run"])
            self.assertEqual(code, 0)
            self.assertEqual(out["status"], "dry-run")
            self.assertFalse(pe.PENDING.exists())

    def test_note_is_the_default_flow(self):
        code, out = self.run_it(["--to", "a@b.com", "--subject", "x",
                                 "--body", "hi", "--dry-run"])
        self.assertEqual(out["entry"]["flow"], "note")


class Queueing(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        pe.PENDING = Path(self._tmp.name) / "pending_crm.json"

    def tearDown(self):
        self._tmp.cleanup()

    def _propose(self, to="a@b.com", body="hi"):
        import io, contextlib
        buf = io.StringIO()
        with mock.patch.object(pe, "send_button", return_value=4242), \
             contextlib.redirect_stdout(buf):
            code = pe.main(["--to", to, "--subject", "s", "--body", body])
        return code, json.loads(buf.getvalue())

    def test_the_draft_is_queued_with_its_message_id(self):
        code, out = self._propose()
        self.assertEqual((code, out["status"]), (0, "proposed"))
        entry = json.loads(pe.PENDING.read_text())[0]
        self.assertEqual(entry["source_tg_msg_id"], 4242)
        self.assertEqual(entry["to_email"], "a@b.com")

    def test_a_second_draft_queues_rather_than_stealing_the_buttons(self):
        self._propose(to="first@b.com")
        with mock.patch.object(pe, "send_button") as btn:
            code, out = self._propose(to="second@b.com")
        btn.assert_not_called()          # confirm:crm always acts on queue[0]
        self.assertEqual(out["status"], "queued_behind")
        queue = json.loads(pe.PENDING.read_text())
        self.assertEqual([e["to_email"] for e in queue], ["first@b.com", "second@b.com"])

    def test_nothing_is_sent_by_proposing(self):
        src = (_HERE / "propose_email.py").read_text()
        self.assertNotIn("gmail\", \"send", src)
        self.assertNotIn("messages().send", src)


class TheHandlerHonoursTheFlow(unittest.TestCase):
    SRC = (_HERE / "confirm_crm_email.py").read_text()

    def test_a_note_skips_the_crm_log(self):
        self.assertIn('if flow == "note":', self.SRC)
        note_branch = self.SRC.split('if flow == "note":')[1].split("else:")[0]
        self.assertNotIn("CRM_SCRIPT", note_branch,
                         "a one-off note still writes relationship history")

    def test_a_followup_still_logs(self):
        after = self.SRC.split('if flow == "note":')[1]
        self.assertIn("CRM_SCRIPT", after)
        self.assertIn("Sent follow-up email", after)

    def test_a_threadless_email_is_still_sent(self):
        # Requiring a thread made the queue reply-only, which is why the
        # late-arrival note had nowhere to go.
        self.assertIn('*(["--thread-id", thread_id] if thread_id else [])', self.SRC)
        self.assertNotIn("Entry missing thread_id or draft", self.SRC)


if __name__ == "__main__":
    unittest.main(verbosity=2)
