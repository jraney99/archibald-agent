#!/usr/bin/env python3
"""
skip:filter — the user tapped "❌ Never" on a recurring-sender proposal.

Records a permanent decline for that exact address in the daemon state file so
the daemon never proposes a filter for it again, and clears the pending slot.
"""
import os
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sender_filter as ff
sys.path.insert(0, str(Path(os.path.expanduser("~/.openclaw/workspace/Skills/shared")).expanduser()))
from pending_proposal import load_message_id, edit_or_fallback  # noqa: E402

PENDING = Path(os.path.expanduser("~/.openclaw/pending_filter.json")).expanduser()
STATE = Path(os.path.expanduser("~/.openclaw/email_monitor_state.json")).expanduser()


def main() -> None:
    address = ""
    msg_id = None
    if PENDING.exists():
        msg_id = load_message_id(PENDING)
        try:
            address = json.loads(PENDING.read_text()).get("address", "").lower()
        except Exception:
            pass
        PENDING.unlink(missing_ok=True)

    if not address:
        print(edit_or_fallback(msg_id, "Skipped. 🎩"))
        return

    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    ff.mark_declined(address, state)
    STATE.write_text(json.dumps(state, indent=2))
    print(edit_or_fallback(msg_id, f"❌ Won't ask about {address} again. 🎩"))


if __name__ == "__main__":
    main()
