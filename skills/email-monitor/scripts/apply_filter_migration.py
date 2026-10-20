#!/usr/bin/env python3
"""Apply an approved broad-rule migration plan (filter_migration_plan.py output).

Dry run (default) prints every Gmail change the plan would make and the plan's
sha256. Nothing is modified without --execute, and --execute requires
--expect-sha to equal the sha256 of the exact plan file the user approved (SECURITY.md
Confirmation Protocol: the ✅ covers that plan and nothing else).

Execution order:
  1. verify every rule to delete still exists unchanged (abort on drift)
  2. back up the complete live filter list + labels to filter_snapshots/
  3. create exact-address replacements (skipping identical existing filters)
  4. delete the broad rules
  5. optional: --unsubscribe (List-Unsubscribe links in the plan),
     --trash-backlog (existing non-security mail from Trash-rule senders), and
     --backfill (label existing mail for label-only filing plans; adds labels,
     never removes any)
  6. re-list filters and verify the end state; write a result file

Usage:
  python3 apply_filter_migration.py PLAN.json
  python3 apply_filter_migration.py PLAN.json --execute --expect-sha <sha256> [--unsubscribe] [--trash-backlog] [--backfill]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import email_policy as policy
import sender_filter

SNAPSHOT_DIR = Path(__file__).resolve().parent.parent / "filter_snapshots"


def operations(plan: dict) -> dict:
    creates, deletes, unsubscribes = [], [], []
    for rule in plan["rules"]:
        if rule.get("filter_id"):   # None = create-only entry (no rule to replace)
            deletes.append({"id": rule["filter_id"], "criteria": rule["criteria"],
                            "action": rule["current_action"], "domain": rule["domain"]})
        for sender in rule["senders"]:
            repl = sender.get("replacement")
            if not repl:
                continue
            creates.append({"address": sender["address"], "criteria": repl["criteria"],
                            "action": repl["action"], "kind": sender["recommendation"]})
            if sender["recommendation"] == policy.TRASH and (repl.get("unsubscribe") or {}).get("http"):
                unsubscribes.append({"address": sender["address"],
                                     "url": repl["unsubscribe"]["http"]})
    return {"create": creates, "delete": deletes, "unsubscribe": unsubscribes,
            "backfill": plan.get("backfill", [])}


def resolve_label_names(svc, ops: dict) -> list:
    """Turn addLabelNames into Gmail label ids, creating any missing label.

    Filing plans name labels because a brand-new label has no id until it
    exists. Returns the names that had to be created.
    """
    existing = {lb["name"]: lb["id"] for lb in
                svc.users().labels().list(userId="me").execute().get("labels", [])}
    created = []
    for c in ops["create"]:
        names = c["action"].pop("addLabelNames", [])
        ids = []
        for name in names:
            if name not in existing:
                existing[name] = sender_filter.resolve_label_id(svc, name)
                created.append(name)
            ids.append(existing[name])
        if names:
            c["action"]["addLabelIds"] = c["action"].get("addLabelIds", []) + ids
    for entry in ops["backfill"]:
        entry["label_id"] = existing[entry["label"]]
    return created


def backfill(svc, entries: list) -> dict:
    """Add each entry's label to the existing mail its query matches.

    Adds a label and nothing else — no inbox, read-state or trash changes.
    """
    labelled = {}
    for entry in entries:
        q = f"-in:spam -in:trash {entry['query']}"
        ids, page = [], None
        while True:
            kw = {"userId": "me", "q": q, "maxResults": 500}
            if page:
                kw["pageToken"] = page
            resp = svc.users().messages().list(**kw).execute()
            ids += [m["id"] for m in resp.get("messages", [])]
            page = resp.get("nextPageToken")
            if not page:
                break
        for i in range(0, len(ids), 1000):
            svc.users().messages().batchModify(userId="me", body={
                "ids": ids[i:i + 1000], "addLabelIds": [entry["label_id"]]}).execute()
        labelled[entry["label"]] = ids
    return labelled


def _same(a: dict, b: dict) -> bool:
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def _same_action(a: dict, b: dict) -> bool:
    """Compare actions the way Gmail stores them.

    Gmail drops removeLabelIds:["INBOX"] from a Trash rule — adding TRASH
    already takes the message out of the inbox — so a literal comparison
    reports a freshly created Trash filter as missing, and a re-run would
    create a duplicate.
    """
    def norm(action):
        add = set(action.get("addLabelIds", []))
        remove = set(action.get("removeLabelIds", []))
        if "TRASH" in add:
            remove.discard("INBOX")
        return sorted(add), sorted(remove)

    return norm(a) == norm(b)


def drift(ops: dict, live: list) -> list:
    """Planned deletions whose live filter is missing or changed."""
    by_id = {f["id"]: f for f in live}
    problems = []
    for d in ops["delete"]:
        cur = by_id.get(d["id"])
        if not cur:
            problems.append(f"{d['id']} ({d['domain']}) no longer exists")
        elif not (_same(cur.get("criteria", {}), d["criteria"])
                  and _same_action(cur.get("action", {}), d["action"])):
            problems.append(f"{d['id']} ({d['domain']}) changed since the plan was made")
    return problems


def already_present(create: dict, live: list) -> str:
    for f in live:
        if _same(f.get("criteria", {}), create["criteria"]) and _same_action(f.get("action", {}), create["action"]):
            return f["id"]
    return ""


def verify_end_state(ops: dict, live: list) -> list:
    ids = {f["id"] for f in live}
    problems = [f"broad rule {d['id']} still present" for d in ops["delete"] if d["id"] in ids]
    problems += [f"replacement for {c['address']} missing" for c in ops["create"]
                 if not already_present(c, live)]
    return problems


def print_dry_run(ops: dict, sha: str) -> None:
    print(f"Plan sha256: {sha}")
    print(f"\nCREATE {len(ops['create'])} exact-address filters:")
    for c in ops["create"]:
        shown = c["criteria"].get("query") or c["address"]
        print(f"  + {c['kind']:7} {shown}  action={c['action']}"
              + ("  (security-guarded)" if policy.has_security_guard(c["criteria"]) else ""))
    print(f"\nDELETE {len(ops['delete'])} existing rules:")
    for d in ops["delete"]:
        print(f"  - {d['id']}  {d['domain']}")
    print(f"\nUNSUBSCRIBE (only with --unsubscribe) {len(ops['unsubscribe'])}:")
    for u in ops["unsubscribe"]:
        print(f"  ~ {u['address']}  {u['url'][:80]}")
    if ops["backfill"]:
        print(f"\nBACKFILL (only with --backfill) {len(ops['backfill'])} — adds a label, changes nothing else:")
        for b in ops["backfill"]:
            print(f"  ⊕ {b['label']}  ←  {b['query']}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("plan", type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--expect-sha")
    parser.add_argument("--unsubscribe", action="store_true")
    parser.add_argument("--trash-backlog", action="store_true")
    parser.add_argument("--backfill", action="store_true")
    args = parser.parse_args()

    raw = args.plan.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    plan = json.loads(raw)
    ops = operations(plan)
    if not args.execute:
        print_dry_run(ops, sha)
        print("\nDRY RUN — nothing changed.")
        return 0
    if args.expect_sha != sha:
        print(f"Refusing: plan sha256 {sha} != approved {args.expect_sha}")
        return 2

    svc = sender_filter.gmail_service()
    live = svc.users().settings().filters().list(userId="me").execute().get("filter", [])
    problems = drift(ops, live)
    if problems:
        print("Refusing — live filters drifted from the approved plan:\n  " + "\n  ".join(problems))
        return 3

    stamp = time.strftime("%Y%m%d-%H%M%S")
    labels = svc.users().labels().list(userId="me").execute().get("labels", [])
    backup = SNAPSHOT_DIR / f"filters-{stamp}-pre-migration.json"
    backup.write_text(json.dumps({"filters": live, "labels": labels, "plan_sha256": sha}, indent=2))
    print(f"Backup: {backup} ({len(live)} filters)")

    result = {"plan": str(args.plan), "plan_sha256": sha, "backup": str(backup),
              "labels_created": resolve_label_names(svc, ops),
              "created": {}, "reused": {}, "deleted": [], "unsubscribe": {}, "backlog_trashed": {},
              "backfilled": {}}
    for c in ops["create"]:
        existing = already_present(c, live)
        if existing:
            result["reused"][c["address"]] = existing
            continue
        made = svc.users().settings().filters().create(
            userId="me", body={"criteria": c["criteria"], "action": c["action"]}).execute()
        result["created"][c["address"]] = made.get("id")
    for d in ops["delete"]:
        svc.users().settings().filters().delete(userId="me", id=d["id"]).execute()
        result["deleted"].append(d["id"])
    if args.unsubscribe:
        for u in ops["unsubscribe"]:
            result["unsubscribe"][u["address"]] = sender_filter.try_unsubscribe_http(f"<{u['url']}>")
    if args.trash_backlog:
        for c in ops["create"]:
            if c["kind"] != policy.TRASH:
                continue
            q = f"from:{c['address']} -in:trash -{policy.TRASH_SECURITY_GUARD}"
            ids = [m["id"] for m in svc.users().messages().list(
                userId="me", q=q, maxResults=200).execute().get("messages", [])]
            if ids:
                svc.users().messages().batchModify(userId="me", body={
                    "ids": ids, "addLabelIds": ["TRASH"], "removeLabelIds": ["INBOX"]}).execute()
            result["backlog_trashed"][c["address"]] = ids
    if args.backfill:
        result["backfilled"] = backfill(svc, ops["backfill"])

    after = svc.users().settings().filters().list(userId="me").execute().get("filter", [])
    result["filters_after"] = len(after)
    result["verification_problems"] = verify_end_state(ops, after)
    out = SNAPSHOT_DIR / f"migration-result-{stamp}.json"
    out.write_text(json.dumps(result, indent=2))
    print(json.dumps({k: ({n: len(i) for n, i in v.items()} if k == "backfilled" else v)
                      for k, v in result.items() if k != "backlog_trashed"}, indent=2))
    print(f"Result: {out}")
    return 1 if result["verification_problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
