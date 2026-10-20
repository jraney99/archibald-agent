#!/usr/bin/env python3
"""Discard the pending style identity update without touching style_identity.yaml.

Usage: python3 cancel_style_identity_update.py
"""
import json
from pathlib import Path

PENDING_PATH = Path(__file__).resolve().parent.parent / "profile" / "pending_style_identity_update.json"


def main():
    if not PENDING_PATH.exists():
        print("No pending style identity update.")
        return

    with open(PENDING_PATH) as f:
        pending = json.load(f)

    if not pending:
        print("No pending style identity update.")
        return

    with open(PENDING_PATH, "w") as f:
        json.dump({}, f)

    print("Discarded pending style identity update.")


if __name__ == "__main__":
    main()
