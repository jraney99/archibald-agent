#!/usr/bin/env python3
"""Build a re-filing plan from refile_spec.json — reads Gmail, changes nothing.

Scope: every message that already has a folder, plus the inbox
(`--backfill-after YYYY/MM/DD` adds unfiled archived mail since that date).
Each planned message records its current folders, so apply_refile.py can show
exactly what will happen and write an undo file.

    python3 refile_plan.py [--backfill-after 2023/01/01]
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
import time
from pathlib import Path

import refile_rules as rr

SKILL = Path(__file__).resolve().parent.parent
SNAPSHOTS = SKILL / "filter_snapshots"
HEADERS = ["From", "Subject", "Date", "List-Unsubscribe"]


def parse_from(value: str) -> tuple[str, str]:
    value = value or ""
    name = re.sub(r'"', "", value.split("<")[0]).strip()
    addr = value.split("<")[-1].rstrip(">").strip().lower()
    return name, addr


def scan(svc, query: str, label_names: dict) -> list:
    out, token = [], None
    while True:
        page = svc.users().messages().list(userId="me", q=query, maxResults=500, pageToken=token).execute()
        for ref in page.get("messages", []):
            msg = svc.users().messages().get(userId="me", id=ref["id"], format="metadata",
                                             metadataHeaders=HEADERS).execute()
            hdr = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
            name, addr = parse_from(hdr.get("From", ""))
            ids = msg.get("labelIds", [])
            out.append({"id": ref["id"], "name": name, "addr": addr, "subject": hdr.get("Subject", ""),
                        "date": hdr.get("Date", "")[:16],
                        "labels": [label_names.get(i, i) for i in ids],
                        "in_inbox": "INBOX" in ids, "starred": "STARRED" in ids,
                        "automated": rr.is_automated(addr, ids, "List-Unsubscribe" in hdr)})
        token = page.get("nextPageToken")
        if not token:
            return out


def build(messages: list, rules: list) -> dict:
    moves, untouched = [], collections.Counter()
    for m in messages:
        rule = rr.decide(rules, m["addr"], m["name"], m["subject"], m.get("automated", True),
                         needs_you=m["in_inbox"])
        change = rr.plan_message(rule, m["labels"], m["in_inbox"], m["starred"])
        if change is None:
            if rule is None and (m["in_inbox"] or rr.user_labels(m["labels"])):
                where = "INBOX" if m["in_inbox"] else (rr.user_labels(m["labels"]) or ["?"])[0]
                untouched[(where, m["addr"])] += 1
            continue
        moves.append({"id": m["id"], "from": m["addr"], "subject": m["subject"][:90], "date": m["date"],
                      "was": rr.user_labels(m["labels"]) + (["INBOX"] if m["in_inbox"] else []), **change})
    return {"moves": moves, "untouched": [[w, a, n] for (w, a), n in untouched.most_common()]}


def summarize(plan: dict) -> str:
    flows = collections.Counter()
    for mv in plan["moves"]:
        src = ", ".join(x for x in mv["was"]) or "(no folder)"
        kept = [w for w in mv["was"] if w not in mv["remove"]]
        dst = mv["add"][0] if mv["add"] else (kept[0] if kept else "(archive, no folder)")
        flows[(src, dst)] += 1
    lines = [f"{n:5}  {src}  →  {dst}" for (src, dst), n in sorted(flows.items(), key=lambda kv: (-kv[1], kv[0]))]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backfill-after", help="also plan unfiled archived mail since YYYY/MM/DD")
    parser.add_argument("--scan-cache", help="reuse a saved scan while tuning rules (final plans scan live)")
    args = parser.parse_args()
    import sender_filter
    svc = sender_filter.gmail_service()
    label_names = {l["id"]: l["name"] for l in svc.users().labels().list(userId="me").execute()["labels"]}
    rules = rr.load_rules()
    cache = Path(args.scan_cache) if args.scan_cache else None
    if cache and cache.exists():
        messages = json.loads(cache.read_text())
    else:
        messages = {m["id"]: m for m in scan(svc, "has:userlabels -in:spam -in:trash", label_names)}
        messages.update({m["id"]: m for m in scan(svc, "in:inbox", label_names)})
        if args.backfill_after:
            q = f"-has:userlabels -in:inbox -in:spam -in:trash -from:me after:{args.backfill_after}"
            messages.update({m["id"]: m for m in scan(svc, q, label_names)})
        if cache:
            cache.write_text(json.dumps(messages))
    plan = build(list(messages.values()), rules)
    plan.update({"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "kind": "refile",
                 "scanned": len(messages), "backfill_after": args.backfill_after,
                 "from_cache": bool(cache and cache.exists() and args.scan_cache),
                 "new_labels": sorted({mv["add"][0] for mv in plan["moves"] if mv["add"]}
                                      - set(label_names.values()))})
    out = SNAPSHOTS / f"refile-plan-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps(plan, indent=1))
    print(f"scanned {len(messages)} messages; {len(plan['moves'])} would change\n")
    print(summarize(plan))
    print("\nNew folders:", ", ".join(plan["new_labels"]) or "none")
    print(f"\nPlan written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
