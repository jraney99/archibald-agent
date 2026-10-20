#!/usr/bin/env python3
"""Confirm one or all pending closet items — moves them from
profile/pending_closet_additions.json into profile/closet.json and validates.

Usage:
  python3 confirm_closet_item.py --list            # show the queue, no changes
  python3 confirm_closet_item.py <id>               # confirm one item
  python3 confirm_closet_item.py --all              # confirm everything queued

Mirrors this workspace's existing pending/confirm pattern (see
email-monitor/scripts/confirm_diary_entry.py) — drafting is automatic,
this is the one deliberate checkpoint before anything becomes permanent.
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PENDING_PATH = ROOT / "profile" / "pending_closet_additions.json"
CLOSET_PATH = ROOT / "profile" / "closet.json"
VALIDATE_SCRIPT = Path(__file__).resolve().parent / "validate_closet.py"


def load(path):
    if not path.exists():
        return []
    with open(path) as f:
        return json.load(f)


def save(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def list_pending(pending):
    if not pending:
        print("Pending queue is empty.")
        return
    print(f"{len(pending)} item(s) pending confirmation:")
    for item in pending:
        flags = item.get("flagged_fields", [])
        flag_note = f" (uncertain: {', '.join(flags)})" if isinstance(flags, list) and flags else ""
        summary = item.get("review_summary") or f"{item.get('brand', '?')} {item.get('color', '?')} {item.get('category', '?')}"
        print(f"  - {item.get('id')}: {summary}{flag_note}")


def confirm(ids):
    pending = load(PENDING_PATH)
    closet = load(CLOSET_PATH)

    if ids == "__all__":
        to_confirm = pending
        remaining = []
    else:
        to_confirm = [item for item in pending if item.get("id") == ids]
        remaining = [item for item in pending if item.get("id") != ids]
        if not to_confirm:
            print(f"No pending item with id '{ids}'.")
            list_pending(pending)
            sys.exit(1)

    missing_id = [item for item in to_confirm if not item.get("id")]
    if missing_id:
        bad = ", ".join(item.get("review_summary", "?") for item in missing_id)
        sys.exit(f"Refusing to confirm — {len(missing_id)} pending item(s) missing 'id': {bad}. Fix the pending draft first.")

    # Refuse before writing, not after — a duplicate id among the items
    # being confirmed (two drafts accidentally sharing a slug) would
    # otherwise write both into closet.json and only get caught by the
    # post-write validator, leaving closet.json broken until manually fixed.
    existing_ids = {item.get("id") for item in closet}
    seen = set()
    colliding = set()
    for item in to_confirm:
        item_id = item.get("id")
        if item_id in existing_ids or item_id in seen:
            colliding.add(item_id)
        seen.add(item_id)
    if colliding:
        sys.exit(f"Refusing to confirm — id(s) already in use, would create a duplicate: {', '.join(sorted(colliding))}. Fix the pending draft first.")

    for item in to_confirm:
        item.pop("review_summary", None)
        item.pop("flagged_fields", None)
        closet.append(item)

    save(CLOSET_PATH, closet)
    save(PENDING_PATH, remaining)

    print(f"Confirmed {len(to_confirm)} item(s): {', '.join(i['id'] for i in to_confirm)}")

    result = subprocess.run([sys.executable, str(VALIDATE_SCRIPT)], capture_output=True, text=True)
    print(result.stdout.strip())
    if result.returncode != 0:
        print("Validation failed after write — review closet.json.", file=sys.stderr)
        sys.exit(1)


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)

    arg = sys.argv[1]
    if arg == "--list":
        list_pending(load(PENDING_PATH))
    elif arg == "--all":
        confirm("__all__")
    else:
        confirm(arg)


if __name__ == "__main__":
    main()
