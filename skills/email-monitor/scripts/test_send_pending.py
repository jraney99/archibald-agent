#!/usr/bin/env python3
"""
Regression test for send_pending.py's --from-callback branching. No real
Gmail send - get_service() is monkeypatched.

This script is used by TWO different callers with different expectations:
  1. RULES.md's bare-✅-in-free-text rule: calls it with no flag, and parses
     the exact "SENT: <id> -> <email>" stdout to compose its own reply.
  2. confirm:email's dispatch-table entry (a real button tap): calls it with
     --from-callback, which must edit the original message in place and
     print NO_REPLY instead - see feedback about tapping a button leaving a
     stuck button + a redundant new message.
Getting this wrong either breaks the "✅ ALONE" rule (RULES.md, marked
non-negotiable) or reintroduces the double-message bug for the other path -
so this gets a real test rather than compile-check-only confidence.

    python3 test_send_pending.py
"""
import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))
import send_pending as sp  # noqa: E402
import approval

_fails = []


def check(name, got, want):
    if got != want:
        _fails.append(f"{name}: got {got!r}, want {want!r}")
    else:
        print(f"  ok  {name}")


class _FakeMessages:
    def send(self, userId, body):
        return self

    def execute(self):
        return {"id": "sent123"}


class _FakeUsers:
    def messages(self):
        return _FakeMessages()


class _FakeService:
    def users(self):
        return _FakeUsers()


def _run(argv, entry_extra=None):
    """Run send_pending.main() against a fresh fake state file, capturing
    stdout. Returns (stdout, state_after)."""
    state_path = Path(tempfile.mktemp())
    entry = {
        "to_email": "someone@example.com",
        "draft": "hi",
        "subject": "Re: thing",
        "thread_id": "t1",
        "rfc_msg_id": "",
    }
    if entry_extra:
        entry.update(entry_extra)
    approval_patch = mock.patch.object(approval, "DB_PATH", state_path.with_suffix(".sqlite"))
    approval_patch.start()
    token = approval.issue("email", "gm1", entry)
    argv = ["--approval-id", token] + [a for a in argv if a == "--from-callback"]
    state_path.write_text(json.dumps({"gmail_pending": {"gm1": entry}, "tg_pending": {}}))

    import io
    import contextlib
    orig_state_file = sp.STATE_FILE
    orig_argv = sys.argv
    sp.STATE_FILE = str(state_path)
    sys.argv = ["send_pending.py"] + argv
    buf = io.StringIO()
    try:
        with mock.patch.object(sp, "get_service", return_value=_FakeService()), \
             contextlib.redirect_stdout(buf):
            sp.main()
    finally:
        sp.STATE_FILE = orig_state_file
        sys.argv = orig_argv
        state_after = json.loads(state_path.read_text())
        state_path.unlink()
        approval_patch.stop()
        state_path.with_suffix(".sqlite").unlink(missing_ok=True)
    return buf.getvalue().strip(), state_after


# --- free-text path (no flag): exact format RULES.md parses, unchanged ----
edited_calls = []
with mock.patch.object(sp, "edit_or_fallback", side_effect=lambda mid, text: edited_calls.append((mid, text)) or "SHOULD_NOT_BE_USED"):
    out, state = _run(["gm1"])
check("no --from-callback: exact 'SENT: <id> -> <email>' format",
      out, "SENT: sent123 → someone@example.com")
check("no --from-callback: edit_or_fallback never called (free-text path untouched)",
      edited_calls, [])
check("no --from-callback: gmail_pending entry removed", "gm1" in state["gmail_pending"], False)

# --- --from-callback, with a stored button message id: edits in place -----
with mock.patch.object(sp, "edit_or_fallback", return_value="NO_REPLY") as m:
    out, state = _run(["gm1", "--from-callback"], entry_extra={"source_tg_msg_id": 999})
    check("--from-callback + source_tg_msg_id: edit_or_fallback called with that id",
          m.call_args[0][0], 999)
check("--from-callback + source_tg_msg_id: prints NO_REPLY sentinel", out, "NO_REPLY")

# --- --from-callback, no stored message id: falls back to a normal reply --
with mock.patch.object(sp, "edit_or_fallback", wraps=sp.edit_or_fallback) as m:
    out, state = _run(["gm1", "--from-callback"])  # no source_tg_msg_id in entry
    check("--from-callback + no source_tg_msg_id: edit_or_fallback called with None",
          m.call_args[0][0], None)
check("--from-callback + no source_tg_msg_id: falls back to the ✅ Sent text",
      out, "✅ Sent to someone@example.com. Gmail ID: sent123 🎩")

# --- --latest still works with the flag present anywhere in argv ----------
with mock.patch.object(sp, "edit_or_fallback", return_value="NO_REPLY"):
    out, state = _run(["--latest", "--from-callback"], entry_extra={"source_tg_msg_id": 42})
check("scoped approval resolves and edits normally", out, "NO_REPLY")

if _fails:
    print("\nFAILURES:")
    for f in _fails:
        print("  " + f)
    sys.exit(1)
print("\nall pass")
