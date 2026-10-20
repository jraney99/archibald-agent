"""
Shared helper for the edit-in-place-on-confirm pattern used across every
confirm:*/skip:* Telegram proposal in this workspace.

2026-09-13/14: The user flagged that tapping a proposal's button left the
original button visibly stuck AND posted a separate "Logged/Sent/Done ..."
message underneath it - "report" mode always posts the script's stdout as a
brand new message, and most confirm scripts never touched the original
message at all. Fixed first for meals (log_meals.py), then generalized here
so every other confirm:* flow can adopt the same one-message result with a
couple of lines instead of duplicating the Telegram call each time.

Field name (`source_tg_msg_id`) matches the convention `send_proposal.py` /
`confirm_cal_event.py` already established, rather than inventing a second
one - existing pending files that already carry this field (cal, diary, uv)
work with this module unchanged.

Usage (sender side, right after sending the proposal and getting its
message_id back):
    from pending_proposal import save_message_id
    save_message_id(PENDING_FILE, msg_id)

Usage (confirm side, once you have your final human-readable result text):
    from pending_proposal import load_message_id, edit_or_fallback
    msg_id = load_message_id(PENDING_FILE)   # read BEFORE unlinking the file
    print(edit_or_fallback(msg_id, "✅ Whatever happened. 🎩"))
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import send_telegram  # noqa: E402


def save_message_id(pending_path: Path, message_id, index: int | None = None) -> None:
    """Merge message_id into an existing pending-proposal JSON file. No-op
    (never raises) if the file is missing/malformed, so a sender script's own
    error handling isn't disturbed.

    A dict-shaped file gets `source_tg_msg_id` at the top level. A LIST-shaped
    file (a queue, e.g. pending_crm.json's array of drafts) needs `index` to
    say which entry the button belongs to - the queue's confirm script reads
    the id off the entry it is currently showing, not off the array. Passing
    `index` for a dict, or omitting it for a list, is a no-op rather than a
    crash: a mis-wired sender must not take the proposal down with it.
    """
    if not message_id:
        return
    try:
        data = json.loads(pending_path.read_text())
    except Exception:  # noqa: BLE001
        return
    if isinstance(data, dict):
        if index is not None:
            return
        data["source_tg_msg_id"] = message_id
    elif isinstance(data, list):
        if index is None or not (-len(data) <= index < len(data)):
            return
        if not isinstance(data[index], dict):
            return
        data[index]["source_tg_msg_id"] = message_id
    else:
        return
    try:
        pending_path.write_text(json.dumps(data, indent=2))
    except Exception:  # noqa: BLE001
        pass


def load_message_id(pending_path: Path):
    """Read message_id out of a pending-proposal file. Call this BEFORE
    unlinking the file (most confirm scripts delete it once they've read
    what they need). Returns None on any missing/malformed case."""
    try:
        data = json.loads(pending_path.read_text())
    except Exception:  # noqa: BLE001
        return None
    if isinstance(data, dict):
        return data.get("source_tg_msg_id")
    return None


def edit_or_fallback(message_id, text: str, drop_buttons: bool = True, keep: bool = False) -> str:
    """Edit the original proposal message in place if a message_id is
    available and the edit succeeds. Returns the literal string "NO_REPLY"
    in that case - print it as-is so callback-dispatch's reply:"script" mode
    relays the sentinel and stays silent instead of posting a second
    message. Falls back to returning `text` unchanged (caller should just
    print it) when there's no message_id or the edit fails, so nothing is
    ever silently dropped.

    `drop_buttons=False` leaves the message's existing inline keyboard
    alone (Telegram keeps whatever reply_markup a message already has when
    editMessageText's own reply_markup is omitted) - for a "skip" that
    means "keep this pending, decide later" rather than "this is over",
    e.g. physician-journal's "kept as a draft, tap Send whenever you're ready"."""
    if message_id and send_telegram.edit_message_text(message_id, text, drop_buttons=drop_buttons, keep=keep):
        return "NO_REPLY"
    return text

def report(message_id, text: str) -> int:
    """Say what happened ON the tapped message and give callback-dispatch the exit
    code that means "handled". Always returns 0.

    WHY (2026-10-13): callback-dispatch treats a non-zero exit as a dispatch
    failure and posts `⚠️ <token> — <stdout>`. A handler that had reported its
    outcome perfectly well and then exited non-zero to mean "the thing didn't
    happen" therefore showed the user the raw sentinel:

        ⚠️ confirm:txnsplit — NO_REPLY

    An outcome of "not saved, here is why" IS a successful handling — the message
    says so. Non-zero is for the case where the handler could not report at all.
    """
    print(edit_or_fallback(message_id, text))
    return 0
