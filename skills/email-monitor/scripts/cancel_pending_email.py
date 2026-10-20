#!/usr/bin/env python3
"""
Cancel the most recent pending email draft.
Called by Archibald when user taps ❌ (skip:email) on a draft button.
"""
import json, sys
from pathlib import Path

sys.path.insert(0, str(Path("~/.openclaw/workspace/Skills/shared")))
from pending_proposal import edit_or_fallback  # noqa: E402
from approval import lookup, require_cli, finish

STATE_FILE = Path("~/.openclaw/email_monitor_state.json")


def main():
    if not STATE_FILE.exists():
        print("No pending email drafts.")
        sys.exit(0)

    state = json.loads(STATE_FILE.read_text())
    gp    = state.get("gmail_pending", {})
    tp    = state.get("tg_pending", {})

    if not gp:
        print("No pending email drafts.")
        sys.exit(0)

    args = sys.argv[1:]
    if "--approval-id" not in args:
        raise ValueError("Use Cancel on the specific email proposal.")
    record = lookup(args[args.index("--approval-id") + 1])
    latest_key = record["target"] if record and record["kind"] == "email" else ""
    entry = gp.get(latest_key)
    if not entry:
        raise ValueError("This email proposal no longer exists.")
    token = require_cli("email", latest_key, entry)
    gp.pop(latest_key)
    msg_id     = entry.get("source_tg_msg_id")

    # Also remove matching tg_pending entries
    to_remove = [k for k, v in tp.items() if v.get("gmail_msg_id") == latest_key]
    for k in to_remove:
        del tp[k]

    STATE_FILE.write_text(json.dumps(state, indent=2))
    finish(token, "cancelled")
    to_email = entry.get("to_email", "unknown")
    print(edit_or_fallback(msg_id, f"❌ Cancelled draft to {to_email}. 🎩"))


if __name__ == "__main__":
    main()
