#!/usr/bin/env python3
"""Apply a plan from refile_plan.py. Dry run unless --execute --expect-sha <sha>.

Only ever adds/removes labels (folders and INBOX) — nothing is deleted or
trashed. Before changing anything it writes an undo file holding every
message's previous folders; `--undo <file>` puts them back.

    python3 apply_refile.py PLAN                       # show what would happen
    python3 apply_refile.py PLAN --execute --expect-sha SHA
    python3 apply_refile.py --undo UNDO_FILE --execute
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import sys
import time
from pathlib import Path

import refile_plan

SNAPSHOTS = refile_plan.SNAPSHOTS
BATCH = 1000


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def groups(moves: list) -> dict:
    """Messages sharing the same (add, remove) change, for batchModify."""
    out = collections.defaultdict(list)
    for mv in moves:
        out[(tuple(mv["add"]), tuple(mv["remove"]))].append(mv["id"])
    return out


def ensure_labels(svc, names: set, execute: bool) -> dict:
    """name -> id for every label needed, creating missing ones (parents first)."""
    have = {l["name"]: l["id"] for l in svc.users().labels().list(userId="me").execute()["labels"]}
    have.setdefault("INBOX", "INBOX")
    needed = set()
    for name in names:
        parts = name.split("/")
        needed.update("/".join(parts[:i]) for i in range(1, len(parts) + 1))
    created = []
    for name in sorted(needed - set(have), key=lambda n: n.count("/")):
        created.append(name)
        if execute:
            made = svc.users().labels().create(userId="me", body={
                "name": name, "labelListVisibility": "labelShow",
                "messageListVisibility": "show"}).execute()
            have[name] = made["id"]
    return {"ids": have, "created": created}


def apply_moves(svc, moves: list, label_ids: dict) -> int:
    changed = 0
    for (add, remove), ids in groups(moves).items():
        body = {"addLabelIds": [label_ids[n] for n in add],
                "removeLabelIds": [label_ids[n] for n in remove if n in label_ids]}
        for i in range(0, len(ids), BATCH):
            svc.users().messages().batchModify(userId="me", body={**body, "ids": ids[i:i + BATCH]}).execute()
            changed += len(ids[i:i + BATCH])
    return changed


def undo_moves(moves: list) -> list:
    """The inverse of each move: put back what was removed, take off what was added."""
    return [{"id": mv["id"], "add": list(mv["remove"]), "remove": list(mv["add"])} for mv in moves]


def verify(svc, moves: list, label_ids: dict, sample: int = 40) -> list:
    problems = []
    step = max(1, len(moves) // sample)
    for mv in moves[::step]:
        ids = set(svc.users().messages().get(userId="me", id=mv["id"], format="minimal").execute().get("labelIds", []))
        for name in mv["add"]:
            if label_ids[name] not in ids:
                problems.append(f"{mv['id']}: missing {name}")
        for name in mv["remove"]:
            if label_ids.get(name) in ids:
                problems.append(f"{mv['id']}: still has {name}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("plan", nargs="?")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--expect-sha")
    parser.add_argument("--undo")
    args = parser.parse_args()

    source = Path(args.undo or args.plan or "")
    if not source.is_file():
        parser.error("give a plan file, or --undo <undo file>")
    data = json.loads(source.read_text())
    moves = data["moves"]
    sha = sha256(source)
    print(f"{'Undo' if args.undo else 'Plan'} sha256: {sha}\n{len(moves)} messages")
    print(refile_plan.summarize({"moves": [dict(mv, was=mv.get("was", mv["remove"])) for mv in moves]}))

    if not args.execute:
        print("\nDRY RUN — nothing changed.")
        return 0
    if not args.undo and args.expect_sha != sha:
        print("\nREFUSED: --expect-sha does not match this plan file.")
        return 2

    import sender_filter
    svc = sender_filter.gmail_service()
    names = {n for mv in moves for n in mv["add"] + mv["remove"]}
    labels = ensure_labels(svc, {n for n in names if n != "INBOX"}, execute=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    if not args.undo:
        undo = SNAPSHOTS / f"refile-undo-{stamp}.json"
        undo.write_text(json.dumps({"plan_sha256": sha, "moves": undo_moves(moves)}, indent=1))
        print(f"Undo file: {undo}")
    changed = apply_moves(svc, moves, labels["ids"])
    problems = verify(svc, moves, labels["ids"])
    result = {"source": str(source), "sha256": sha, "changed": changed,
              "labels_created": labels["created"], "verification_problems": problems}
    out = SNAPSHOTS / f"refile-result-{stamp}.json"
    out.write_text(json.dumps(result, indent=1))
    print(json.dumps(result, indent=1))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
