#!/usr/bin/env python3
"""
Regression test for confirm_crm_email.py's queue-advance + edit-in-place
logic. No real email send / no real Telegram call - gog and urlopen are
monkeypatched.

    python3 test_confirm_crm_email.py
"""
import os
import tempfile
os.environ["OPENCLAW_HOME"] = tempfile.mkdtemp(prefix="crm-compat-")
os.environ["TELEGRAM_SEND_DISABLED"] = "1"
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))
import confirm_crm_email as cce  # noqa: E402

_fails = []


def check(name, got, want):
    if got != want:
        _fails.append(f"{name}: got {got!r}, want {want!r}")
    else:
        print(f"  ok  {name}")


def _fake_gog_success(cmd, **kw):
    return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")


def _run(queue, tmp):
    tmp.write_text(json.dumps(queue))
    orig_pending = cce.PENDING
    cce.PENDING = tmp
    import io
    import contextlib
    buf = io.StringIO()
    try:
        with mock.patch.object(cce.subprocess, "run", side_effect=_fake_gog_success), \
             contextlib.redirect_stdout(buf):
            cce.main()
    finally:
        cce.PENDING = orig_pending
    return buf.getvalue().strip()


# --- single-entry queue: sends, edits the tapped message, clears file -----
tmp = Path(tempfile.mktemp())
try:
    queue = [{
        "thread_id": "t1", "to_email": "a@example.com", "draft": "hi",
        "contact_name": "Alice", "flow": "reengage", "subject": "Catching up",
        "source_tg_msg_id": 111,
    }]
    with mock.patch.object(cce, "edit_or_fallback", return_value="NO_REPLY") as m:
        out = _run(queue, tmp)
        check("single entry: edit_or_fallback called with the tapped message's id",
              m.call_args[0][0], 111)
        check("single entry: result text mentions the contact",
              "Alice" in m.call_args[0][1], True)
    check("single entry: prints NO_REPLY", out, "NO_REPLY")
    check("single entry: pending file cleared", tmp.exists(), False)
finally:
    if tmp.exists():
        tmp.unlink()

# --- multi-entry queue: advances, next button gets its own new message ----
tmp = Path(tempfile.mktemp())
try:
    queue = [
        {"thread_id": "t1", "to_email": "a@example.com", "draft": "hi",
         "contact_name": "Alice", "flow": "reengage", "subject": "Catching up",
         "source_tg_msg_id": 111},
        {"thread_id": "t2", "to_email": "b@example.com", "draft": "hey",
         "contact_name": "Bob", "flow": "reengage", "subject": "Long time"},
    ]
    with mock.patch.object(cce, "send_next_button", side_effect=lambda e: e.__setitem__("source_tg_msg_id", 222)) as sender, \
         mock.patch.object(cce, "edit_or_fallback", return_value="NO_REPLY") as m:
        out = _run(queue, tmp)
        check("multi entry: send_next_button called once for the remaining entry",
              sender.call_count, 1)
        check("multi entry: edited the message for the JUST-CONFIRMED entry (111), not the next one",
              m.call_args[0][0], 111)
        check("multi entry: result text mentions both the send and the queue advance",
              "Alice" in m.call_args[0][1] and "more draft" in m.call_args[0][1], True)
    saved = json.loads(tmp.read_text())
    check("multi entry: remaining queue persisted with the new button's id",
          saved, [{"thread_id": "t2", "to_email": "b@example.com", "draft": "hey",
                   "contact_name": "Bob", "flow": "reengage", "subject": "Long time",
                   "source_tg_msg_id": 222}])
finally:
    if tmp.exists():
        tmp.unlink()

# --- send failure: entry requeued, original button NOT cleared (retryable) -
tmp = Path(tempfile.mktemp())
try:
    queue = [{"thread_id": "t1", "to_email": "a@example.com", "draft": "hi",
              "contact_name": "Alice", "flow": "reengage", "subject": "Catching up",
              "source_tg_msg_id": 111}]

    def _fake_gog_fail(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="quota exceeded")

    with mock.patch.object(cce, "edit_or_fallback") as m:
        tmp.write_text(json.dumps(queue))
        orig_pending = cce.PENDING
        cce.PENDING = tmp
        try:
            with mock.patch.object(cce.subprocess, "run", side_effect=_fake_gog_fail):
                cce.main()
        except SystemExit as e:
            check("send failure: exits nonzero", e.code, 2)
        finally:
            cce.PENDING = orig_pending
        check("send failure: edit_or_fallback NOT called (buttons stay live for retry)",
              m.called, False)
    check("send failure: entry put back in the queue", json.loads(tmp.read_text()), queue)
finally:
    if tmp.exists():
        tmp.unlink()

if _fails:
    print("\nFAILURES:")
    for f in _fails:
        print("  " + f)
    sys.exit(1)
print("\nall pass")
