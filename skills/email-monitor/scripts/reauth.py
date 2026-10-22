#!/usr/bin/env python3
"""OAuth re-auth: refreshes the shared Gmail/Calendar token without touching daemon state.

~/.openclaw holds two OAuth client files of the same Google Cloud project; the old version
took whichever glob() returned first. This one uses the client the current token belongs to
(same client_id), or the file named with --client-file (for example after creating a new OAuth
client). The token is written 0600 before it has content. 2026-10-02.
"""
import os
import argparse
import json
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

HOME = Path(os.path.expanduser("~/.openclaw")).expanduser()
sys.path.insert(0, str(HOME / "workspace/Skills/shared"))
from gmail_token import write_private  # noqa: E402

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.settings.basic",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]
STATE_FILE = HOME / "email_monitor_state.json"
TOKEN_PATH = HOME / "gmail_monitor_token.json"


def client_id_of(client_file):
    data = json.loads(Path(client_file).read_text())
    return (data.get("installed") or data.get("web") or {}).get("client_id")


def pick_client_file(explicit=None):
    if explicit:
        return Path(explicit)
    files = sorted(HOME.glob("client_secret_*.json"))
    if not files:
        raise FileNotFoundError("No client_secret_*.json found in ~/.openclaw")
    if len(files) == 1:
        return files[0]
    current = None
    if TOKEN_PATH.exists():
        try:
            current = json.loads(TOKEN_PATH.read_text()).get("client_id")
        except ValueError:
            pass
    matches = [f for f in files if client_id_of(f) == current]
    if len(matches) == 1:
        return matches[0]
    names = ", ".join(f.name for f in files)
    raise SystemExit(f"{len(files)} client files and {len(matches)} match the current token's client_id; "
                     f"pass --client-file <path>. Found: {names}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--client-file", help="OAuth client JSON to use (default: the one the current token belongs to)")
    args = ap.parse_args()
    client_file = pick_client_file(args.client_file)
    print(f"Using OAuth client {client_file.name}")

    # Preserve daemon state across reauth — processed_ids must survive a token refresh
    # so the startup catch-up scan doesn't re-propose already-handled emails.
    state_backup = STATE_FILE.read_text() if STATE_FILE.exists() else None

    flow = InstalledAppFlow.from_client_secrets_file(str(client_file), SCOPES)
    creds = flow.run_local_server(port=0)

    write_private(TOKEN_PATH, creds.to_json())  # 0600 before content; the token holds modify-capable scopes

    if state_backup is not None and not STATE_FILE.exists():
        STATE_FILE.write_text(state_backup)
        print("State file restored.")

    missing = sorted(set(SCOPES) - set(creds.scopes or []))
    print(f"Token written to {TOKEN_PATH}")
    print(f"Scopes: {creds.scopes}")
    if missing:
        print(f"WARNING: these scopes were not granted, re-run and tick every box: {missing}")
        sys.exit(2)


if __name__ == "__main__":
    main()
