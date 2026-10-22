#!/usr/bin/env python3
"""
confirm:unsub — the user tapped "🗑 Unsubscribe & filter" on an unsub proposal.

Reads ~/.openclaw/pending_unsub.json and:
  1. best-effort hits the List-Unsubscribe link (RFC 8058 one-click POST, else GET)
  2. creates a native Gmail filter: from that address → Trash, skip inbox
  3. trashes what's already in the inbox from that sender
  4. records it in the daemon state file (`unsub_filter` block)

Idempotent: a second tap after the filter exists just reports it's already set.
Prints a one-line confirmation for the callback-dispatch plugin to relay.
"""
import os
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sender_filter as sf
sys.path.insert(0, str(Path(os.path.expanduser("~/.openclaw/workspace/Skills/shared")).expanduser()))
from pending_proposal import load_message_id, edit_or_fallback  # noqa: E402

PENDING = Path(os.path.expanduser("~/.openclaw/pending_unsub.json")).expanduser()
STATE = Path(os.path.expanduser("~/.openclaw/email_monitor_state.json")).expanduser()


def _load_state() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def _save_state(state: dict) -> None:
    STATE.write_text(json.dumps(state, indent=2))


def main() -> None:
    if not PENDING.exists():
        print("No pending unsubscribe proposal.")
        return

    data = json.loads(PENDING.read_text())
    msg_id = load_message_id(PENDING)
    address = (data.get("address") or "").lower()
    name = data.get("name", address)
    lu_header = data.get("list_unsubscribe", "")

    if not address:
        PENDING.unlink(missing_ok=True)
        print(edit_or_fallback(msg_id, "Pending unsubscribe proposal was malformed — discarded."))
        return

    state = _load_state()
    if address in sf._unsub_block(state).get("created", {}):
        PENDING.unlink(missing_ok=True)
        print(edit_or_fallback(msg_id, f"✅ Already unsubscribed from {address} — nothing to do."))
        return

    unsub_status = sf.try_unsubscribe_http(lu_header)

    try:
        svc = sf.gmail_service()
        filter_id = sf.create_junk_filter(svc, address)
        moved = sf.file_junk_from_sender(svc, address)
    except Exception as e:
        print(edit_or_fallback(msg_id, f"⚠️ Unsubscribe link {unsub_status}, but the filter failed: {e}. Tap again to retry.", drop_buttons=False))
        return

    sf.unsub_mark_created(address, filter_id, state)
    try:
        sf.store_filter_cache(state, sf.list_filter_coverage(svc))
    except Exception:
        pass
    _save_state(state)
    PENDING.unlink(missing_ok=True)

    tail = f" Moved {moved} existing." if moved else ""
    print(edit_or_fallback(msg_id, f"✅ 🗑 {name}: {unsub_status}. Future mail from {address} now skips the "
          f"inbox to Low Priority (nothing is deleted).{tail}"))


if __name__ == "__main__":
    main()
