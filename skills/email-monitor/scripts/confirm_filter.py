#!/usr/bin/env python3
"""
confirm:filter — the user tapped "✅ Create filter" on a recurring-sender proposal.

Reads ~/.openclaw/pending_filter.json, creates a native Gmail filter
scoped to the exact From address (add label, skip inbox), back-files what's
already in the inbox from that sender, records it in the daemon state file, and
prints a one-line confirmation for the callback-dispatch plugin to relay.

Idempotent: a second tap after the filter exists just reports it's already set.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sender_filter as ff
sys.path.insert(0, str(Path("~/.openclaw/workspace/Skills/shared")))
from pending_proposal import load_message_id, edit_or_fallback  # noqa: E402

PENDING = Path("~/.openclaw/pending_filter.json")
STATE = Path("~/.openclaw/email_monitor_state.json")


def _load_state() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def _save_state(state: dict) -> None:
    STATE.write_text(json.dumps(state, indent=2))


def main() -> None:
    if not PENDING.exists():
        print("No pending filter proposal.")
        return

    data = json.loads(PENDING.read_text())
    msg_id = load_message_id(PENDING)
    address = data.get("address", "").lower()
    label = data.get("label", "Finance")
    name = data.get("institution", address)

    if not address:
        PENDING.unlink(missing_ok=True)
        print(edit_or_fallback(msg_id, "Pending filter proposal was malformed — discarded."))
        return

    state = _load_state()
    if address in ff._block(state).get("created", {}):
        PENDING.unlink(missing_ok=True)
        print(edit_or_fallback(msg_id, f"✅ Filter for {address} already exists — nothing to do."))
        return

    try:
        svc = ff.gmail_service()
        label_id = ff.resolve_label_id(svc, label)
        if not label_id:
            print(edit_or_fallback(msg_id, f"⚠️ Couldn't resolve the '{label}' label — filter not created. Tap again to retry.", drop_buttons=False))
            return
        filter_id = ff.create_filter(svc, address, label_id)
        moved = ff.backfile_inbox(svc, address, label_id)
    except Exception as e:
        print(edit_or_fallback(msg_id, f"⚠️ Filter creation failed: {e}. Tap again to retry.", drop_buttons=False))
        return

    ff.mark_created(address, filter_id, state)
    # A sender the daemon worked out for itself (sender_ledger): remember the
    # domain so the next address there is recognised without asking again.
    learned = ""
    if data.get("source") == "learned" and data.get("domain"):
        # Never let a bookkeeping failure look like a failed filter: the filter
        # is already created and back-filed by this point.
        try:
            if ff.remember_learned_sender(name, data["domain"], label,
                                          data.get("area", "other")):
                learned = f" Learned {data['domain']} → {label} for future senders."
        except Exception as e:
            learned = f" (couldn't record {data['domain']} for future senders: {e})"
    # refresh the coverage cache from the live filter list so the daemon has an
    # accurate picture (and doesn't re-propose an existing filter before TTL)
    try:
        ff.store_filter_cache(state, ff.list_filter_coverage(svc))
    except Exception:
        pass
    _save_state(state)
    PENDING.unlink(missing_ok=True)

    tail = f" Back-filed {moved} existing {'thread' if moved == 1 else 'threads'}." if moved else ""
    print(edit_or_fallback(msg_id, f"✅ 🏦 Filter created — mail from {address} ({name}) now files to "
          f"{label} and skips the inbox.{tail}{learned}"))


if __name__ == "__main__":
    main()
