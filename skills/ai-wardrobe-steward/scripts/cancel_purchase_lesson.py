#!/usr/bin/env python3
"""Discard one or all pending purchase lessons without touching purchase_lessons.json.

Usage:
  python3 cancel_purchase_lesson.py <id>
  python3 cancel_purchase_lesson.py --all
"""
import json
import sys
from pathlib import Path

PENDING_PATH = Path(__file__).resolve().parent.parent / "profile" / "pending_purchase_lessons.json"


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

    pending = load()
    arg = sys.argv[1]

    if not isinstance(pending, list) or not all(isinstance(e, dict) for e in pending):
        sys.exit("pending_purchase_lessons.json must be a list of mappings — file may be corrupted.")

    if arg == "--all":
        save([])
        print(f"Discarded {len(pending)} pending lesson(s).")
        return

    match = [e for e in pending if e.get("id") == arg]
    if not match:
        print(f"No pending lesson with id '{arg}'.")
        sys.exit(1)

    save([e for e in pending if e.get("id") != arg])
    print(f"Discarded pending lesson '{arg}'.")


if __name__ == "__main__":
    main()
