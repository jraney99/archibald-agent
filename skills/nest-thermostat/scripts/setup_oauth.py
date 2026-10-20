#!/usr/bin/env python3
"""
One-time setup: get a Nest refresh token and write all four secrets into
openclaw.json's env.vars.

    python3 setup_oauth.py            # the whole flow, prompting as it goes
    python3 setup_oauth.py --check    # what is already installed (redacted)

What has to exist first (console work, not scriptable):
  1. console.nest.google.com/device-access — accept terms, pay the one-time US$5.
     It must be a CONSUMER Google account; Workspace accounts are refused.
  2. Google Cloud: a project with the **Smart Device Management API** enabled.
  3. Credentials → OAuth client ID → **Web application**, authorized redirect URI
     exactly `https://www.google.com`.
  4. Back in the Device Access Console → Create project → paste that Client ID →
     it hands back a Project ID (a UUID).

Then this script does the rest. It prints the Partner Connection Manager URL —
which is NOT the ordinary Google consent screen; it is where you choose which
devices to share — you open it, approve, and Google redirects to
`https://www.google.com/?code=...`. Paste that whole URL back in and it exchanges
the code, verifies the token works by listing your devices, and saves everything.

Secrets are prompted without echo, and openclaw.json is rewritten atomically with
a timestamped backup (it is gitignored, `.gitignore` line 108).

ITS OWN OAUTH CLIENT. Do not paste the client used by `gog` or the email daemon.
Those two shared one client and revoking it killed both at once on 2026-10-02; a
thermostat is not worth putting calendar and mail at risk.
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

from nest_client import (  # noqa: E402
    OPENCLAW_JSON, SECRETS, NestClient, NestError, auth_url, exchange_code, _secret,
)


def redact(value: str) -> str:
    return f"{len(value)} chars, ending {value[-4:]}" if len(value) > 4 else "too short"


def install(values: dict) -> Path:
    raw = OPENCLAW_JSON.read_text()
    cfg = json.loads(raw)
    cfg.setdefault("env", {}).setdefault("vars", {}).update(values)
    backup = OPENCLAW_JSON.with_suffix(f".json.bak.{time.strftime('%Y%m%d-%H%M%S')}")
    backup.write_text(raw)
    tmp = OPENCLAW_JSON.with_suffix(".json.installing")
    tmp.write_text(json.dumps(cfg, indent=2) + "\n")
    tmp.replace(OPENCLAW_JSON)          # atomic within the same filesystem
    saved = (json.loads(OPENCLAW_JSON.read_text()).get("env") or {}).get("vars") or {}
    for key, want in values.items():
        if saved.get(key) != want:
            raise NestError(f"write-back check failed for {key}. Previous file: {backup}")
    return backup


def main() -> None:
    ap = argparse.ArgumentParser(description="Get a Nest refresh token and install the secrets.")
    ap.add_argument("--check", action="store_true", help="Report what is installed; change nothing.")
    args = ap.parse_args()

    if args.check:
        for name in SECRETS:
            val = _secret(name)
            print(f"{name}: {redact(val) if val else 'not set'}")
        return

    print("Nest setup. The console steps in this script's docstring must be done first.\n")

    project_id = input("Device Access Project ID (UUID): ").strip()
    client_id = input("OAuth Client ID: ").strip()
    if not project_id or not client_id:
        print("Both are required — nothing changed.", file=sys.stderr)
        sys.exit(1)
    client_secret = getpass.getpass("OAuth Client Secret (not echoed): ").strip()
    if not client_secret:
        print("No client secret entered — nothing changed.", file=sys.stderr)
        sys.exit(1)

    print("\n1. Open this URL, sign in, and allow access to the thermostat:\n")
    print(auth_url(project_id, client_id))
    print("\n2. Google will redirect to https://www.google.com/?code=...")
    print("   It will look like a blank or ordinary Google page — that is expected.")
    print("   Copy the whole URL out of the address bar.\n")

    pasted = input("Paste the redirect URL (or just the code): ").strip()
    code = pasted
    if pasted.startswith("http"):
        found = parse_qs(urlparse(pasted).query).get("code")
        if not found:
            print("That URL has no ?code= in it. If it says error=access_denied, the "
                  "consent was declined; start again from the URL above.", file=sys.stderr)
            sys.exit(1)
        code = found[0]

    try:
        tokens = exchange_code(code, client_id, client_secret)
    except NestError as e:
        print(f"\nerror: {e}", file=sys.stderr)
        sys.exit(1)

    values = {
        "NEST_PROJECT_ID": project_id,
        "NEST_CLIENT_ID": client_id,
        "NEST_CLIENT_SECRET": client_secret,
        "NEST_REFRESH_TOKEN": tokens["refresh_token"],
    }
    try:
        backup = install(values)
    except (NestError, OSError, ValueError) as e:
        print(f"\nerror writing openclaw.json: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"\nInstalled 4 secrets. Refresh token: {redact(values['NEST_REFRESH_TOKEN'])}")
    print(f"Previous openclaw.json saved as {backup.name}")

    # Prove it works now rather than letting a cron discover it at 10pm.
    try:
        devices = NestClient().list_devices()
    except NestError as e:
        print(f"\n⚠️  Secrets saved, but the first call failed: {e}", file=sys.stderr)
        sys.exit(1)
    thermostats = [d for d in devices
                   if str(d.get("type", "")).endswith("THERMOSTAT")]
    print(f"\nVerified: {len(devices)} device(s) on the project, "
          f"{len(thermostats)} thermostat(s).")
    if not thermostats:
        print("No thermostat was shared. Re-open the URL above and make sure the "
              "thermostat is ticked.", file=sys.stderr)
        sys.exit(1)
    print("\nNext: python3 discover_devices.py")


if __name__ == "__main__":
    main()
