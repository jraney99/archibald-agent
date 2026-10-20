"""Short-lived, single-use approvals bound to the exact pending action."""
import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path.home() / ".openclaw/state/approvals.sqlite"
IGNORED = {"source_tg_msg_id", "approval_id", "proposal_text", "created_at", "created_epoch"}


def fingerprint(payload):
    data = {k: v for k, v in payload.items() if k not in IGNORED}
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@contextmanager
def connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(DB_PATH), timeout=15)
    try:
        db.execute("CREATE TABLE IF NOT EXISTS approvals (id TEXT PRIMARY KEY, kind TEXT NOT NULL, target TEXT NOT NULL, digest TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL, status TEXT NOT NULL, message_id TEXT, result TEXT)")
        db.commit()
        yield db
    finally:
        db.close()


def issue(kind, target, payload, ttl=15 * 60):
    if kind not in ("email", "cal", "trade"):
        raise ValueError("Unsupported approval kind")
    token = uuid.uuid4().hex
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM approvals WHERE kind=? AND target=? AND status='executing'", (kind, target)).fetchone():
            raise ValueError("An earlier attempt has an uncertain result. Verify it before issuing another approval.")
        db.execute("UPDATE approvals SET status='superseded' WHERE kind=? AND target=? AND status='pending'", (kind, target))
        db.execute("INSERT INTO approvals VALUES (?,?,?,?,?,?,?,NULL,NULL)",
                   (token, kind, target, fingerprint(payload), time.time(), time.time() + ttl, "pending"))
        db.commit()
    return token


def attach_message(token, message_id):
    with connection() as db:
        db.execute("UPDATE approvals SET message_id=? WHERE id=? AND status='pending'", (str(message_id), token))
        db.commit()


def claim(token, kind, target, payload):
    """Claim before executing; an uncertain external result must never auto-retry."""
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT kind,target,digest,expires,status FROM approvals WHERE id=?", (token,)).fetchone()
        if not row or row[:3] != (kind, target, fingerprint(payload)):
            raise ValueError("This proposal changed or no longer exists. Request a fresh proposal.")
        if row[3] < time.time():
            raise ValueError("This approval expired. Request a fresh proposal.")
        if row[4] != "pending":
            raise ValueError("This approval was already used. Check its result before retrying.")
        if db.execute("SELECT 1 FROM approvals WHERE kind=? AND target=? AND status='executing'", (kind, target)).fetchone():
            raise ValueError("This action already has an in-progress or uncertain attempt.")
        db.execute("UPDATE approvals SET status='executing' WHERE id=?", (token,))
        db.commit()


def finish(token, result):
    with connection() as db:
        db.execute("UPDATE approvals SET status='completed', result=? WHERE id=? AND status='executing'", (result, token))
        db.commit()


def expire_due(kind="email", announce_within=24 * 3600):
    """Flip pending approvals of `kind` whose time has run out to 'expired'.
    Returns the Telegram message ids of the ones that ran out recently, so the
    caller can say so ON the message -- its buttons are already dead (claim()
    refuses an expired token) but it still looks live in the chat (2026-09-20).
    Older ones are flipped quietly: rewriting weeks-old chat history helps nobody.
    Security-neutral: nothing here can make an approval usable."""
    now = time.time()
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        rows = db.execute("SELECT id, message_id, expires FROM approvals WHERE kind=? AND status='pending' AND expires<?",
                          (kind, now)).fetchall()
        db.executemany("UPDATE approvals SET status='expired' WHERE id=? AND status='pending'", [(r[0],) for r in rows])
        db.commit()
    return [r[1] for r in rows if r[1] and now - r[2] <= announce_within]


def lookup(token):
    with connection() as db:
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT id,kind,target,status,message_id,expires FROM approvals WHERE id=?", (token,)).fetchone()
        return dict(row) if row else None


def for_message(message_id):
    with connection() as db:
        db.row_factory = sqlite3.Row
        rows = db.execute("SELECT id,kind,target,status FROM approvals WHERE message_id=? AND status='pending' AND expires>?", (str(message_id), time.time())).fetchall()
        return dict(rows[0]) if len(rows) == 1 else None


def require_cli(kind, target, payload):
    import sys
    args = sys.argv[1:]
    if "--approval-id" not in args:
        raise ValueError("Use the approval button on this specific proposal; an unscoped confirmation cannot execute it.")
    pos = args.index("--approval-id")
    if pos + 1 >= len(args):
        raise ValueError("Missing approval ID")
    token = args[pos + 1]
    claim(token, kind, target, payload)
    return token


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--message-id", required=True)
    args = parser.parse_args()
    print(json.dumps(for_message(args.message_id)))
