#!/usr/bin/env python3
"""
skip:unsub — the user tapped "❌ Keep" on an unsubscribe proposal.

Records a permanent decline for that address so the daemon never offers to
unsubscribe from it again, and clears the pending slot.
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


def main() -> None:
    address = ""
    msg_id = None
    if PENDING.exists():
        msg_id = load_message_id(PENDING)
        try:
            address = (json.loads(PENDING.read_text()).get("address") or "").lower()
        except Exception:
            pass
        PENDING.unlink(missing_ok=True)

    if not address:
        print(edit_or_fallback(msg_id, "Kept. 🎩"))
        return

    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    sf.unsub_mark_declined(address, state)
    STATE.write_text(json.dumps(state, indent=2))
    print(edit_or_fallback(msg_id, f"✅ Keeping {address} — won't offer to unsubscribe again. 🎩"))


if __name__ == "__main__":
    main()
