#!/usr/bin/env python3
"""
Confirm a pending calendar title rename proposed by detect_bad_invites.py.
Called by Archibald when the user taps ✅ (confirm:calrename).
"""
import os
import json
import subprocess
import sys
from pathlib import Path

PENDING   = Path(os.path.expanduser("~/.openclaw/pending_cal_rename.json")).expanduser()
SEEN_FILE = Path(os.path.expanduser("~/.openclaw/cal_rename_seen.json")).expanduser()
GOG       = "/opt/homebrew/bin/gog"
ACCOUNT   = "owner@example.com"

sys.path.insert(0, str(Path(os.path.expanduser("~/.openclaw/workspace/Skills/shared")).expanduser()))
from pending_proposal import load_message_id, edit_or_fallback  # noqa: E402


def mark_seen(event_id: str) -> None:
    seen = set()
    if SEEN_FILE.exists():
        try:
            seen = set(json.loads(SEEN_FILE.read_text()).get("ids", []))
        except Exception:
            pass
    seen.add(event_id)
    SEEN_FILE.write_text(json.dumps({"ids": sorted(seen)}))


def main():
    if not PENDING.exists():
        print("No pending calendar rename.")
        sys.exit(1)

    p = json.loads(PENDING.read_text())
    msg_id = load_message_id(PENDING)
    result = subprocess.run(
        [GOG, "cal", "update", p["calendar_id"], p["event_id"],
         "--account", ACCOUNT, "--summary", p["new_title"],
         "--reminder", "popup:15m"],
        capture_output=True, text=True, timeout=30,
    )

    mark_seen(p["event_id"])
    PENDING.unlink(missing_ok=True)

    if result.returncode != 0:
        print(f"Rename failed: {result.stderr.strip()}")
        sys.exit(1)

    print(edit_or_fallback(msg_id, f"✅ Renamed to \"{p['new_title']}\". 🎩"))


if __name__ == "__main__":
    main()
