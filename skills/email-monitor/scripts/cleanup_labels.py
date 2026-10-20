#!/usr/bin/env python3
"""Delete Gmail folders that are truly dead. Dry run unless --execute.

A folder is only ever a candidate when ALL of these hold: it holds no mail, it
has no sub-folders, no Gmail filter files into it, and it is not one of the
working folders (Action Needed, Low Priority). Deleting an empty folder loses
nothing; it can be recreated by name at any time.

    python3 cleanup_labels.py            # list candidates
    python3 cleanup_labels.py --execute  # delete them
"""
from __future__ import annotations

import sys

KEEP = {"Action Needed", "Low Priority"}


def candidates(labels: list, counts: dict, filter_label_ids: set) -> list:
    """labels: [{id, name, type}]; counts: id -> (messages, threads)."""
    user = [l for l in labels if l.get("type") == "user"]
    names = {l["name"] for l in user}
    out = []
    for l in user:
        has_children = any(n.startswith(l["name"] + "/") for n in names)
        messages, threads = counts.get(l["id"], (1, 1))      # unknown count = not empty
        if (l["name"] in KEEP or has_children or messages or threads or l["id"] in filter_label_ids):
            continue
        out.append(l)
    return sorted(out, key=lambda l: l["name"])


def main() -> int:
    import sender_filter
    svc = sender_filter.gmail_service()
    labels = svc.users().labels().list(userId="me").execute().get("labels", [])
    counts = {}
    for l in labels:
        if l.get("type") == "user":
            info = svc.users().labels().get(userId="me", id=l["id"]).execute()
            counts[l["id"]] = (info.get("messagesTotal", 0), info.get("threadsTotal", 0))
    filters = svc.users().settings().filters().list(userId="me").execute().get("filter", [])
    fed = {i for f in filters for i in f.get("action", {}).get("addLabelIds", [])}
    dead = candidates(labels, counts, fed)
    print(f"{len(dead)} dead folder(s):")
    for l in dead:
        print("  -", l["name"])
    if "--execute" not in sys.argv:
        print("\nDRY RUN — nothing deleted.")
        return 0
    for l in dead:
        svc.users().labels().delete(userId="me", id=l["id"]).execute()
    print(f"deleted {len(dead)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
