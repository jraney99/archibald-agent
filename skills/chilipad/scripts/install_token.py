#!/usr/bin/env python3
"""
Put the sleep.me API token into openclaw.json's env.vars block.

    python3 install_token.py

Prompts for the token without echoing it, so it never lands in shell history.
Pass --check to see whether a token is already installed (it prints only the
length and last 4 characters, never the token).

Why a script rather than hand-editing: openclaw.json is the live gateway config.
A truncated write breaks the gateway, so this writes to a temp file in the same
directory and renames it over the original — the rename is atomic, so the file is
either the old one or the new one and never a half-written one. The previous
contents are kept alongside as openclaw.json.bak.<timestamp> (already gitignored,
along with openclaw.json itself, so the token never lands in version control).

The token is read by sleepme_client.py, which checks the SLEEPME_API_TOKEN env var
first and falls back to this file. The file fallback is what makes this work
without restarting the gateway: cron command jobs do not inherit a shell export.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import time
from pathlib import Path

OPENCLAW_JSON = Path("~/.openclaw/openclaw.json")
KEY = "SLEEPME_API_TOKEN"


def redact(token: str) -> str:
    return f"{len(token)} chars, ending {token[-4:]}" if len(token) > 4 else "too short to be a token"


def main() -> None:
    ap = argparse.ArgumentParser(description="Install the sleep.me API token.")
    ap.add_argument("--check", action="store_true", help="Report whether a token is installed; change nothing.")
    args = ap.parse_args()

    try:
        raw = OPENCLAW_JSON.read_text()
        cfg = json.loads(raw)
    except FileNotFoundError:
        print(f"{OPENCLAW_JSON} does not exist.", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"{OPENCLAW_JSON} is not valid JSON ({e}) — not touching it.", file=sys.stderr)
        sys.exit(1)

    existing = ((cfg.get("env") or {}).get("vars") or {}).get(KEY)
    if args.check:
        print(f"{KEY}: {redact(existing) if existing else 'not set'}")
        if os.environ.get(KEY):
            print(f"(an environment variable is also set and takes precedence: {redact(os.environ[KEY])})")
        return

    if existing:
        print(f"{KEY} is already set ({redact(existing)}).")
        if input("Replace it? [y/N] ").strip().lower() not in ("y", "yes"):
            print("Left alone.")
            return

    token = getpass.getpass("sleep.me API token (not echoed): ").strip()
    if not token:
        print("Nothing entered — nothing changed.", file=sys.stderr)
        sys.exit(1)
    if len(token) < 16 or any(c.isspace() for c in token):
        print("That does not look like a token (too short, or contains whitespace). "
              "Nothing changed.", file=sys.stderr)
        sys.exit(1)

    cfg.setdefault("env", {}).setdefault("vars", {})[KEY] = token

    backup = OPENCLAW_JSON.with_suffix(f".json.bak.{time.strftime('%Y%m%d-%H%M%S')}")
    backup.write_text(raw)
    tmp = OPENCLAW_JSON.with_suffix(".json.installing")
    tmp.write_text(json.dumps(cfg, indent=2) + "\n")
    tmp.replace(OPENCLAW_JSON)   # atomic within the same filesystem

    # Read it back through the same path the client uses, so "installed" is a fact.
    check = ((json.loads(OPENCLAW_JSON.read_text()).get("env") or {}).get("vars") or {}).get(KEY)
    if check != token:
        print(f"Write-back check FAILED. The previous file is at {backup}.", file=sys.stderr)
        sys.exit(1)

    print(f"{KEY} installed ({redact(token)}).")
    print(f"previous openclaw.json saved as {backup.name}")
    print("\nNext: python3 discover_devices.py")


if __name__ == "__main__":
    main()
