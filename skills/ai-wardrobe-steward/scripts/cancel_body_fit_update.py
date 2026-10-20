#!/usr/bin/env python3
"""Discard the pending body/fit update without touching body_fit_profile.yaml.

Usage: python3 cancel_body_fit_update.py
"""
import json
from pathlib import Path

PENDING_PATH = Path(__file__).resolve().parent.parent / "profile" / "pending_body_fit_update.json"


def main():
    if not PENDING_PATH.exists():
        print("No pending body/fit update.")
        return

    with open(PENDING_PATH) as f:
        pending = json.load(f)

    if not pending:
        print("No pending body/fit update.")
        return

    with open(PENDING_PATH, "w") as f:
        json.dump({}, f)

    print("Discarded pending body/fit update.")


if __name__ == "__main__":
    main()
