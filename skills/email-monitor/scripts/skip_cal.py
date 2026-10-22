#!/usr/bin/env python3
"""
Skip the pending calendar event proposal.
Called by Archibald when the user taps ❌ (skip:cal). Deletes pending_cal_event.json
and, if another proposal was waiting behind it (see daemon.py's
_publish_cal_proposal / confirm_cal_event.py's _promote_next_queued), promotes
it into the now-empty slot so it isn't silently lost.
"""
import os
import json
import sys
from pathlib import Path

sys.path.insert(0, os.path.expanduser("~/.openclaw/workspace/Skills/email-monitor/scripts"))
sys.path.insert(0, os.path.expanduser("~/.openclaw/workspace/Skills/shared"))
from approval import require_cli, finish

CAL_FILE       = Path(os.path.expanduser("~/.openclaw/pending_cal_event.json")).expanduser()
CAL_QUEUE_FILE = Path(os.path.expanduser("~/.openclaw/pending_cal_queue.json")).expanduser()


def _promote_next_queued() -> None:
    if not CAL_QUEUE_FILE.exists():
        return
    try:
        queue = json.loads(CAL_QUEUE_FILE.read_text())
    except Exception:
        queue = []
    import daemon as _d
    # Drop any queued proposals that have gone stale (event already passed /
    # sat unconfirmed too long) rather than promoting a dead one into the slot.
    while queue:
        stale, why = _d._cal_proposal_should_drop(queue[0])
        if not stale:
            break
        _d._log("Cal", f"skipped promoting stale queued proposal ({why}): "
                       f"{_d._first_line(queue[0])}")
        queue.pop(0)
    if not queue:
        CAL_QUEUE_FILE.unlink(missing_ok=True)
        return
    nxt, rest = queue[0], queue[1:]
    # Send this proposal's real buttons now, on promotion — it was queued
    # with a plain no-button notice (see daemon.py's _publish_cal_proposal)
    # specifically so nothing was tappable while a different proposal still
    # occupied CAL_FILE.
    msg_id = _d._tg_send_with_buttons(nxt["proposal_text"], "cal", proposal=nxt)
    if msg_id:
        nxt["source_tg_msg_id"] = msg_id
    CAL_FILE.write_text(json.dumps(nxt))
    if rest:
        CAL_QUEUE_FILE.write_text(json.dumps(rest))
    else:
        CAL_QUEUE_FILE.unlink(missing_ok=True)


def main():
    if not CAL_FILE.exists():
        print("No pending calendar event.")
        return

    try:
        proposal = json.loads(CAL_FILE.read_text())
    except Exception:
        proposal = {}

    token = require_cli("cal", "pending_cal_event", proposal)
    CAL_FILE.unlink(missing_ok=True)
    finish(token, "skipped")
    _promote_next_queued()

    # Edit the original proposal message in place (drop its buttons) rather than
    # leaving them tappable under a separate "Skipped." reply.
    src = proposal.get("source_tg_msg_id")
    orig = (proposal.get("proposal_text") or "").strip()
    if src and orig:
        try:
            from send_telegram import edit_message_text
            if edit_message_text(src, f"{orig}\n\n❌ Skipped"):
                print("NO_REPLY")
                return
        except Exception:
            pass
    print("Skipped.")


if __name__ == "__main__":
    main()
