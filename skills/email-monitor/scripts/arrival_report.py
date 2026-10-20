#!/usr/bin/env python3
"""Read-only preview: what E1 would do with the inbox right now. Changes nothing.

    python3 arrival_report.py [--no-ai]
"""
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    spec = importlib.util.spec_from_file_location("daemon_report", HERE / "daemon.py")
    daemon = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(daemon)
    svc = daemon._gmail()
    ids, token = [], None
    while True:
        page = svc.users().messages().list(userId="me", q="in:inbox", maxResults=200, pageToken=token).execute()
        ids += [m["id"] for m in page.get("messages", [])]
        token = page.get("nextPageToken")
        if not token:
            break
    results = daemon._arrival_pass(svc, ids, live=False, use_ai="--no-ai" not in sys.argv)
    print(f"INBOX: {len(ids)} emails")
    for e in results:
        d = e["decision"]
        if d:
            where = d["add"][0] if d["add"] else "(leaves inbox; keeps its folder, if any)"
            print(f"  FILE  → {where:34} [{d['why']}]  {e['from'][:34]:34} | {e['subject'][:48]}")
    left = [e for e in results if not e["decision"]]
    print(f"\n  STAYS: {len(left)} emails (people you correspond with, starred, or unsure)")
    print("\nCONVERSATIONS that would leave or need a nudge:")
    for e in daemon._arrival_threads_pass(svc, live=False):
        d = e["decision"]
        print(f"  {d['action'].upper():7} → {(d['label'] or '(no folder)'):22} [{d['why']}]  {e['with'][:30]:30} | {e['subject'][:44]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
