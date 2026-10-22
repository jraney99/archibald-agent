#!/usr/bin/env python3
"""
Send a pending action's proposal message to Telegram and write back the message ID.

Usage: python3 send_proposal.py /path/to/pending.json

The pending file must contain a "proposal_text" field.
On success, "source_tg_msg_id" is written back into the same file.
"""
import os
import json, sys, urllib.request
from pathlib import Path

TG_TOKEN   = __import__("runpy").run_path(os.path.expanduser("~/.openclaw/workspace/Skills/shared/runtime_secrets.py"))["require_secret"]("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID = "1000000001"

if len(sys.argv) < 2:
    print("Usage: send_proposal.py /path/to/pending.json")
    sys.exit(1)

pending = Path(sys.argv[1])
if not pending.exists():
    print(f"File not found: {pending}")
    sys.exit(1)

data = json.loads(pending.read_text())
text = data.get("proposal_text", "")

if not text:
    print("No proposal_text in file")
    sys.exit(1)

req = urllib.request.Request(
    f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
    data=json.dumps({"chat_id": TG_CHAT_ID, "text": text}).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(req, timeout=10) as r:
    result = json.loads(r.read())

msg_id = result["result"]["message_id"]
data["source_tg_msg_id"] = msg_id
pending.write_text(json.dumps(data))
print(f"Proposal sent (msg_id: {msg_id})")
