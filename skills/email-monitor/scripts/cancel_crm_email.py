#!/usr/bin/env python3
"""
Skip the first pending CRM draft and advance to the next.
Called by Archibald when user taps ❌ (skip:crm).
"""
import os
import json, sys, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(os.path.expanduser("~/.openclaw/workspace/Skills/shared")).expanduser()))
from pending_proposal import edit_or_fallback  # noqa: E402

PENDING    = Path(os.path.expanduser("~/.openclaw/pending_crm.json")).expanduser()
TG_TOKEN   = __import__("runpy").run_path(os.path.expanduser("~/.openclaw/workspace/Skills/shared/runtime_secrets.py"))["require_secret"]("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID = "1000000001"


def send_next_button(entry: dict) -> None:
    """Sends the next queued entry's button and stashes its message_id onto
    that same entry (mirrors confirm_crm_email.py's copy of this function)."""
    contact = entry.get("contact_name", entry.get("to_email", "?"))
    draft   = entry.get("draft", "")[:300]
    text    = f"✉️ Draft for {contact}:\n\n{draft}"
    if len(text) > 3990:
        text = text[:3990] + "…"
    payload = {
        "chat_id": TG_CHAT_ID,
        "text":    text,
        "reply_markup": {"inline_keyboard": [[
            {"text": "✅ Send", "callback_data": "confirm:crm"},
            {"text": "❌ Skip", "callback_data": "skip:crm"},
        ]]},
    }
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            result = json.loads(r.read())
        msg_id = result.get("result", {}).get("message_id")
        if msg_id:
            entry["source_tg_msg_id"] = msg_id
    except Exception:
        pass


def main():
    if not PENDING.exists():
        print("No pending CRM draft.")
        sys.exit(0)

    raw = json.loads(PENDING.read_text())
    queue = raw if isinstance(raw, list) else [raw]

    if not queue:
        PENDING.unlink(missing_ok=True)
        print("Queue empty.")
        sys.exit(0)

    skipped = queue.pop(0)
    contact = skipped.get("contact_name", skipped.get("to_email", "?"))
    msg_id  = skipped.get("source_tg_msg_id")

    if queue:
        send_next_button(queue[0])  # mutates queue[0] in place with its own source_tg_msg_id
        PENDING.write_text(json.dumps(queue, indent=2))
        print(edit_or_fallback(msg_id, f"❌ Skipped {contact}. {len(queue)} more draft(s) — next button sent. 🎩"))
    else:
        PENDING.unlink(missing_ok=True)
        print(edit_or_fallback(msg_id, f"❌ Skipped {contact}. No more drafts. 🎩"))


if __name__ == "__main__":
    main()
