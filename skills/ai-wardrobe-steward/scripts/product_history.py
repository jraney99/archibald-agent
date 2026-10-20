#!/usr/bin/env python3
"""Durable product/variant, price and stock history for store scans."""
import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATH = ROOT / "profile" / "product_history.json"


sys.path.insert(0, str(Path(__file__).resolve().parent))
from _store import load_json, save_json  # noqa: E402


def load(): return load_json(PATH, [])
def save(x): save_json(PATH, x)


# The three stored verdicts, plus the near-misses the scan has actually typed
# (2026-09-17 "reject", 2026-09-18 "pending_evaluation"), each of which cost a failed call.
VERDICT_WORDS = {"reject": "rejected", "rejects": "rejected", "recommend": "recommended",
                 "pending": "candidate", "pending_evaluation": "candidate", "evaluate": "candidate"}


def verdict(word: str) -> str:
    w = word.strip().lower().replace("-", "_")
    return VERDICT_WORDS.get(w, w)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="command", required=True)
    seen = sub.add_parser("seen")
    for flag in ("url", "retailer", "name", "color", "color-code", "size", "status", "sku", "product-id", "source"):
        seen.add_argument(f"--{flag}", required=flag in ("url", "retailer", "name"), default="")
    seen.add_argument("--price", type=float)
    seen.add_argument("--watch-id", default="")
    seen.add_argument("--verdict", type=verdict, choices=["candidate", "recommended", "rejected"], default="candidate")
    seen.add_argument("--reason", default="")
    sub.add_parser("changes")
    args = ap.parse_args()
    records = load()
    if args.command == "changes":
        print(json.dumps([r for r in records if r.get("changed")], indent=2)); return
    stable_id = args.sku or args.product_id
    identity = "|".join((args.retailer.lower(), stable_id.lower(), args.name.lower() if not stable_id else "", args.color_code.lower() or args.color.lower(), args.size.lower(), args.url.split("?")[0] if not stable_id else ""))
    pid = hashlib.sha256(identity.encode()).hexdigest()[:16]
    now = datetime.now().astimezone().isoformat()
    record = next((r for r in records if r.get("id") == pid), None)
    snapshot = {"at": now, "price": args.price, "status": args.status}
    if record:
        prior = record.get("history", [{}])[-1]
        record["changed"] = prior.get("price") != args.price or prior.get("status") != args.status
        record["last_seen"] = now
        record["history"].append(snapshot)
        record["history"] = record["history"][-20:]
        record.update({"verdict": args.verdict, "reason": args.reason})
    else:
        record = {"id": pid, "retailer": args.retailer, "name": args.name, "sku": args.sku,
                  "product_id": args.product_id, "color": args.color, "color_code": args.color_code,
                  "size": args.size, "url": args.url, "source": args.source, "watch_id": args.watch_id, "first_seen": now,
                  "last_seen": now, "verdict": args.verdict, "reason": args.reason,
                  "changed": True, "history": [snapshot]}
        records.append(record)
    save(records)
    print(json.dumps(record, indent=2))


if __name__ == "__main__": main()
