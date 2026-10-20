#!/usr/bin/env python3
"""Cancel one or all pending closet items — removes them from
profile/pending_closet_additions.json without ever writing to closet.json.

Usage:
  python3 cancel_closet_item.py <id>     # drop one pending item
  python3 cancel_closet_item.py --all    # clear the whole queue
"""
import json
import sys
from pathlib import Path

PENDING_PATH = Path(__file__).resolve().parent.parent / "profile" / "pending_closet_additions.json"


def load():
    if not PENDING_PATH.exists():
        return []
    with open(PENDING_PATH) as f:
        return json.load(f)


def save(data):
    with open(PENDING_PATH, "w") as f:
        json.dump(data, f, indent=2)


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)

    arg = sys.argv[1]
    pending = load()

    if arg == "--all":
        save([])
        print(f"Cancelled all {len(pending)} pending item(s).")
        return

    remaining = [item for item in pending if item.get("id") != arg]
    if len(remaining) == len(pending):
        print(f"No pending item with id '{arg}'.")
        sys.exit(1)

    save(remaining)
    print(f"Cancelled pending item '{arg}'.")


if __name__ == "__main__":
    main()
