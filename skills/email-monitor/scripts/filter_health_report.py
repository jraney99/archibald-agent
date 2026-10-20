#!/usr/bin/env python3
"""
Weekly Gmail filter-health report — catches the filtering system decaying.

Sent directly via the Telegram Bot API (no gateway, no LLM tokens). Runs from
the system crontab, Sunday morning CT. Every section is wrapped so one API
failure can't sink the whole report.

Checks
------
  1. Rotting labels    user labels with 0 mail in the last N days — a filter
                       probably broke, or the label is dead
  2. Unfiled repeats   sender addresses sitting in the inbox 3+ times in N days
                       that no filter covers and senders.json doesn't know —
                       candidates to teach / file
  3. Dead filters      label-adding filters whose `from:` matched 0 mail in
                       180 days — candidates to delete
  4. Promo in inbox    CATEGORY_PROMOTIONS still landing in the inbox
  5. Inbox vitals      total / unread

Usage:
    python3 filter_health_report.py [--days N] [--dry-run] [--stdout]
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from collections import Counter
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parent.parent / "shared"))

import sender_filter as sf          # noqa: E402  (auth + config + filter helpers)
from send_telegram import chunk_text, send_chunk  # noqa: E402

DEFAULT_DAYS = 30
DEAD_FILTER_DAYS = 365             # a full year of zero matches = real signal
REPEAT_THRESHOLD = 3
INBOX_SCAN_CAP = 400               # messages fetched for the unfiled-repeats scan
MANUAL_LABELS = {"Personal/Notes", "Records/AutoReplied"}  # hand-applied — silence in "rotting"
SELF_ADDRESSES = {"owner@example.com", "owner.alt@example.com"}

_ADDR_RE = re.compile(r"[\w.+\-]+@[\w.\-]+\.[a-zA-Z]{2,}")


def _addr(sender: str) -> str:
    m = re.search(r"<([^>]+)>", sender or "")
    cand = (m.group(1) if m else (sender or "")).strip().lower()
    m2 = _ADDR_RE.search(cand)
    return m2.group(0) if m2 else cand


def _domain(addr: str) -> str:
    return addr.split("@", 1)[1] if "@" in addr else ""


def _count(svc, q: str, cap: int = 1000) -> int:
    """Exact message count for a query, paged, capped."""
    n, page = 0, None
    while n < cap:
        r = svc.users().messages().list(
            userId="me", q=q, maxResults=500, pageToken=page).execute()
        n += len(r.get("messages", []))
        page = r.get("nextPageToken")
        if not page:
            break
    return n


# ── sections ──────────────────────────────────────────────────────────────────

def rotting_labels(svc, days: int) -> list[str]:
    out = []
    resp = svc.users().labels().list(userId="me").execute()
    for lb in resp.get("labels", []):
        if lb.get("type") != "user" or lb.get("name") in MANUAL_LABELS:
            continue
        try:
            r = svc.users().messages().list(
                userId="me", labelIds=[lb["id"]], q=f"newer_than:{days}d",
                maxResults=1).execute()
        except Exception:
            continue
        if not r.get("messages"):
            out.append(lb["name"])
        time.sleep(0.05)
    return sorted(out)


def unfiled_repeat_senders(svc, days: int, config: dict, covered: set) -> list[tuple[str, int]]:
    ids, page = [], None
    while len(ids) < INBOX_SCAN_CAP:
        r = svc.users().messages().list(
            userId="me", q=f"in:inbox newer_than:{days}d", maxResults=200,
            pageToken=page).execute()
        ids += [m["id"] for m in r.get("messages", [])]
        page = r.get("nextPageToken")
        if not page:
            break
    ids = ids[:INBOX_SCAN_CAP]

    counts: Counter = Counter()
    bulk: set = set()          # addresses that sent at least one bulk message
    for mid in ids:
        try:
            msg = svc.users().messages().get(
                userId="me", id=mid, format="metadata",
                metadataHeaders=["From", "List-Unsubscribe", "Precedence"]).execute()
        except Exception:
            continue
        hdrs = {h["name"].lower(): h["value"]
                for h in msg.get("payload", {}).get("headers", [])}
        a = _addr(hdrs.get("from", ""))
        if not a:
            continue
        counts[a] += 1
        if hdrs.get("list-unsubscribe") or hdrs.get("precedence", "").lower() in ("bulk", "list"):
            bulk.add(a)

    out = []
    for addr, c in counts.most_common():
        if c < REPEAT_THRESHOLD:
            break
        if addr in SELF_ADDRESSES:
            continue
        if addr not in bulk:                       # only suggest filing bulk mail, never people
            continue
        if sf.existing_filter_covers(addr, covered):
            continue
        if sf.match_institution(addr, config):
            continue
        out.append((addr, c))
    return out[:12]


def dead_filters(svc) -> list[tuple[str, str]]:
    try:
        resp = svc.users().settings().filters().list(userId="me").execute()
    except Exception:
        return []
    out = []
    for f in resp.get("filter", []):
        act = f.get("action", {})
        adds = act.get("addLabelIds", []) or []
        # only label-adding filters — a dead TRASH filter is harmless
        if not any(x.startswith("Label_") for x in adds):
            continue
        frm = (f.get("criteria", {}).get("from") or "").strip()
        if not frm or " " in frm or "OR" in frm:
            continue
        try:
            r = svc.users().messages().list(
                userId="me", q=f"from:{frm} newer_than:{DEAD_FILTER_DAYS}d",
                maxResults=1).execute()
        except Exception:
            continue
        if not r.get("messages"):
            out.append((frm, ", ".join(adds)))
        time.sleep(0.05)
    return out


def promo_in_inbox(svc) -> tuple[int, list[str]]:
    ids, page = [], None
    while len(ids) < 200:
        r = svc.users().messages().list(
            userId="me", q="in:inbox category:promotions", maxResults=100,
            pageToken=page).execute()
        ids += [m["id"] for m in r.get("messages", [])]
        page = r.get("nextPageToken")
        if not page:
            break
    doms: Counter = Counter()
    for mid in ids[:60]:
        try:
            msg = svc.users().messages().get(
                userId="me", id=mid, format="metadata",
                metadataHeaders=["From"]).execute()
        except Exception:
            continue
        hdrs = {h["name"].lower(): h["value"]
                for h in msg.get("payload", {}).get("headers", [])}
        d = _domain(_addr(hdrs.get("from", "")))
        if d:
            doms[d] += 1
    return len(ids), [d for d, _ in doms.most_common(6)]


# ── report ────────────────────────────────────────────────────────────────────

def build_report(days: int) -> str:
    svc = sf.gmail_service()
    config = sf.load_config()
    try:
        covered = sf.list_filter_coverage(svc)
    except Exception:
        covered = set()

    L = ["🎩 Weekly inbox-filter health check, Master Wayne.", ""]

    try:
        total = _count(svc, "in:inbox", cap=1500)
        unread = _count(svc, "in:inbox is:unread", cap=1500)
        ttl = f"{total}+" if total >= 1500 else str(total)
        L += [f"Inbox: {ttl} messages, {unread} unread.", ""]
    except Exception as e:
        L += [f"Inbox vitals: unavailable ({e})", ""]

    try:
        rot = rotting_labels(svc, days)
        if rot:
            L.append(f"⚠️ Labels with no mail in {days}d (filter may be broken):")
            L += [f"  • {n}" for n in rot]
        else:
            L.append(f"✅ Every label received mail in the last {days}d.")
        L.append("")
    except Exception as e:
        L += [f"Rotting-label check failed: {e}", ""]

    try:
        rep = unfiled_repeat_senders(svc, days, config, covered)
        if rep:
            L.append(f"📥 Recurring senders in your inbox not yet filed ({days}d):")
            L += [f"  • {a} — {c}×" for a, c in rep]
            L.append("  (reply to file any of these)")
        else:
            L.append("✅ No unfiled repeat senders piling up.")
        L.append("")
    except Exception as e:
        L += [f"Unfiled-repeats check failed: {e}", ""]

    try:
        dead = dead_filters(svc)
        if dead:
            L.append(f"🗑 Label filters with no match in {DEAD_FILTER_DAYS}d "
                     f"(candidates to delete):")
            L += [f"  • {frm}" for frm, _ in dead[:15]]
        else:
            L.append("✅ No obviously-dead label filters.")
        L.append("")
    except Exception as e:
        L += [f"Dead-filter check failed: {e}", ""]

    try:
        pc, pdoms = promo_in_inbox(svc)
        if pc:
            L.append(f"🏷 {pc} promotional messages in the inbox"
                     + (f" — top: {', '.join(pdoms)}" if pdoms else ""))
        else:
            L.append("✅ No promotional mail loose in the inbox.")
    except Exception as e:
        L.append(f"Promo-in-inbox check failed: {e}")

    return "\n".join(L).rstrip()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    ap.add_argument("--dry-run", action="store_true", help="build but don't send")
    ap.add_argument("--stdout", action="store_true", help="also print the report")
    args = ap.parse_args()

    try:
        report = build_report(args.days)
    except Exception as e:
        report = f"⚠️ Weekly filter-health report failed to run: {e} 🎩"

    if args.stdout or args.dry_run:
        print(report)
    if not args.dry_run:
        for chunk in chunk_text(report, 4000):
            send_chunk(chunk)


if __name__ == "__main__":
    main()
