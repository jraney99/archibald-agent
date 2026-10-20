#!/usr/bin/env python3
"""Shared state for monitored sources and retryable notifications.

This module never contacts a network itself. Domain skills provide a sender
callable when they deliberately want to deliver a queued notification.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional


DEFAULT_DB = Path.home() / ".openclaw/workspace/state/monitoring.sqlite"
SOURCE_STATUSES = {"complete", "partial", "unavailable"}
NOTICE_STATUSES = {"pending", "attempting", "delivered", "failed", "expired", "ambiguous"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class KnownNotSent(Exception):
    """Use only when the transport proves no message was accepted."""


def valid_receipt(receipt):
    """Accept the shared CLI receipt or a Telegram message receipt, never bools."""
    def message_id(value):
        return isinstance(value, int) and not isinstance(value, bool) and value > 0
    if not isinstance(receipt, dict):
        return False
    if receipt.get("sent") is True:
        ids = receipt.get("message_ids")
        return (isinstance(ids, list) and bool(ids) and all(message_id(v) for v in ids)
                and isinstance(receipt.get("chunks"), int) and not isinstance(receipt.get("chunks"), bool)
                and receipt.get("chunks") == len(ids))
    if receipt.get("ok") is True and isinstance(receipt.get("result"), dict):
        return message_id(receipt["result"].get("message_id"))
    return message_id(receipt.get("message_id")) and receipt.get("ok") is not False


class MonitoringStore:
    def __init__(self, path: Path | str = DEFAULT_DB):
        self.path = Path(path)

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(str(self.path), timeout=20)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        db.execute(
            """CREATE TABLE IF NOT EXISTS source_health (
                   source TEXT PRIMARY KEY,
                   status TEXT NOT NULL,
                   last_attempt TEXT NOT NULL,
                   last_success TEXT,
                   cursor TEXT,
                   coverage_json TEXT NOT NULL DEFAULT '{}',
                   detail TEXT,
                   failure_streak INTEGER NOT NULL DEFAULT 0
               )"""
        )
        db.execute(
            """CREATE TABLE IF NOT EXISTS notification_outbox (
                   notice_key TEXT PRIMARY KEY,
                   domain TEXT NOT NULL,
                   text TEXT NOT NULL,
                   metadata_json TEXT NOT NULL DEFAULT '{}',
                   priority TEXT NOT NULL DEFAULT 'digest',
                   status TEXT NOT NULL DEFAULT 'pending',
                   created_at TEXT NOT NULL,
                   updated_at TEXT NOT NULL,
                   expires_at TEXT,
                   attempts INTEGER NOT NULL DEFAULT 0,
                   next_attempt_at TEXT,
                   delivered_at TEXT,
                   receipt TEXT,
                   last_error TEXT
               )"""
        )
        db.commit()
        try:
            yield db
        finally:
            db.close()

    def record_source(self, source: str, status: str, *, cursor: Optional[str] = None,
                      coverage: Optional[dict] = None, detail: Optional[str] = None,
                      attempted_at: Optional[str] = None) -> dict:
        if status not in SOURCE_STATUSES:
            raise ValueError(f"unknown source status: {status}")
        attempted_at = attempted_at or utc_now()
        with self.connect() as db:
            current = db.execute(
                "SELECT last_success,failure_streak,cursor,coverage_json FROM source_health WHERE source=?",
                (source,),
            ).fetchone()
            last_success = attempted_at if status in {"complete", "partial"} else (
                current["last_success"] if current else None)
            failures = 0 if status in {"complete", "partial"} else (
                int(current["failure_streak"]) + 1 if current else 1)
            saved_cursor = cursor if cursor is not None else (current["cursor"] if current else None)
            saved_coverage = coverage if coverage is not None else (
                json.loads(current["coverage_json"]) if current else {})
            db.execute(
                """INSERT INTO source_health
                       (source,status,last_attempt,last_success,cursor,coverage_json,detail,failure_streak)
                   VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(source) DO UPDATE SET
                       status=excluded.status,last_attempt=excluded.last_attempt,
                       last_success=excluded.last_success,cursor=excluded.cursor,
                       coverage_json=excluded.coverage_json,detail=excluded.detail,
                       failure_streak=excluded.failure_streak""",
                (source, status, attempted_at, last_success, saved_cursor,
                 json.dumps(saved_coverage, sort_keys=True), detail, failures),
            )
            db.commit()
        return self.source(source)

    def source(self, source: str) -> Optional[dict]:
        with self.connect() as db:
            row = db.execute("SELECT * FROM source_health WHERE source=?", (source,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["coverage"] = json.loads(result.pop("coverage_json"))
        return result

    def sources(self, prefix: Optional[str] = None) -> list[dict]:
        with self.connect() as db:
            if prefix is None:
                rows = db.execute("SELECT * FROM source_health ORDER BY source").fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM source_health WHERE source LIKE ? ORDER BY source",
                    (prefix + "%",),
                ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["coverage"] = json.loads(item.pop("coverage_json"))
            result.append(item)
        return result

    def enqueue(self, notice_key: str, domain: str, text: str, *, metadata: Optional[dict] = None,
                priority: str = "digest", expires_at: Optional[str] = None) -> dict:
        now = utc_now()
        with self.connect() as db:
            db.execute(
                """INSERT INTO notification_outbox
                       (notice_key,domain,text,metadata_json,priority,status,created_at,updated_at,expires_at)
                   VALUES (?,?,?,?,?,'pending',?,?,?)
                   ON CONFLICT(notice_key) DO NOTHING""",
                (notice_key, domain, text, json.dumps(metadata or {}, sort_keys=True),
                 priority, now, now, expires_at),
            )
            db.commit()
        notice = self.notice(notice_key)
        if (notice["domain"], notice["text"], notice["metadata"], notice["priority"], notice["expires_at"]) != (
                domain, text, metadata or {}, priority, expires_at):
            raise ValueError("Notification key reused for a different payload")
        return notice

    def notice(self, notice_key: str) -> Optional[dict]:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM notification_outbox WHERE notice_key=?", (notice_key,)
            ).fetchone()
        return self._notice_dict(row) if row else None

    @staticmethod
    def _notice_dict(row) -> dict:
        result = dict(row)
        result["metadata"] = json.loads(result.pop("metadata_json"))
        return result

    def pending(self, domain: Optional[str] = None, limit: int = 50,
                include_ambiguous: bool = False) -> list[dict]:
        params: list = []
        where = ("status IN ('pending','failed','ambiguous')" if include_ambiguous
                 else "status IN ('pending','failed')")
        where += " AND attempts < 3 AND (next_attempt_at IS NULL OR next_attempt_at<=?)"
        params.append(utc_now())
        if domain:
            where += " AND domain=?"
            params.append(domain)
        params.append(limit)
        with self.connect() as db:
            rows = db.execute(
                f"SELECT * FROM notification_outbox WHERE {where} "
                "ORDER BY CASE priority WHEN 'immediate' THEN 0 ELSE 1 END,created_at LIMIT ?",
                params,
            ).fetchall()
        return [self._notice_dict(row) for row in rows]

    def deliver(self, notice_key: str, sender: Callable[[str], object]) -> bool:
        # Commit the claim before sending; a second worker cannot send this intent.
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM notification_outbox WHERE notice_key=?",
                             (notice_key,)).fetchone()
            if row is None:
                raise ValueError("Unknown notification")
            if row["status"] == "delivered":
                return True
            if row["status"] not in ("pending", "failed"):
                return False
            now = utc_now()
            if row["expires_at"] and row["expires_at"] <= now:
                db.execute("UPDATE notification_outbox SET status='expired',updated_at=? WHERE notice_key=?",
                           (now, notice_key))
                db.commit()
                return False
            if row["attempts"] >= 3 or (row["next_attempt_at"] and row["next_attempt_at"] > now):
                return False
            # Legacy failed rows may have reached the provider. Never blindly replay them.
            if row["status"] == "failed" and row["last_error"] != "transport_proved_not_sent":
                db.execute("UPDATE notification_outbox SET status='ambiguous',updated_at=? WHERE notice_key=?",
                           (now, notice_key))
                db.commit()
                return False
            db.execute("UPDATE notification_outbox SET status='attempting',attempts=attempts+1,updated_at=? WHERE notice_key=?",
                       (now, notice_key))
            db.commit()
            attempt = row["attempts"] + 1
        try:
            receipt = sender(row["text"])
            if not valid_receipt(receipt):
                self._set_notice(notice_key, "ambiguous", last_error="missing_provider_receipt")
                return False
        except KnownNotSent:
            retry = (datetime.now(timezone.utc) + timedelta(seconds=min(300, 30 * 2 ** (attempt - 1)))).isoformat()
            with self.connect() as db:
                db.execute("UPDATE notification_outbox SET status='failed',updated_at=?,last_error=?,next_attempt_at=? WHERE notice_key=?",
                           (utc_now(), "transport_proved_not_sent", retry, notice_key))
                db.commit()
            return False
        except Exception as exc:
            # Exception text can contain a provider URL with a token: retain only its class.
            self._set_notice(notice_key, "ambiguous", last_error=type(exc).__name__)
            return False
        self._set_notice(notice_key, "delivered", delivered_at=utc_now(),
                         receipt=json.dumps(receipt, sort_keys=True, default=str))
        return True

    def recover_interrupted(self, before: str) -> int:
        """Reclassify abandoned claims for review, never schedule an uncertain resend."""
        with self.connect() as db:
            count = db.execute("UPDATE notification_outbox SET status='ambiguous',last_error='interrupted_worker' "
                               "WHERE status='attempting' AND updated_at<=?", (before,)).rowcount
            db.commit()
            return count

    def _set_notice(self, notice_key: str, status: str, *, last_error: Optional[str] = None,
                    delivered_at: Optional[str] = None, receipt: Optional[str] = None,
                    increment_attempts: bool = False) -> None:
        if status not in NOTICE_STATUSES:
            raise ValueError(f"unknown notification status: {status}")
        with self.connect() as db:
            db.execute(
                """UPDATE notification_outbox SET status=?,updated_at=?,last_error=?,
                          delivered_at=COALESCE(?,delivered_at),receipt=COALESCE(?,receipt),
                          attempts=attempts+?
                   WHERE notice_key=?""",
                (status, utc_now(), last_error, delivered_at, receipt,
                 1 if increment_attempts else 0, notice_key),
            )
            db.commit()
