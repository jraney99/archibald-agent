#!/usr/bin/env python3
"""Discard the pending colour-analysis update without touching color_analysis.yaml.

Usage: python3 cancel_color_analysis_update.py
"""
import json
from pathlib import Path

PENDING_PATH = Path(__file__).resolve().parent.parent / "profile" / "pending_color_analysis_update.json"


def main():
    if not PENDING_PATH.exists():
        print("No pending colour-analysis update.")
        return

    with open(PENDING_PATH) as f:
        pending = json.load(f)

    if not pending:
        print("No pending colour-analysis update.")
        return

    with open(PENDING_PATH, "w") as f:
        json.dump({}, f)

    print("Discarded pending colour-analysis update.")


if __name__ == "__main__":
    main()
