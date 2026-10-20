#!/usr/bin/env python3
"""Run all email-policy regression tests, then the read-only live Gmail audit."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TESTS = sorted(path.name for path in HERE.glob("test_*.py"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-live", action="store_true")
    args = parser.parse_args()
    failed = False
    for name in TESTS:
        print(f"\n=== {name} ===", flush=True)
        result = subprocess.run([sys.executable, str(HERE / name)])
        failed |= result.returncode != 0
    if not args.skip_live:
        print("\n=== live Gmail filter audit ===", flush=True)
        result = subprocess.run([
            sys.executable, str(HERE / "email_filter_audit.py"), "--live",
        ])
        failed |= result.returncode != 0
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
