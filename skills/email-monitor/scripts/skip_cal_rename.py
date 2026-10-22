#!/usr/bin/env python3
"""
Skip a pending calendar title rename proposed by detect_bad_invites.py.
Called by Archibald when the user taps ❌ (skip:calrename). Marks the event as
seen so it isn't re-proposed on the next scan.
"""
import os
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(os.path.expanduser("~/.openclaw/workspace/Skills/shared")).expanduser()))
from pending_proposal import load_message_id, edit_or_fallback  # noqa: E402

PENDING   = Path(os.path.expanduser("~/.openclaw/pending_cal_rename.json")).expanduser()
SEEN_FILE = Path(os.path.expanduser("~/.openclaw/cal_rename_seen.json")).expanduser()


def main():
    if not PENDING.exists():
        print("No pending calendar rename.")
        return

    p = json.loads(PENDING.read_text())
    msg_id = load_message_id(PENDING)

    seen = set()
    if SEEN_FILE.exists():
        try:
            seen = set(json.loads(SEEN_FILE.read_text()).get("ids", []))
        except Exception:
            pass
    seen.add(p["event_id"])
    SEEN_FILE.write_text(json.dumps({"ids": sorted(seen)}))

    PENDING.unlink(missing_ok=True)
    print(edit_or_fallback(msg_id, "❌ Skipped. 🎩"))


if __name__ == "__main__":
    main()
