#!/usr/bin/env python3
"""Personal Shopper watchlist (DESIGN.md Module 6) — things to keep looking
for, either because the user asked directly or because a genuine closet gap
(closet_report.py) got confirmed as worth watching for. Powers the proactive
scan flow in SKILL.md: read the active list here, search for real candidates
matching each entry's criteria, run any hits through the normal Shopping
Advisor evaluation (shopping_closet_check.py, style profile, brand sizing).

Usage:
  python3 shopping_watchlist.py add --category <cat> [--max-price N] [--formality N] [--season <season>] [--brands "A,B"] [--notes "..."] [--source user-requested|gap-detected]
  python3 shopping_watchlist.py list [--status active|fulfilled|cancelled|all]
  python3 shopping_watchlist.py fulfill <id> [--linked-item <closet-item-id>]
  python3 shopping_watchlist.py cancel <id>
  python3 shopping_watchlist.py touch <id> [--date YYYY-MM-DD]

All writes here happen immediately — the user asking to watch for something,
or confirming a gap is worth watching, is itself the checkpoint, same
precedent as outfit/care logging elsewhere in this skill.
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is required: pip install pyyaml")

ROOT = Path(__file__).resolve().parent.parent
WATCHLIST_PATH = ROOT / "profile" / "shopping_watchlist.json"
CLOSET_PATH = ROOT / "profile" / "closet.json"
VOCAB_PATH = ROOT / "vocab.yaml"


sys.path.insert(0, str(Path(__file__).resolve().parent))
from _store import load_json, save_json  # noqa: E402


def load(path):
    return load_json(path, [])


def save(path, data):
    save_json(path, data)


def flat_categories(vocab):
    cats = []
    for group in vocab["category_groups"].values():
        cats.extend(group)
    return cats


def cmd_add(args):
    with open(VOCAB_PATH) as f:
        vocab = yaml.safe_load(f)

    if args.category not in flat_categories(vocab):
        sys.exit(f"Unknown category '{args.category}'. Valid: {flat_categories(vocab)}")
    if args.max_price is not None and args.max_price <= 0:
        sys.exit(f"'--max-price {args.max_price:g}' must be a positive number.")
    if args.formality is not None and args.formality not in vocab["formality_scale"]:
        sys.exit(f"'{args.formality}' is not a valid formality. Valid: {list(vocab['formality_scale'].keys())}")
    if args.season and args.season not in vocab["seasons"]:
        sys.exit(f"Unknown season '{args.season}'. Valid: {vocab['seasons']}")
    if args.source not in vocab["watchlist_sources"]:
        sys.exit(f"Unknown source '{args.source}'. Valid: {vocab['watchlist_sources']}")

    watchlist = load(WATCHLIST_PATH)
    date_str = date.today().isoformat()
    same_day_count = sum(1 for w in watchlist if w.get("created_date") == date_str)
    watch_id = f"watch-{date_str}-{same_day_count + 1:03d}"

    watchlist.append({
        "id": watch_id,
        "created_date": date_str,
        "category": args.category,
        "criteria": {
            "max_price": args.max_price,
            "formality": args.formality,
            "season": args.season,
            "brands": [b.strip() for b in args.brands.split(",") if b.strip()] if args.brands else [],
            "notes": args.notes or "",
        },
        "source": args.source,
        "status": "active",
        "last_checked": None,
        "linked_closet_item_id": None,
    })
    save(WATCHLIST_PATH, watchlist)
    print(f"Added {watch_id}: watching for {args.category}" + (f" under ${args.max_price:g}" if args.max_price else ""))


def cmd_list(args):
    watchlist = load(WATCHLIST_PATH)
    if args.status != "all":
        watchlist = [w for w in watchlist if w.get("status") == args.status]
    if args.oldest_first:
        watchlist.sort(key=lambda item: (item.get("last_checked") or "", item.get("created_date") or "", item.get("id") or ""))
    if args.limit is not None:
        if args.limit < 1:
            sys.exit("--limit must be positive")
        watchlist = watchlist[:args.limit]
    if args.json:
        print(json.dumps(watchlist, indent=2))
        return

    if not watchlist:
        scope = "any status" if args.status == "all" else f"status '{args.status}'"
        print(f"No watchlist entries with {scope}.")
        return

    print(f"{len(watchlist)} watchlist entr{'y' if len(watchlist) == 1 else 'ies'}:")
    for w in watchlist:
        c = w.get("criteria", {})
        bits = []
        if c.get("max_price"):
            bits.append(f"under ${c['max_price']:g}")
        if c.get("season"):
            bits.append(c["season"])
        if c.get("brands"):
            bits.append(f"brands: {', '.join(c['brands'])}")
        detail = f" ({', '.join(bits)})" if bits else ""
        checked = f", last checked {w['last_checked']}" if w.get("last_checked") else ", never checked"
        print(f"  - {w.get('id')}: {w.get('category')}{detail} [{w.get('status')}, {w.get('source')}{checked}]")


def cmd_fulfill(args):
    watchlist = load(WATCHLIST_PATH)
    match = next((w for w in watchlist if w.get("id") == args.watch_id), None)
    if not match:
        sys.exit(f"No watchlist entry with id '{args.watch_id}'.")

    if args.linked_item:
        closet = load(CLOSET_PATH)
        if not any(item.get("id") == args.linked_item for item in closet):
            sys.exit(f"No closet item with id '{args.linked_item}' — not linking.")
        match["linked_closet_item_id"] = args.linked_item

    match["status"] = "fulfilled"
    save(WATCHLIST_PATH, watchlist)
    print(f"Marked {args.watch_id} fulfilled.")


def cmd_cancel(args):
    watchlist = load(WATCHLIST_PATH)
    match = next((w for w in watchlist if w.get("id") == args.watch_id), None)
    if not match:
        sys.exit(f"No watchlist entry with id '{args.watch_id}'.")
    match["status"] = "cancelled"
    save(WATCHLIST_PATH, watchlist)
    print(f"Cancelled {args.watch_id}.")


def cmd_update(args):
    entries = load(WATCHLIST_PATH)
    match = next((w for w in entries if w.get("id") == args.watch_id), None)
    if not match:
        sys.exit(f"No watchlist entry with id '{args.watch_id}'.")
    changed = []
    if args.max_price is not None:
        match["criteria"]["max_price"] = args.max_price; changed.append(f"max_price={args.max_price:g}")
    if args.season:
        match["criteria"]["season"] = args.season; changed.append(f"season={args.season}")
    if args.brands:
        match["criteria"]["brands"] = [b.strip() for b in args.brands.split(",") if b.strip()]
        changed.append(f"brands={len(match['criteria']['brands'])}")
    if args.append_note:
        match["criteria"]["notes"] = (match["criteria"].get("notes") or "") + " || " + args.append_note
        changed.append("note")
    if not changed:
        sys.exit("Nothing to change: pass --max-price, --season, --brands or --append-note.")
    save(WATCHLIST_PATH, entries)
    print(f"Updated {args.watch_id}: {', '.join(changed)}")


def cmd_touch(args):
    watchlist = load(WATCHLIST_PATH)
    match = next((w for w in watchlist if w.get("id") == args.watch_id), None)
    if not match:
        sys.exit(f"No watchlist entry with id '{args.watch_id}'.")
    match["last_checked"] = args.date or date.today().isoformat()
    save(WATCHLIST_PATH, watchlist)
    print(f"Updated last_checked for {args.watch_id} to {match['last_checked']}.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add")
    p_add.add_argument("--category", required=True)
    p_add.add_argument("--max-price", type=float, default=None)
    p_add.add_argument("--formality", type=int, default=None)
    p_add.add_argument("--season", default=None)
    p_add.add_argument("--brands", default=None, help="comma-separated")
    p_add.add_argument("--notes", default=None)
    p_add.add_argument("--source", default="user-requested")

    p_list = sub.add_parser("list")
    p_list.add_argument("--status", default="active")
    p_list.add_argument("--limit", type=int)
    p_list.add_argument("--oldest-first", action="store_true")
    p_list.add_argument("--json", action="store_true")

    p_fulfill = sub.add_parser("fulfill")
    p_fulfill.add_argument("watch_id")
    p_fulfill.add_argument("--linked-item", default=None)

    p_cancel = sub.add_parser("cancel")
    p_cancel.add_argument("watch_id")

    p_touch = sub.add_parser("touch")
    p_update = sub.add_parser("update", help="change an entry's criteria (2026-10-13: price caps)")
    p_update.add_argument("watch_id")
    p_update.add_argument("--max-price", type=float)
    p_update.add_argument("--season")
    p_update.add_argument("--brands", help="comma-separated; replaces the list")
    p_update.add_argument("--append-note")

    p_touch.add_argument("watch_id")
    p_touch.add_argument("--date", default=None)

    args = parser.parse_args()

    if args.command == "add":
        cmd_add(args)
    elif args.command == "list":
        cmd_list(args)
    elif args.command == "fulfill":
        cmd_fulfill(args)
    elif args.command == "cancel":
        cmd_cancel(args)
    elif args.command == "update":
        cmd_update(args)
    elif args.command == "touch":
        cmd_touch(args)


if __name__ == "__main__":
    main()
