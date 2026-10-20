#!/usr/bin/env python3
"""Find the user's outreach threads and plan filing them under Personal/Networking.

Reads Gmail, changes nothing. A thread counts as outreach when:
  * The user wrote the first message (it is not a reply),
  * it went to 1–3 real people — not himself, family, or an automated address,
  * the subject or opening lines ask for time, advice, an intro or a follow-up.
The whole thread (his messages and their replies) gets the one label. Threads
that already have a folder are left alone (one email, one folder), and nothing
is moved into or out of the inbox.

    python3 networking_plan.py          # writes a plan for apply_refile.py
"""
from __future__ import annotations

import json
import re
import sys
import time
from email.utils import getaddresses
from pathlib import Path

import refile_rules as rr

LABEL = "Personal/Networking"
SNAPSHOTS = Path(__file__).resolve().parent.parent / "filter_snapshots"
OWN = {"owner@example.com", "owner.alt@example.com", "owner@mail.example",
       "owner@alumni.example.edu", "owner@work.example"}
FAMILY_RE = re.compile(r"family", re.I)   # stand-in for the family surname: mail to relatives is not networking
ASK_RE = re.compile(
    r"\b(coffee|catch(ing)? up|grab (a )?(lunch|breakfast|drink|coffee)|"
    r"(15|20|30) minutes|your time|a (quick|brief) (call|chat)|hop on a call|"
    r"pick your brain|your (advice|perspective|guidance|insight)|informational|"
    r"introduc(e|tion|ing)|\bintro\b|connect(ing|ion)?\b|reconnect|breakfast|lunch|would love to (meet|connect|chat|learn)|"
    r"internship|opportunit(y|ies)|resume|follow(ing)?[ -]?up|thank you for (your time|meeting|speaking)|"
    r"great (to meet|meeting|speaking)|nice (to meet|meeting)|availability|are you (free|available))\b", re.I)
NOT_OUTREACH_RE = re.compile(
    r"\b(invoice|receipt|order|unsub(scribe)?|password|verification|lease|rent|refund|payment|"
    r"currently unavailable|out of office|auto(matic)?[ -]?reply|added class|quiz|sick|"
    r"study this week|expo help|club meeting)\b|^\s*(hey|schedule)\s*$|- schedule\s*$", re.I)
# Schools the user attended: mail to teachers is coursework, not networking.
NOT_PEOPLE_RE = re.compile(r"@school-district\.example$|^(bounce|unsubscribe|m-[a-z0-9]{20,})|^[^@]{40,}@", re.I)
# The user's veto (2026-09-17): borderline threads he chose to leave in the general archive.
NOT_NETWORKING = ("friend1@example.com", "@vendor.example", "friend2@example.com",
                  "friend3@example.com", "classmate@university.example")
HEADERS = ["From", "To", "Cc", "Subject", "Date", "In-Reply-To"]


def recipients(hdr: dict) -> list:
    pairs = getaddresses([hdr.get("To", ""), hdr.get("Cc", "")])
    return [(name, addr.lower()) for name, addr in pairs if "@" in addr]


def is_outreach(hdr: dict, snippet: str) -> bool:
    if hdr.get("In-Reply-To"):
        return False                       # a reply: someone else started it
    subject = hdr.get("Subject", "")
    if re.match(r"^\s*(re|fwd?)\s*:", subject, re.I):
        return False
    people = [(n, a) for n, a in recipients(hdr) if a not in OWN]
    if not 1 <= len(people) <= 3:
        return False
    for name, addr in people:
        if rr.AUTOMATED_ADDRESS_RE.search(addr) or FAMILY_RE.search(addr) or FAMILY_RE.search(name or ""):
            return False
        if addr.endswith(NOT_NETWORKING) or NOT_PEOPLE_RE.search(addr) or re.match(
                r"^(support|help|info|hello|sales|billing|admin|contact|team|service|careers|jobs|membership|leasing)@", addr):
            return False
    text = f"{subject} {snippet or ''}"
    return bool(ASK_RE.search(text)) and not NOT_OUTREACH_RE.search(subject)


def main() -> int:
    import sender_filter
    svc = sender_filter.gmail_service()
    names = {l["id"]: l["name"] for l in svc.users().labels().list(userId="me").execute()["labels"]}
    firsts, token = {}, None
    while True:
        page = svc.users().messages().list(userId="me", q="in:sent", maxResults=500, pageToken=token).execute()
        for ref in page.get("messages", []):
            msg = svc.users().messages().get(userId="me", id=ref["id"], format="metadata",
                                             metadataHeaders=HEADERS).execute()
            hdr = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
            if is_outreach(hdr, msg.get("snippet", "")):
                firsts[msg["threadId"]] = {"to": ", ".join(a for _, a in recipients(hdr) if a not in OWN),
                                           "subject": hdr.get("Subject", "")[:80], "date": hdr.get("Date", "")[:16]}
        token = page.get("nextPageToken")
        if not token:
            break
    moves, threads, skipped = [], [], 0
    for tid, first in firsts.items():
        th = svc.users().threads().get(userId="me", id=tid, format="minimal").execute()
        msgs = th.get("messages", [])
        if any(rr.user_labels([names.get(i, i) for i in m.get("labelIds", [])]) for m in msgs):
            skipped += 1                   # already has a folder
            continue
        threads.append({**first, "messages": len(msgs)})
        moves += [{"id": m["id"], "from": first["to"], "subject": first["subject"], "date": first["date"],
                   "was": [], "add": [LABEL], "remove": [], "rule": "networking-outreach"} for m in msgs]
    plan = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "kind": "networking",
            "threads": threads, "moves": moves, "skipped_already_filed": skipped}
    out = SNAPSHOTS / f"networking-plan-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps(plan, indent=1))
    print(f"{len(threads)} outreach threads ({len(moves)} emails) → {LABEL}; {skipped} skipped (already filed)")
    print(f"Plan written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
