#!/usr/bin/env python3
"""
Archive stale inbox mail — remove the INBOX label from messages older than N
days. Nothing is deleted: archived mail stays in All Mail, keeps every other
label, is fully searchable, and returns to the inbox if the thread gets a reply.

Used two ways:
  • one-time backlog clear:  python3 archive_old_inbox.py --older-than 90
  • weekly maintenance cron:  python3 archive_old_inbox.py --older-than 45 --quiet

Always keeps: starred mail, unread mail, and anything under a label listed in
KEEP_LABELS. Every archived message id is written to a timestamped file under
filter_snapshots/archived/ so a bulk undo is possible.

Flags:
  --older-than N   age threshold in days (required)
  --dry-run        count what would be archived, touch nothing
  --quiet          only emit output (and a Telegram note) when something changed
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parent.parent / "shared"))
import sender_filter as sf  # noqa: E402

ARCHIVE_DIR = SCRIPT_DIR.parent / "filter_snapshots" / "archived"
KEEP_LABELS: list[str] = []   # e.g. ["Records/Jobs"] — never archive mail under these


def _query(days: int) -> str:
    q = f"in:inbox older_than:{days}d -is:starred -is:unread"
    for lb in KEEP_LABELS:
        q += f' -label:"{lb}"'
    return q


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--older-than", type=int, required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    svc = sf.gmail_service()
    q = _query(args.older_than)

    ids, page = [], None
    while True:
        r = svc.users().messages().list(
            userId="me", q=q, maxResults=500, pageToken=page).execute()
        ids += [m["id"] for m in r.get("messages", [])]
        page = r.get("nextPageToken")
        if not page:
            break

    if args.dry_run:
        print(f"[dry-run] would archive {len(ids)} messages "
              f"(in:inbox older_than:{args.older_than}d, keeping starred/unread"
              + (f"/{'/'.join(KEEP_LABELS)}" if KEEP_LABELS else "") + ")")
        return

    if not ids:
        if not args.quiet:
            print(f"Nothing older than {args.older_than}d to archive. 🎩")
        return

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    rec = ARCHIVE_DIR / f"archived-{stamp}-{args.older_than}d.txt"
    rec.write_text("\n".join(ids) + "\n")

    archived = 0
    for i in range(0, len(ids), 1000):
        batch = ids[i:i + 1000]
        svc.users().messages().batchModify(
            userId="me", body={"ids": batch, "removeLabelIds": ["INBOX"]}).execute()
        archived += len(batch)
        time.sleep(0.3)

    msg = (f"🗃 Archived {archived} inbox messages older than {args.older_than} "
           f"days (starred + unread kept). Nothing deleted — all still in All "
           f"Mail. Undo list: {rec.name}")
    print(msg)
    try:
        from send_telegram import send_chunk
        send_chunk(msg + " 🎩")
    except Exception:
        pass


if __name__ == "__main__":
    main()
