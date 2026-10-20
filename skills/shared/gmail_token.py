#!/usr/bin/env python3
"""The shared Gmail OAuth token, loaded and refreshed without ever loosening its mode.

WHY (2026-10-02 audit): gmail_monitor_token.json sat at 0644 — readable by every
process on the Mac — and carried gmail.modify, gmail.settings.basic and calendar
scopes. Six finance watchers refreshed it with Path.write_text(), which keeps an
existing file's mode but would recreate a deleted one at the umask default (0644).
Every writer here goes through one routine: the file is created or replaced with
mode 0600, atomically, so a refresh can never leave it world-readable or half-written.

Consumers of the same file that still write it themselves (email-monitor daemon,
reauth.py, crm.py) only ever truncate-and-rewrite the existing inode, which keeps
whatever mode the file already has; they are not changed here.

    from gmail_token import gmail_service
    svc = gmail_service(TOKEN_FILE, scopes)
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

PRIVATE = 0o600


def ensure_private(token_file) -> bool:
    """Tighten an existing token file to 0600. Returns True when the mode changed."""
    path = Path(token_file)
    if not path.exists():
        return False
    mode = path.stat().st_mode & 0o777
    if mode != PRIVATE:
        os.chmod(path, PRIVATE)
        return True
    return False


def write_private(token_file, text: str) -> None:
    """Replace the token file atomically with mode 0600 from the first byte."""
    path = Path(token_file)
    fd, temp = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        os.fchmod(fd, PRIVATE)
        with os.fdopen(fd, "w") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    os.chmod(path, PRIVATE)


def recorded_scopes(token_file) -> set:
    """The scopes the stored access token was last issued with."""
    try:
        return set(json.loads(Path(token_file).read_text()).get("scopes") or [])
    except (OSError, ValueError, AttributeError):
        return set()


def load_credentials(token_file, scopes):
    """Credentials from the token file, refreshed when stale OR when the stored token
    does not already carry every scope the caller asked for, and written back privately.

    WHY the scope check (2026-10-05): this file is shared by the email daemon, the six
    finance watchers and the Gmail filter tooling, and each asks for only the scopes it
    needs. A caller that wanted just gmail.modify refreshed with that one scope and wrote
    the result back, so the recorded list SHRANK and the next access token was issued
    without gmail.settings.basic. An access token stays valid for an hour, so the old
    "refresh only when expired" rule then handed that narrowed token to the filter
    tooling, which failed with 403 Insufficient Permission while the underlying grant
    still had the scope. Diagnosing it costs an hour, because the file says one thing,
    Google says another, and nothing logs the difference.

    Two halves, both needed: asking for the union of recorded and requested scopes means
    no caller can narrow the file for the next one, and refreshing on a scope miss repairs
    a file that was narrowed before this fix existed. What gets recorded afterwards is
    what Google reports as granted, so a scope the grant genuinely lacks is never written
    down as present — that would turn a loud 403 into a silent never-retry.
    """
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    ensure_private(token_file)
    wanted = set(scopes)
    recorded = recorded_scopes(token_file)
    creds = Credentials.from_authorized_user_file(str(token_file), sorted(recorded | wanted))
    if creds.refresh_token and (not creds.valid or not wanted <= recorded):
        creds.refresh(Request())
        granted = set(getattr(creds, "granted_scopes", None) or creds.scopes or [])
        payload = json.loads(creds.to_json())
        if granted:
            payload["scopes"] = sorted(granted)
        write_private(token_file, json.dumps(payload))
    return creds


def gmail_service(token_file, scopes):
    from googleapiclient.discovery import build
    return build("gmail", "v1", credentials=load_credentials(token_file, scopes), cache_discovery=False)
