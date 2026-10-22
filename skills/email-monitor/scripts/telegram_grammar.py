#!/usr/bin/env python3
"""
Telegram grammar fixer — polls for incoming messages from the user, fixes
grammar/spelling/punctuation via Claude, and replies with the cleaned text.

When a message is a reply to an email notification (identified via tg_pending),
it also updates gmail_pending with the corrected draft so the main agent can
send it correctly via send_pending.py.

✅ messages are deliberately ignored here — the main openclaw agent handles sends.

Runs every 2 minutes via system crontab.
State: ~/.openclaw/email_monitor_state.json
"""
import os
import json, subprocess, sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, os.path.expanduser("~/.openclaw/workspace/Skills/shared"))
from usage_logger import log_usage

TG_TOKEN   = __import__("runpy").run_path(os.path.expanduser("~/.openclaw/workspace/Skills/shared/runtime_secrets.py"))["require_secret"]("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID = "1000000001"
API_KEY    = "REDACTED"
MODEL      = "claude-sonnet-4-6"
STATE_FILE = os.path.expanduser("~/.openclaw/email_monitor_state.json")

GRAMMAR_PROMPT = """\
You are a grammar, spelling, and punctuation corrector. The user sends you a voice-dictated
email draft. Fix all errors silently — wrong words, run-ons, missing punctuation, capitalization.
Preserve the user's exact meaning, tone, and every factual detail. Do not rephrase, shorten,
or add anything new. Return ONLY the corrected text with no explanation."""


def get_updates(offset: Optional[int]) -> list:
    import requests
    params: dict = {"timeout": 0, "allowed_updates": ["message"]}
    if offset is not None:
        params["offset"] = offset
    r = requests.get(
        f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates",
        params=params, timeout=15,
    )
    r.raise_for_status()
    return r.json().get("result", [])


def send_reply(text: str, reply_to: Optional[int] = None) -> Optional[int]:
    """Send a Telegram message, return its message_id."""
    import requests
    payload: dict = {
        "chat_id": TG_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_to:
        payload["reply_to_message_id"] = reply_to
    r = requests.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        json=payload, timeout=15,
    )
    if r.status_code == 200:
        return r.json().get("result", {}).get("message_id")
    return None


def fix_grammar(text: str) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=API_KEY)
    resp = client.messages.create(
        model=MODEL,
        max_tokens=1000,
        temperature=0,
        system=GRAMMAR_PROMPT,
        messages=[{"role": "user", "content": text}],
    )
    log_usage("telegram_grammar.py:fix_grammar", MODEL, resp.usage, response_id=getattr(resp, "id", None))
    return resp.content[0].text.strip()


def main() -> None:
    state_path = Path(STATE_FILE)
    state      = json.loads(state_path.read_text()) if state_path.exists() else {}
    last_upd   = state.get("tg_last_update_id")
    tg_pending = state.get("tg_pending", {})

    updates = get_updates(last_upd)
    if not updates:
        return

    new_last    = last_upd
    state_dirty = False

    for upd in updates:
        upd_id   = upd["update_id"]
        new_last = max(new_last or 0, upd_id)

        msg     = upd.get("message", {})
        chat_id = str(msg.get("chat", {}).get("id", ""))
        text    = (msg.get("text") or "").strip()
        msg_id  = msg.get("message_id")

        if chat_id != TG_CHAT_ID or not text:
            continue
        if text.startswith("/"):
            continue

        # ✅ — fire the pending send (use specific ID from reply context, else latest)
        if text == "✅":
            if pending_entry:
                arg = pending_entry.get("gmail_msg_id", "--latest")
            else:
                arg = "--latest"
            proc = subprocess.run(
                ["python3", "-W", "ignore",
                 os.path.expanduser("~/.openclaw/workspace/Skills/email-monitor/scripts/send_pending.py"),
                 arg],
                capture_output=True, text=True, timeout=60,
            )
            out = proc.stdout.strip()
            if out.startswith("SENT:"):
                _, detail = out.split(":", 1)
                send_reply(f"✅ Sent {detail.strip()} 🎩", reply_to=msg_id)
                print(f"Email sent: {out}", flush=True)
                state_dirty = True
            elif out.startswith("NO_PENDING:"):
                send_reply("No pending draft to send.", reply_to=msg_id)
            else:
                err = proc.stderr.strip()[:300] or out
                send_reply(f"⚠️ Send failed: {err}", reply_to=msg_id)
                print(f"Send failed: {err}", flush=True)
            continue

        print(f"Received: {text[:80]}", flush=True)

        # Check if this is a reply to a known notification/correction
        reply_to_id   = str(msg.get("reply_to_message", {}).get("message_id", ""))
        pending_entry = tg_pending.get(reply_to_id) if reply_to_id else None

        corrected     = fix_grammar(text)
        has_changes   = corrected != text

        if has_changes:
            body = f"<b>Corrected draft:</b>\n\n{corrected}"
        else:
            body = "✓ No corrections needed."

        if pending_entry:
            gmail_msg_id = pending_entry.get("gmail_msg_id", "")
            link = f"https://mail.google.com/mail/u/0/#inbox/{gmail_msg_id}"
            body += (f"\n\nReply <b>✅</b> to send · <a href=\"{link}\">Open in Gmail</a>"
                     f"\n<code>ref:{gmail_msg_id}</code>")

        bot_msg_id = send_reply(body, reply_to=msg_id)

        if pending_entry and bot_msg_id:
            # Update gmail_pending with the latest draft so main agent uses correct text
            draft_to_store = corrected if has_changes else pending_entry["draft"]
            updated_entry  = dict(pending_entry)
            updated_entry["draft"] = draft_to_store

            state.setdefault("gmail_pending", {})[pending_entry["gmail_msg_id"]] = updated_entry

            # Propagate tg_pending to bot's reply so chained corrections also have context
            tg_pending[str(bot_msg_id)] = updated_entry
            state_dirty = True

            print(f"{'Corrected' if has_changes else 'Unchanged'} — gmail_pending updated.", flush=True)
        elif has_changes:
            print(f"Corrected (standalone, no email context).", flush=True)

    if new_last is not None or state_dirty:
        if new_last is not None:
            state["tg_last_update_id"] = new_last + 1
        state["tg_pending"] = tg_pending
        state_path.write_text(json.dumps(state, indent=2))


if __name__ == "__main__":
    main()
