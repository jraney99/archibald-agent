#!/usr/bin/env python3
"""
Send the next pending CRM draft and log the interaction.
Called by Archibald when user taps ✅ (confirm:crm).

pending_crm.json holds a QUEUE (list) of drafts. This script:
  1. Pops the first entry off the queue
  2. Sends it via gog
  3. Logs to CRM
  4. If more entries remain, sends the next ✅/❌ button automatically
"""
import json, subprocess, sys, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))
from data_paths import OPENCLAW, path
from runtime_secrets import require_secret
from pending_proposal import load_message_id, edit_or_fallback  # noqa: E402

PENDING    = path("pending_crm.json")
GOG        = "/opt/homebrew/bin/gog"
EMAIL      = "owner@example.com"
CRM_SCRIPT = str(Path(__file__).resolve().parents[2] / "crm/scripts/crm.py")
TG_TOKEN   = require_secret("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID = "1000000001"


def send_next_button(entry: dict) -> None:
    """Sends the next queued entry's button and stashes its message_id onto
    that same entry (already `queue[0]` in the caller, about to be written
    to PENDING) so the confirm this new button triggers can edit it in
    place too."""
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
    except Exception as e:
        print(f"Warning: next button send failed: {e}", file=sys.stderr)


def main():
    if not PENDING.exists():
        print("No pending CRM draft.")
        sys.exit(1)

    raw = json.loads(PENDING.read_text())

    # Support both legacy single-object format and new queue format
    if isinstance(raw, dict):
        queue = [raw]
    elif isinstance(raw, list):
        queue = raw
    else:
        print("Malformed pending_crm.json.")
        PENDING.unlink(missing_ok=True)
        sys.exit(1)

    if not queue:
        print("No pending CRM drafts.")
        PENDING.unlink(missing_ok=True)
        sys.exit(0)

    # Pop first entry
    entry     = queue.pop(0)
    msg_id    = entry.get("source_tg_msg_id")
    thread_id = entry.get("thread_id", "")
    to_email  = entry.get("to_email", "")
    draft     = entry.get("draft", "")
    contact   = entry.get("contact_name", to_email)
    flow      = entry.get("flow", "followup")
    subject   = entry.get("subject", "")
    re_subj   = subject if subject.lower().startswith("re:") else f"Re: {subject}"

    lines: list = []

    if not draft or not to_email:
        lines.append("Entry missing recipient or draft — skipping.")
    else:
        # Send via gog — use explicit --to (not --reply-all) to avoid CC'ing everyone.
        # --thread-id only when there IS a thread: a first email to someone has no
        # thread to join, and requiring one used to make this queue reply-only
        # (2026-10-13 — a running-late note to a venue had nowhere to go).
        subj = re_subj if thread_id else (subject or "(no subject)")
        cmd = [GOG, "gmail", "send", "--account", EMAIL,
               *(["--thread-id", thread_id] if thread_id else []),
               "--to", to_email,
               "--subject", subj,
               "--body", draft]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            # Put entry back (buttons stay live for a retry) and stay quiet -
            # don't clear/edit the button on a failure the tap should be
            # able to retry.
            queue.insert(0, entry)
            PENDING.write_text(json.dumps(queue, indent=2))
            print(f"Send failed: {result.stderr.strip()[:200]}")
            sys.exit(2)

        # flow="note" is a one-off message — "running 15 late", a quick answer —
        # not relationship activity. Logging those to the CRM would fill the user's
        # relationship history with venue logistics (2026-10-13).
        if flow == "note":
            lines.append(f"✅ Sent to {contact}.")
        else:
            action = "Re-engagement outreach sent" if flow == "reengage" else "Sent follow-up email"
            subprocess.run(
                ["python3", "-W", "ignore", CRM_SCRIPT,
                 "log", to_email, action, "--name", contact],
                capture_output=True, timeout=15,
            )
            lines.append(f"✅ Sent to {contact}. Logged to CRM.")

    # Save remaining queue (or delete if empty). The next button is a
    # genuinely new message (a different draft) - not a redundant repeat -
    # so it's sent as its own message regardless of the edit-in-place above.
    if queue:
        send_next_button(queue[0])  # mutates queue[0] in place with its own source_tg_msg_id
        PENDING.write_text(json.dumps(queue, indent=2))
        lines.append(f"{len(queue)} more draft(s) queued — next button sent.")
    else:
        PENDING.unlink(missing_ok=True)

    print(edit_or_fallback(msg_id, "\n".join(lines), keep=True))  # an email went out in the user's name


if __name__ == "__main__":
    main()
