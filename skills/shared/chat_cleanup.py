#!/usr/bin/env python3
"""
Keep the user's Telegram chat clean WITHOUT losing anything: every message is
copied to a local archive file before it is removed from the chat.

WHY (2026-09-20): every answered prompt left a "✅ Logged…" line in the chat
forever (~76 buttons across 15 skills, nothing ever deleted), old briefings
piled up, and no record existed of what the system had posted. The user: "my
system would keep all of it on file, just not cluttering my telegram chat."

Three rules, approved by the user 2026-09-20:
  1. answerable  A prompt he answered. The ✅ line stays for the rest of the
                 day (so he can see the tap worked) and is removed at the
                 nightly sweep. Unanswered prompts are never touched.
  2. stream      A scheduled message that supersedes itself (morning briefing,
                 evening briefing, …). When the new one is sent, the previous
                 one is removed — the chat always shows exactly the latest.
  3. notice      A one-off message with nothing to answer. Removed at the
                 first nightly sweep after it is NOTICE_MIN_AGE_H old, so it
                 can't vanish before he has seen it.
  4. ttl_hours   Any message (including a stream's) sent with `ttl_hours=N`
                 goes N hours after it was SENT, answered or not. Added
                 2026-10-08 for the 6pm FYI email digest, which the user reads
                 once and then has to look at until tomorrow: "Can we do the
                 same thing for FYI emails? Except those delete after 2 hours
                 after being sent to me?" Needs the sweep to run often (see
                 the chat-cleanup-frequent cron), not just nightly.
`keep=True` exempts a message from removal (e.g. an email "✅ Sent"
confirmation — a record of something sent in his name); it is still archived.

Archive first, delete second, always. If the archive write fails the message
stays in the chat. A delete Telegram refuses (already gone, or older than its
48-hour limit for bots) is recorded as such and dropped from tracking.

Files (both git-ignored — chat text includes health and finance details and
must not be pushed by the auto-backup):
  ~/.openclaw/telegram_archive/YYYY-MM.jsonl   one JSON line per removed message
  ~/.openclaw/chat_cleanup_state.json          messages currently tracked

Every public function is safe to call from a live handler: failures are
swallowed and reported as False/None, never raised — cleaning the chat must
never break logging a meal.

CLI:
  chat_cleanup.py sweep [--dry-run]    nightly job (every rule)
  chat_cleanup.py sweep --ttl-only     the frequent job: rule 4 only
  chat_cleanup.py show                 what is tracked right now
  chat_cleanup.py find "<text>" [--month YYYY-MM]   search the archive
"""
from __future__ import annotations

import argparse
import fcntl
import json
import sys
import time
import urllib.request
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

OPENCLAW = Path("~/.openclaw")
STATE_PATH = OPENCLAW / "chat_cleanup_state.json"
ARCHIVE_DIR = OPENCLAW / "telegram_archive"
TZ = ZoneInfo("America/Chicago")
TG_CHAT_ID = "1000000001"

_REAL_STATE_PATH = STATE_PATH
_REAL_ARCHIVE_DIR = ARCHIVE_DIR

NOTICE_MIN_AGE_H = 20      # a notice is visible for 20–44h
FYI_DIGEST_TTL_H = 2       # his number for the FYI digest; see rule 4
TELEGRAM_DELETE_LIMIT_H = 47   # bots cannot delete their own messages after 48h


def _token() -> str:
    return __import__("runpy").run_path(str(Path(__file__).parent / "runtime_secrets.py"))["require_secret"]("TELEGRAM_BOT_TOKEN")


class _TestRunGuard(RuntimeError):
    pass


@contextmanager
def _locked_state():
    # Any skill's test suite can reach a handler that calls mark_answered()
    # with a made-up message id (2026-09-20: nutrition's tests queued ids 42
    # and 1 for deletion from the REAL chat). A live handler never imports
    # unittest, so: under unittest, the real tracking file is off limits
    # unless the test pointed STATE_PATH somewhere else.
    if "unittest" in sys.modules and STATE_PATH == _REAL_STATE_PATH:
        raise _TestRunGuard("chat_cleanup: refusing to touch the real state file from a test run")
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(str(STATE_PATH) + ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            try:
                state = json.loads(STATE_PATH.read_text())
            except Exception:
                state = {}
            state.setdefault("messages", {})
            state.setdefault("streams", {})
            yield state
            tmp = STATE_PATH.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(state, indent=1))
            tmp.replace(STATE_PATH)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _archive(entry: dict, message_id, reason: str, deleted: bool, now: float) -> bool:
    try:
        if "unittest" in sys.modules and ARCHIVE_DIR == _REAL_ARCHIVE_DIR:
            return False  # same guard as _locked_state: a test run never writes the real archive
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        when = datetime.fromtimestamp(now, TZ)
        line = {"message_id": int(message_id), "removed_at": when.isoformat(timespec="seconds"),
                "reason": reason, "deleted_from_chat": deleted,
                "sent_at": _iso(entry.get("sent_at")), "answered_at": _iso(entry.get("answered_at")),
                "kind": entry.get("kind"), "stream": entry.get("stream"), "skill": entry.get("skill"),
                "text": entry.get("text", "")}
        with open(ARCHIVE_DIR / f"{when.strftime('%Y-%m')}.jsonl", "a") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
        return True
    except Exception:
        return False


def _iso(ts) -> str | None:
    return datetime.fromtimestamp(ts, TZ).isoformat(timespec="seconds") if ts else None


def _delete(message_id) -> bool:
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{_token()}/deleteMessage",
        data=json.dumps({"chat_id": TG_CHAT_ID, "message_id": int(message_id)}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return bool(json.loads(r.read()).get("ok"))
    except Exception:
        return False


def _remove(entry: dict, message_id, reason: str, now: float, dry_run: bool = False) -> str:
    """archive -> delete. Returns 'removed' | 'archived_only' | 'kept' (archive failed)."""
    if dry_run:
        return "removed"
    # Archive BEFORE deleting; mark the line as deleted optimistically and
    # correct it with a second line only in the rare refused-delete case.
    if not _archive(entry, message_id, reason, True, now):
        return "kept"
    if _delete(message_id):
        return "removed"
    _archive(entry, message_id, reason + " (telegram refused the delete)", False, now)
    return "archived_only"


# ── called by senders / handlers ────────────────────────────────────────────────

def track(message_id, *, kind: str, skill: str, text: str, stream: str | None = None, keep: bool = False,
          ttl_hours: float | None = None) -> bool:
    """Record a message that was just sent. kind: 'answerable' | 'notice'.

    `ttl_hours` makes it go that many hours after sending, answered or not (rule 4)."""
    try:
        if not message_id:
            return False
        with _locked_state() as state:
            state["messages"][str(message_id)] = {
                "kind": kind, "skill": skill, "stream": stream, "text": text, "ttl_hours": ttl_hours,
                "sent_at": time.time(), "answered_at": None, "keep": keep}
        return True
    except Exception:
        return False


def mark_answered(message_id, final_text: str, *, skill: str = "", keep: bool = False) -> bool:
    """The prompt is fully answered and now reads `final_text`. Works even if
    the message was never track()ed — the handler knows the id at tap time, so
    senders don't have to cooperate for rule 1 to apply."""
    try:
        if not message_id:
            return False
        with _locked_state() as state:
            entry = state["messages"].setdefault(str(message_id), {
                "kind": "answerable", "skill": skill, "stream": None, "sent_at": None, "keep": keep})
            entry.update({"kind": "answerable", "text": final_text, "answered_at": time.time()})
            entry["keep"] = bool(entry.get("keep") or keep)
            if skill and not entry.get("skill"):
                entry["skill"] = skill
        return True
    except Exception:
        return False


def remove_now(message_id, final_text: str, *, skill: str = "", reason: str = "answered") -> str:
    """Archive and delete an answered prompt IMMEDIATELY, rather than at the nightly sweep.

    For the handlers whose outcome lives somewhere better than the chat. The user, 2026-10-08, of a
    calendar card still sitting there after he approved it: "when I approve a calendar card and
    it's on my calendar, can we delete the message? It's becoming clutter." The calendar is the
    record, so the card has nothing left to say — and waiting until 4:10am is the clutter.

    Same guarantees as the sweep: archived BEFORE it is deleted, and a message Telegram refuses
    to delete (bots cannot delete anything older than 48h) is reported, not silently lost.
    Returns 'removed' | 'archived_only' | 'kept' — callers fall back to editing the message on
    anything but 'removed'.
    """
    try:
        if not message_id:
            return "kept"
        now = time.time()
        with _locked_state() as state:
            entry = state["messages"].pop(str(message_id), None) or {
                "kind": "answerable", "skill": skill, "stream": None, "sent_at": None}
            entry.update({"text": final_text, "answered_at": now})
            if skill and not entry.get("skill"):
                entry["skill"] = skill
            if entry.get("keep"):
                # The keep rule outranks a caller's wish to tidy: a record of something sent in
                # The user's name stays in the chat (2026-09-20).
                state["messages"][str(message_id)] = entry
                return "kept"
        return _remove(entry, message_id, reason, now)
    except Exception:
        return "kept"


def replace_stream(stream: str, message_ids: list, text: str, *, skill: str,
                   ttl_hours: float | None = None) -> int:
    """A new message of `stream` was just sent (possibly in several chunks).
    Remove the previous one(s) of the same stream, then remember the new ids.
    Returns how many old messages were removed from the chat.

    `ttl_hours` also expires this one on its own, without waiting for a successor — the FYI
    digest is sent once a day but the user wants it gone two hours later (rule 4)."""
    try:
        now = time.time()
        removed = 0
        with _locked_state() as state:
            previous = state["streams"].get(stream) or {}
            for old_id in previous.get("message_ids", []):
                if old_id in message_ids:
                    continue
                entry = {"kind": "stream", "stream": stream, "skill": previous.get("skill", skill),
                         "text": previous.get("text", ""), "sent_at": previous.get("sent_at")}
                if _remove(entry, old_id, "replaced by the next one", now) == "removed":
                    removed += 1
            state["streams"][stream] = {"message_ids": [int(m) for m in message_ids if m], "text": text,
                                        "skill": skill, "sent_at": now, "ttl_hours": ttl_hours}
        return removed
    except Exception:
        return 0


def archive_removed(message_id, text: str, *, skill: str, stream: str | None = None,
                    reason: str = "replaced by the next one", sent_at: float | None = None) -> bool:
    """For a skill that already deletes its own previous message (hydration):
    put the text on file before it goes."""
    return _archive({"kind": "stream", "stream": stream, "skill": skill, "text": text, "sent_at": sent_at},
                    message_id, reason, True, time.time())


# ── nightly sweep ────────────────────────────────────────────────────────────

def sweep(now: float | None = None, dry_run: bool = False, ttl_only: bool = False) -> dict:
    """The removal pass. `ttl_only` does rule 4 and nothing else.

    WHY it exists: a 2-hour ttl needs a sweep every few minutes, but running the WHOLE sweep
    that often would also take rule 1's answered prompts within minutes of the tap — and rule 1
    is "the ✅ line stays for the rest of the day so he can see the tap worked", which the user
    approved on 2026-09-20. So the frequent cron passes ttl_only; the nightly one does not.
    """
    now = now or time.time()
    out = {"answered_removed": 0, "notices_removed": 0, "expired_removed": 0, "archived_only": 0,
           "kept_by_rule": 0, "unanswered_left_alone": 0, "too_old_to_delete": 0, "archive_failed": 0}
    with _locked_state() as state:
        for mid, entry in list(state["messages"].items()):
            age_h = (now - (entry.get("sent_at") or entry.get("answered_at") or now)) / 3600
            answered = bool(entry.get("answered_at"))
            ttl = entry.get("ttl_hours")
            expired = ttl is not None and age_h >= float(ttl)
            due = (answered or expired
                   or (entry.get("kind") == "notice" and age_h >= NOTICE_MIN_AGE_H))
            if ttl_only and not expired:
                continue    # the daily rules belong to the nightly sweep
            if entry.get("keep"):
                # Stays in the chat by rule; archive once it has been answered, then stop tracking.
                if answered:
                    if not dry_run:
                        _archive(entry, mid, "kept in chat by rule", False, now)
                        del state["messages"][mid]
                    out["kept_by_rule"] += 1
                continue
            if not due:
                if age_h > TELEGRAM_DELETE_LIMIT_H and not dry_run:
                    # Still unanswered and now beyond Telegram's delete window — nothing more we can do with it.
                    _archive(entry, mid, "left in chat: unanswered and past telegram's 48h delete limit", False, now)
                    del state["messages"][mid]
                    out["too_old_to_delete"] += 1
                else:
                    out["unanswered_left_alone"] += 1
                continue
            why = "answered" if answered else (f"{ttl:g}h ttl elapsed" if expired else "notice aged out")
            result = _remove(entry, mid, why, now, dry_run)
            if result == "kept":
                out["archive_failed"] += 1
                continue
            out["archived_only" if result == "archived_only" else
                ("answered_removed" if answered else "expired_removed" if expired else "notices_removed")] += 1
            if not dry_run:
                del state["messages"][mid]

        # Streams live in their own record (they are replaced by their successor rather than
        # answered), so a ttl has to be checked here too — this is what expires the FYI digest.
        for stream, rec in list(state["streams"].items()):
            ttl = rec.get("ttl_hours")
            if ttl is None or not rec.get("message_ids"):
                continue
            if (now - (rec.get("sent_at") or now)) / 3600 < float(ttl):
                continue
            for mid in list(rec["message_ids"]):
                entry = {"kind": "stream", "stream": stream, "skill": rec.get("skill"),
                         "text": rec.get("text", ""), "sent_at": rec.get("sent_at")}
                result = _remove(entry, mid, f"{float(ttl):g}h ttl elapsed", now, dry_run)
                if result == "kept":
                    out["archive_failed"] += 1
                    continue
                out["archived_only" if result == "archived_only" else "expired_removed"] += 1
                if not dry_run:
                    rec["message_ids"].remove(mid)
            if not dry_run and not rec["message_ids"]:
                # Keep the record (so the next digest still knows the stream) but with nothing
                # left to remove; drop the ttl so an empty record is not rechecked every sweep.
                rec["ttl_hours"] = None
    return out


def _cli() -> int:
    ap = argparse.ArgumentParser(description="Archive-then-remove answered Telegram messages.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sw = sub.add_parser("sweep")
    sw.add_argument("--dry-run", action="store_true")
    sw.add_argument("--ttl-only", action="store_true",
                    help="Only remove messages whose ttl_hours has elapsed (rule 4).")
    sub.add_parser("show")
    fd = sub.add_parser("find"); fd.add_argument("text"); fd.add_argument("--month", default=None)
    args = ap.parse_args()

    if args.cmd == "sweep":
        print(json.dumps(sweep(dry_run=args.dry_run, ttl_only=args.ttl_only)))
    elif args.cmd == "show":
        with _locked_state() as state:
            for mid, e in state["messages"].items():
                status = "answered" if e.get("answered_at") else "open"
                print(f"{mid:>8} {e.get('kind', ''):10} {status:9} {'KEEP ' if e.get('keep') else ''}"
                      f"{e.get('skill', ''):14} {(e.get('text') or '').splitlines()[0][:70] if e.get('text') else ''}")
            for name, s in state["streams"].items():
                print(f"  stream {name}: current message(s) {s.get('message_ids')}")
    else:
        needle = args.text.lower()
        for f in sorted(ARCHIVE_DIR.glob(f"{args.month or '*'}.jsonl")):
            for line in f.read_text().splitlines():
                if needle in line.lower():
                    e = json.loads(line)
                    print(f"[{e.get('sent_at') or e.get('removed_at')}] ({e.get('skill')}) {e.get('text')}\n")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
