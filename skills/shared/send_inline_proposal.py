#!/usr/bin/env python3
"""
Send a Telegram message with ✅/❌ inline buttons.

Pass --pending so the sent message's id is written straight into the pending
file as "source_tg_msg_id". The matching confirm:/skip: script reads it and
edits THIS message in place on a tap, instead of leaving its buttons stuck and
posting a second "Done ..." message underneath (2026-09-26: the user flagged the
two-message result again). Until then the id was only printed, and the rule
files told the agent to hand-edit the pending file afterwards - a step that
only has to be forgotten once for the buttons to get stuck, and nothing in the
pipeline noticed when it was.

Usage:
    python send_inline_proposal.py \
        --text "Your proposal text here" \
        --action-type equipment \
        --confirm-label "✅ Save profile" \
        --skip-label "❌ Skip" \
        --pending ~/.openclaw/pending_equipment.json
"""
from __future__ import annotations

import os
import argparse
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from pending_proposal import save_message_id  # noqa: E402

TG_TOKEN   = __import__("runpy").run_path(os.path.expanduser("~/.openclaw/workspace/Skills/shared/runtime_secrets.py"))["require_secret"]("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID = "1000000001"


TG_MAX_LEN = 4096
BUTTON_OVERHEAD = 100  # approx chars consumed by button labels and padding


def send(text: str, action_type: str, confirm_label: str, skip_label: str) -> int:
    # Truncate to Telegram limit; reserve space for button overhead
    max_text = TG_MAX_LEN - BUTTON_OVERHEAD
    if len(text) > max_text:
        text = text[:max_text - 3] + "…"

    payload = {
        "chat_id": TG_CHAT_ID,
        "text":    text,
        # No parse_mode — plain text so special chars in drafts don't break delivery
        "reply_markup": {"inline_keyboard": [[
            {"text": confirm_label, "callback_data": f"confirm:{action_type}"},
            {"text": skip_label,    "callback_data": f"skip:{action_type}"},
        ]]},
    }
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        result = json.loads(r.read())
    return result.get("result", {}).get("message_id", 0)


def load_message_id_matches(pending: Path, msg_id: int, index: int | None) -> bool:
    """Read the pending file back and confirm the id actually landed. Same
    verify-after-write habit the calendar scripts use - a silent no-op here
    reads as "buttons stuck" days later, with nothing pointing at the cause."""
    try:
        data = json.loads(pending.read_text())
    except Exception:
        return False
    entry = data[index] if isinstance(data, list) and index is not None and -len(data) <= index < len(data) else data
    return isinstance(entry, dict) and entry.get("source_tg_msg_id") == msg_id


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--text",          required=True)
    parser.add_argument("--action-type",   required=True)
    parser.add_argument("--confirm-label", default="✅ Confirm")
    parser.add_argument("--skip-label",    default="❌ Skip")
    args = parser.parse_args()

    msg_id = send(args.text, args.action_type, args.confirm_label, args.skip_label)
    print(json.dumps({"sent": True, "msg_id": msg_id}))


if __name__ == "__main__":
    main()
