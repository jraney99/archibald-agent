#!/usr/bin/env python3
"""Discard the pending style profile update without touching style_profile.yaml.

Usage: python3 cancel_style_update.py
"""
import json
from pathlib import Path

PENDING_PATH = Path(__file__).resolve().parent.parent / "profile" / "pending_style_update.json"


def main():
    if not PENDING_PATH.exists():
        print("No pending style update.")
        return

    with open(PENDING_PATH) as f:
        pending = json.load(f)

    if not pending:
        print("No pending style update.")
        return

    with open(PENDING_PATH, "w") as f:
        json.dump({}, f)

    print("Discarded pending style update.")


if __name__ == "__main__":
    main()
