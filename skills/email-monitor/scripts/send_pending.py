#!/usr/bin/env python3
"""
Send a pending email reply by Gmail message ID.

Usage:
    python3 send_pending.py --approval-id <current-id> [--from-callback]

Reads gmail_pending from ~/.openclaw/email_monitor_state.json,
sends the reply, then removes the entry from state.

Exit codes:
    0  — sent successfully (prints: SENT: <sent_id> → <to_email>)
    1  — no pending entry found (prints: NO_PENDING: <gmail_msg_id>)
    2  — send failed (prints error)

`--from-callback` is set ONLY by the confirm:email dispatch-table entry (a
real button tap) — NOT by RULES.md's bare-✅-in-free-text rule, which still
needs the plain "SENT: ..."/"NO_PENDING: ..." stdout format to compose its
own reply, exactly as before. With the flag, and only when this entry has a
source_tg_msg_id (the button message), the original message is edited in
place and NO_REPLY is printed instead - see pending_proposal.py.
"""
import os
import base64, json, sys
from email.mime.text import MIMEText
from pathlib import Path

sys.path.insert(0, str(Path(os.path.expanduser("~/.openclaw/workspace/Skills/shared")).expanduser()))
from pending_proposal import edit_or_fallback  # noqa: E402
from approval import require_cli, lookup, finish, fingerprint

STATE_FILE = os.path.expanduser("~/.openclaw/email_monitor_state.json")
TOKEN_FILE = os.path.expanduser("~/.openclaw/gmail_monitor_token.json")
MY_EMAIL   = "owner@example.com"
SCOPES     = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.settings.basic",
    "https://www.googleapis.com/auth/calendar.readonly",
]


def get_credentials():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    path = Path(TOKEN_FILE)
    creds = Credentials.from_authorized_user_file(str(path), SCOPES)
    if not creds.valid and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        path.write_text(creds.to_json())
    return creds


def get_service():
    from googleapiclient.discovery import build
    return build("gmail", "v1", credentials=get_credentials(), cache_discovery=False)


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--from-callback"]
    from_callback = "--from-callback" in sys.argv[1:]
    if not args:
        print("Usage: send_pending.py --approval-id <current-id> [--from-callback]", file=sys.stderr)
        sys.exit(2)

    state_path = Path(STATE_FILE)
    state      = json.loads(state_path.read_text())
    pending    = state.get("gmail_pending", {})

    if "--approval-id" not in args:
        print("APPROVAL_REQUIRED: use the button on the specific email proposal.")
        sys.exit(1)
    pos = args.index("--approval-id")
    record = lookup(args[pos + 1]) if pos + 1 < len(args) else None
    gmail_msg_id = record["target"] if record and record["kind"] == "email" else ""

    entry = pending.get(gmail_msg_id)
    if not entry:
        print(f"NO_PENDING: {gmail_msg_id}")
        sys.exit(1)

    approval_id = require_cli("email", gmail_msg_id, entry)

    to_email  = entry["to_email"]
    draft     = entry["draft"]
    subject   = entry["subject"]
    thread_id = entry["thread_id"]
    rfc_id    = entry.get("rfc_msg_id", "")

    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"

    msg            = MIMEText(draft, "plain")
    msg["To"]      = to_email
    msg["From"]    = MY_EMAIL
    msg["Subject"] = subject
    if rfc_id:
        msg["In-Reply-To"] = rfc_id
        msg["References"]  = rfc_id

    raw    = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    result = get_service().users().messages().send(
        userId="me",
        body={"raw": raw, "threadId": thread_id},
    ).execute()
    sent_id = result.get("id")
    if not sent_id:
        raise RuntimeError("Gmail did not return a message ID; delivery is unconfirmed. Do not retry automatically.")
    finish(approval_id, "sent:" + sent_id)

    # Preserve unrelated updates made by the daemon while Gmail was responding.
    state = json.loads(state_path.read_text())

    # Remove from gmail_pending and any tg_pending entries for this gmail msg
    current = state.get("gmail_pending", {}).get(gmail_msg_id)
    if current and fingerprint(current) == fingerprint(entry):
        state["gmail_pending"].pop(gmail_msg_id, None)
    tg_pending = state.get("tg_pending", {})
    stale = [k for k, v in tg_pending.items() if v.get("gmail_msg_id") == gmail_msg_id and fingerprint(v) == fingerprint(entry)]
    for k in stale:
        del tg_pending[k]

    state_path.write_text(json.dumps(state, indent=2))

    if from_callback:
        msg_id = entry.get("source_tg_msg_id")
        print(edit_or_fallback(msg_id, f"✅ Sent to {to_email}. Gmail ID: {sent_id} 🎩", keep=True))
    else:
        # RULES.md's bare-✅ free-text rule parses this exact format - unchanged.
        print(f"SENT: {sent_id} → {to_email}")


if __name__ == "__main__":
    main()
