#!/usr/bin/env python3
"""
Regression test for loop_closer.py's close/keep-open edit-in-place
behavior. No network, no real MEMORY.md/state writes - MEMORY_PATH and
STATE_PATH are monkeypatched to temp files, and edit_or_fallback is
monkeypatched to avoid a real Telegram call.

    python3 test_loop_closer.py
"""
import json
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))
import loop_closer as lc  # noqa: E402

_fails = []


def check(name, got, want):
    if got != want:
        _fails.append(f"{name}: got {got!r}, want {want!r}")
    else:
        print(f"  ok  {name}")


SAMPLE_MEMORY = (
    "# Memory Index\n\n"
    "## Pending Actions\n\n"
    "- **🚨 CRITICAL/OVERDUE:** Submit the annual paperwork before the deadline\n\n"
    "## Other Section\n\n"
    "- something else\n"
)
ITEM_ID = lc._item_id("Submit the annual paperwork before the deadline")


def _fresh(state_extra=None):
    mem_tmp = Path(tempfile.mktemp())
    state_tmp = Path(tempfile.mktemp())
    mem_tmp.write_text(SAMPLE_MEMORY)
    item = {
        "text": "Submit the annual paperwork before the deadline",
        "first_flagged": "2026-06-01", "last_pinged": "2026-09-10",
        "ping_count": 3, "source_tg_msg_id": 888,
    }
    if state_extra:
        item.update(state_extra)
    state_tmp.write_text(json.dumps({"version": 1, "items": {ITEM_ID: item}}))
    return mem_tmp, state_tmp


def _with_patches(mem_tmp, state_tmp, fn):
    orig_mem, orig_state, orig_edit = lc.MEMORY_PATH, lc.STATE_PATH, lc.edit_or_fallback
    try:
        lc.MEMORY_PATH = mem_tmp
        lc.STATE_PATH = state_tmp
        return fn()
    finally:
        lc.MEMORY_PATH, lc.STATE_PATH, lc.edit_or_fallback = orig_mem, orig_state, orig_edit
        for p in (mem_tmp, state_tmp):
            if p.exists():
                p.unlink()


# --- close: removes from Pending Actions, edits proposal msg, drops state -
mem_tmp, state_tmp = _fresh()


def _t():
    seen = []
    lc.edit_or_fallback = lambda mid, text: seen.append((mid, text)) or "NO_REPLY"
    out = lc.cmd_close(ITEM_ID, "done", "")
    check("close: returns NO_REPLY (edited in place)", out, "NO_REPLY")
    check("close: edited using the escalation's own message id", seen[0][0], 888)
    check("close: result text mentions the item", "paperwork" in seen[0][1], True)
    mem_after = mem_tmp.read_text()
    check("close: bullet removed from Pending Actions", "CRITICAL/OVERDUE" not in mem_after, True)
    check("close: logged under Resolved (Loop Closer)", "Resolved (Loop Closer)" in mem_after, True)
    state_after = json.loads(state_tmp.read_text())
    check("close: item removed from state", ITEM_ID in state_after["items"], False)


_with_patches(mem_tmp, state_tmp, _t)

# --- keep-open: leaves bullet in place, edits in place, snoozes -----------
mem_tmp, state_tmp = _fresh()


def _t():
    seen = []
    lc.edit_or_fallback = lambda mid, text: seen.append((mid, text)) or "NO_REPLY"
    out = lc.cmd_keep_open(ITEM_ID, "")
    check("keep-open: returns NO_REPLY (edited in place)", out, "NO_REPLY")
    check("keep-open: edited using the escalation's own message id", seen[0][0], 888)
    check("keep-open: result mentions still open + snooze", "still open" in seen[0][1], True)
    mem_after = mem_tmp.read_text()
    check("keep-open: bullet still present, tagged confirmed-still-open",
          "CRITICAL/OVERDUE" in mem_after and "confirmed still open" in mem_after, True)
    state_after = json.loads(state_tmp.read_text())
    check("keep-open: item still tracked with a snooze date",
          "snooze_until" in state_after["items"][ITEM_ID], True)


_with_patches(mem_tmp, state_tmp, _t)

# --- no matching item: falls back cleanly, no crash ------------------------
mem_tmp, state_tmp = _fresh()


def _t():
    out = lc.cmd_close("nonexistent", "done", "")
    check("close: unknown id -> plain message, no crash",
          "No matching open item" in out, True)


_with_patches(mem_tmp, state_tmp, _t)

# --- cmd_mark_pinged stores the message id for later editing --------------
mem_tmp, state_tmp = _fresh()


def _t():
    lc.cmd_mark_pinged(ITEM_ID, 999)
    state_after = json.loads(state_tmp.read_text())
    check("mark_pinged: stores source_tg_msg_id for the next escalation",
          state_after["items"][ITEM_ID]["source_tg_msg_id"], 999)


_with_patches(mem_tmp, state_tmp, _t)

if _fails:
    print("\nFAILURES:")
    for f in _fails:
        print("  " + f)
    sys.exit(1)
print("\nall pass")
