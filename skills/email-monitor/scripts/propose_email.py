#!/usr/bin/env python3
"""Draft an email for the user to approve. Sends nothing.

WHY (2026-10-13): The user asked the agent to tell a venue he would be 15
minutes late. It could not — every send path required the daemon to have
drafted a reply to an incoming message first, so an email the user ASKS for had no
route. The machinery was already here: pending_crm.json + confirm:crm/skip:crm
send whatever draft is in the queue when he taps. What was missing was anything
that writes into it — a door with no handle. This is the handle.

Nothing here sends. It appends a draft to the queue and sends the user the ✅/❌,
exactly like the CRM drafts that already use it.

    python3 propose_email.py --to alex@example.com --subject "Running late" \
        --body "Hi Alex — running about 15 minutes behind." \
        [--contact-name Alex] [--thread-id <gmail thread id>] [--flow note]
        [--dry-run]

--flow note     a one-off message; NOT logged to the CRM (the default)
--flow followup / reengage   relationship activity; logged to the CRM on send
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent / "shared"))
from data_paths import path as data_path  # noqa: E402
from runtime_secrets import require_secret  # noqa: E402

PENDING = data_path("pending_crm.json")
TG_CHAT_ID = "1000000001"
FLOWS = ("note", "followup", "reengage")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def load_queue() -> list:
    if not PENDING.exists():
        return []
    try:
        raw = json.loads(PENDING.read_text())
    except ValueError:
        return []
    if isinstance(raw, dict):
        return [raw]
    return raw if isinstance(raw, list) else []


def send_button(entry: dict) -> int | None:
    """The user's ✅/❌ for this draft. Returns the message id so the confirm can
    edit this very message in place rather than posting a second one."""
    contact = entry.get("contact_name") or entry.get("to_email", "?")
    text = f"✉️ Draft for {contact}:\n\n{entry.get('draft', '')[:300]}"
    if len(text) > 3990:
        text = text[:3990] + "…"
    payload = {"chat_id": TG_CHAT_ID, "text": text,
               "reply_markup": {"inline_keyboard": [[
                   {"text": "✅ Send", "callback_data": "confirm:crm"},
                   {"text": "❌ Skip", "callback_data": "skip:crm"}]]}}
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{require_secret('TELEGRAM_BOT_TOKEN')}/sendMessage",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read()).get("result", {}).get("message_id")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--to", required=True)
    ap.add_argument("--subject", required=True)
    ap.add_argument("--body", required=True)
    ap.add_argument("--contact-name")
    ap.add_argument("--thread-id", default="", help="reply inside an existing thread")
    ap.add_argument("--flow", default="note", choices=FLOWS)
    ap.add_argument("--dry-run", action="store_true", help="print the draft; queue nothing")
    args = ap.parse_args(argv)

    if not EMAIL_RE.match(args.to.strip()):
        print(json.dumps({"status": "bad_recipient", "to": args.to}))
        return 1
    if not args.body.strip():
        print(json.dumps({"status": "empty_body"}))
        return 1

    entry = {"to_email": args.to.strip(), "subject": args.subject.strip(),
             "draft": args.body, "contact_name": args.contact_name or args.to.strip(),
             "thread_id": args.thread_id, "flow": args.flow}

    if args.dry_run:
        print(json.dumps({"status": "dry-run", "entry": entry}, indent=2))
        return 0

    queue = load_queue()
    # A draft already waiting owns the buttons: confirm:crm always acts on
    # queue[0], so sending a second set now would let a tap send the wrong one.
    # It is queued and gets its own button when the one ahead is answered.
    if queue:
        queue.append(entry)
        PENDING.write_text(json.dumps(queue, indent=2))
        print(json.dumps({"status": "queued_behind", "ahead": len(queue) - 1,
                          "to": entry["to_email"]}))
        return 0

    msg_id = send_button(entry)
    if not msg_id:
        print(json.dumps({"status": "not_sent", "error": "no message id from Telegram"}))
        return 1
    entry["source_tg_msg_id"] = msg_id
    PENDING.write_text(json.dumps([entry], indent=2))
    print(json.dumps({"status": "proposed", "to": entry["to_email"],
                      "flow": entry["flow"], "msg_id": msg_id}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
