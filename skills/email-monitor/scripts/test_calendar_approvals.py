import json
import sys
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

import confirm_cal_event as cal
import approval
import send_pending as email


_LAST_CLEANUP = None
_LAST_TELEGRAM = None


class _FakeCleanup:
    """chat_cleanup as the handler sees it: remove_now(mid, text, skill=..., reason=...)."""

    def __init__(self, outcome: str):
        global _LAST_CLEANUP
        self.outcome, self.calls = outcome, []
        _LAST_CLEANUP = self

    def remove_now(self, message_id, text, *, skill="", reason=""):
        self.calls.append((message_id, text, skill, reason))
        return self.outcome


class _FakeTelegram:
    def __init__(self):
        global _LAST_TELEGRAM
        self.edits = []
        _LAST_TELEGRAM = self

    def edit_message_text(self, message_id, text, **kw):
        self.edits.append((message_id, text))
        return True



class CalendarApprovalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.patches = [patch.object(approval, "DB_PATH", self.root / "approval.sqlite"),
                        patch.object(cal, "CAL_FILE", self.root / "pending.json")]
        for p in self.patches:
            p.start()
        self.proposal = {"gog_events": [{"summary": "Meeting", "from": "2026-09-15T10:00:00-05:00",
                                        "to": "2026-09-15T11:00:00-05:00", "location": "Zoom", "reminder_minutes": 15}]}
        cal.CAL_FILE.write_text(json.dumps(self.proposal))

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_no_unscoped_calendar_execution(self):
        with patch.object(sys, "argv", ["confirm_cal_event.py"]), patch.object(cal.subprocess, "run") as run:
            with self.assertRaises(ValueError):
                cal.main()
            run.assert_not_called()

    def test_changed_calendar_payload_never_executes(self):
        token = approval.issue("cal", "pending_cal_event", self.proposal)
        self.proposal["gog_events"][0]["summary"] = "Changed meeting"
        cal.CAL_FILE.write_text(json.dumps(self.proposal))
        with patch.object(sys, "argv", ["confirm_cal_event.py", "--approval-id", token]), patch.object(cal.subprocess, "run") as run:
            with self.assertRaises(ValueError):
                cal.main()
            run.assert_not_called()

    def test_verified_create_consumes_approval(self):
        token = approval.issue("cal", "pending_cal_event", self.proposal)
        result = CompletedProcess([], 0, json.dumps({"event": {"id": "event123"}}), "")
        with patch.object(sys, "argv", ["confirm_cal_event.py", "--approval-id", token]), \
             patch.object(cal.subprocess, "run", return_value=result) as run, \
             patch.object(cal, "_delete_google_duplicates", return_value=[]), \
             patch.object(cal, "_promote_next_queued"), patch.object(cal, "_finish"):
            cal.main()
            self.assertEqual(run.call_count, 2)
        self.assertEqual(approval.lookup(token)["status"], "completed")
        self.assertFalse(cal.CAL_FILE.exists())

    def test_an_approved_card_is_deleted_once_the_event_is_verified(self):
        """The user 2026-10-08: "when I approve a calendar card and it's on my calendar, can we
        delete the message? It's becoming clutter." """
        self.proposal["source_tg_msg_id"] = 4242
        self.proposal["proposal_text"] = "🎟 Event ticket detected"
        cal.CAL_FILE.write_text(json.dumps(self.proposal))
        token = approval.issue("cal", "pending_cal_event", self.proposal)
        result = CompletedProcess([], 0, json.dumps({"event": {"id": "event123"}}), "")
        with patch.object(sys, "argv", ["confirm_cal_event.py", "--approval-id", token]), \
             patch.object(cal.subprocess, "run", return_value=result), \
             patch.object(cal, "_delete_google_duplicates", return_value=[]), \
             patch.object(cal, "_promote_next_queued"), \
             patch.dict(sys.modules, {"chat_cleanup": _FakeCleanup("removed")}), \
             patch("builtins.print") as printed:
            cal.main()
        self.assertEqual([c[0] for c in _LAST_CLEANUP.calls], [4242])
        self.assertIn("Added to your calendar", _LAST_CLEANUP.calls[0][1])
        self.assertIn("NO_REPLY", [str(c.args[0]) for c in printed.call_args_list])

    def test_a_card_telegram_refuses_to_delete_is_edited_instead(self):
        self.proposal["source_tg_msg_id"] = 4243
        self.proposal["proposal_text"] = "🎟 Event ticket detected"
        cal.CAL_FILE.write_text(json.dumps(self.proposal))
        token = approval.issue("cal", "pending_cal_event", self.proposal)
        result = CompletedProcess([], 0, json.dumps({"event": {"id": "event123"}}), "")
        with patch.object(sys, "argv", ["confirm_cal_event.py", "--approval-id", token]), \
             patch.object(cal.subprocess, "run", return_value=result), \
             patch.object(cal, "_delete_google_duplicates", return_value=[]), \
             patch.object(cal, "_promote_next_queued"), \
             patch.dict(sys.modules, {"chat_cleanup": _FakeCleanup("archived_only")}), \
             patch.dict(sys.modules, {"send_telegram": _FakeTelegram()}), \
             patch("builtins.print"):
            cal.main()
        self.assertEqual(_LAST_TELEGRAM.edits and _LAST_TELEGRAM.edits[0][0], 4243,
                         "an outcome is never silently dropped")

    def test_auth_error_is_not_proof_of_deletion(self):
        with patch.object(cal.subprocess, "run", return_value=CompletedProcess([], 1, "", "invalid_grant")):
            self.assertFalse(cal._event_is_gone("primary", "event123"))
        with patch.object(cal.subprocess, "run", return_value=CompletedProcess([], 1, "", "404 not found")):
            self.assertTrue(cal._event_is_gone("primary", "event123"))

    def test_legacy_latest_cannot_send_email(self):
        state = self.root / "email.json"
        state.write_text(json.dumps({"gmail_pending": {"a": {"draft": "draft"}}}))
        with patch.object(email, "STATE_FILE", str(state)), patch.object(sys, "argv", ["send_pending.py", "--latest"]), patch.object(email, "get_service") as service:
            with self.assertRaises(SystemExit):
                email.main()
            service.assert_not_called()

    def test_email_cleanup_preserves_draft_changed_during_send(self):
        state = self.root / "email.json"
        entry = {"gmail_msg_id": "a", "draft": "Original", "to_email": "test@example.com", "subject": "Test", "thread_id": "thread"}
        state.write_text(json.dumps({"gmail_pending": {"a": entry}}))
        token = approval.issue("email", "a", entry)
        def finish_send():
            state.write_text(json.dumps({"gmail_pending": {"a": dict(entry, draft="New draft")}}))
            return {"id": "sent123"}
        with patch.object(email, "STATE_FILE", str(state)), patch.object(sys, "argv", ["send_pending.py", "--approval-id", token]), patch.object(email, "get_service") as service:
            service.return_value.users.return_value.messages.return_value.send.return_value.execute.side_effect = finish_send
            email.main()
        self.assertEqual(json.loads(state.read_text())["gmail_pending"]["a"]["draft"], "New draft")


if __name__ == "__main__":
    unittest.main()
