#!/usr/bin/env python3
"""Track worn outfits — powers the Outfit Engine's repeat-vs-favorite
tie-break rule (DESIGN.md Module 5) and the Continuous Learning feedback
loop (DESIGN.md Module 10) — bumps wear_count/last_worn on items and flags
first wears, one of the three sampled moments to ask for feedback.

Usage:
  python3 outfit_history.py recent --days 14
  python3 outfit_history.py log --occasion business-casual --items id1,id2,id3 [--date YYYY-MM-DD] [--notes "..."]
  python3 outfit_history.py favorite <outfit-id>
  python3 outfit_history.py unfavorite <outfit-id>
  python3 outfit_history.py feedback <outfit-id> --note "..." [--favorite | --unfavorite]

`log` only runs after the user has confirmed which outfit they're actually
wearing — that confirmation is the checkpoint, so this writes immediately
rather than going through a separate pending/confirm step. Same for
`feedback` — the user volunteering a reaction is itself the checkpoint.
"""
import argparse
import json
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is required: pip install pyyaml")

ROOT = Path(__file__).resolve().parent.parent
HISTORY_PATH = ROOT / "profile" / "outfit_history.json"
CLOSET_PATH = ROOT / "profile" / "closet.json"
VOCAB_PATH = ROOT / "vocab.yaml"
VALIDATE_SCRIPT = Path(__file__).resolve().parent / "validate_closet.py"


def load(path):
    if not path.exists():
        return []
    with open(path) as f:
        return json.load(f)


def save(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def parse_date(s):
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def cmd_recent(args):
    history = load(HISTORY_PATH)
    cutoff = date.today() - timedelta(days=args.days)

    recent = []
    unreadable = []
    for h in history:
        d = parse_date(h.get("date"))
        if d is None:
            unreadable.append(h.get("id", "?"))
            continue
        if d >= cutoff:
            recent.append(h)

    if unreadable:
        plural = len(unreadable) != 1
        print(f"Note: {len(unreadable)} outfit history entr{'ies' if plural else 'y'} {'have' if plural else 'has'} a missing/invalid date and {'were' if plural else 'was'} skipped: {', '.join(unreadable)}")

    if not recent:
        print(f"No outfits logged in the last {args.days} day(s).")
        return

    print(f"{len(recent)} outfit(s) in the last {args.days} day(s):")
    for h in recent:
        fav = " [favorite]" if h.get("favorite") else ""
        items_str = ", ".join(h["items"]) if isinstance(h.get("items"), list) else "(malformed 'items' field)"
        print(f"  - {h['id']} ({h['date']}, {h['occasion']}){fav}: {items_str}")


def cmd_log(args):
    with open(VOCAB_PATH) as f:
        vocab = yaml.safe_load(f)
    if args.occasion not in vocab["occasion_formality"]:
        sys.exit(f"Unknown occasion '{args.occasion}'. Valid: {list(vocab['occasion_formality'].keys())}")

    if args.activity and args.activity not in vocab.get("activities", []):
        sys.exit(f"Unknown activity '{args.activity}'. Valid: {vocab.get('activities', [])}")

    history = load(HISTORY_PATH)
    closet = load(CLOSET_PATH)
    closet_by_id = {item["id"]: item for item in closet}

    item_ids = [i.strip() for i in args.items.split(",") if i.strip()]

    seen = set()
    duplicates = {i for i in item_ids if i in seen or seen.add(i)}
    if duplicates:
        sys.exit(f"Duplicate item id(s) in --items — an outfit can't include the same item twice: {', '.join(duplicates)}")

    missing = [i for i in item_ids if i not in closet_by_id]
    if missing:
        sys.exit(f"Unknown item id(s), not logging: {', '.join(missing)}")

    date_str = args.date or date.today().isoformat()
    same_day_count = sum(1 for h in history if h.get("date") == date_str)
    outfit_id = f"outfit-{date_str}-{same_day_count + 1:03d}"

    # Validate every wear_count is numeric BEFORE mutating or writing anything —
    # otherwise a bad value mid-loop could leave history claiming the outfit
    # happened while only some items' wear counts actually got bumped.
    bad_wear_counts = [i for i in item_ids if not isinstance(closet_by_id[i].get("wear_count", 0), (int, float))]
    if bad_wear_counts:
        sys.exit(f"Item(s) have a non-numeric wear_count, not logging: {', '.join(bad_wear_counts)}")

    first_wear_ids = [i for i in item_ids if closet_by_id[i].get("wear_count", 0) == 0]

    for item_id in item_ids:
        item = closet_by_id[item_id]
        item["wear_count"] = item.get("wear_count", 0) + 1
        item["last_worn"] = date_str

    history.append({
        "id": outfit_id,
        "date": date_str,
        "occasion": args.occasion,
        "activity": args.activity,
        "items": item_ids,
        "favorite": False,
        "notes": args.notes or "",
        "feedback": "",
    })
    save(HISTORY_PATH, history)
    save(CLOSET_PATH, closet)

    print(f"Logged {outfit_id}. Wear count updated for: {', '.join(item_ids)}")
    if first_wear_ids:
        print(f"First wear for: {', '.join(first_wear_ids)} — good moment to ask how it went.")

    result = subprocess.run([sys.executable, str(VALIDATE_SCRIPT)], capture_output=True, text=True)
    print(result.stdout.strip())
    if result.returncode != 0:
        print("Validation failed after wear-count update — review closet.json.", file=sys.stderr)
        sys.exit(1)


def set_favorite(outfit_id, value):
    history = load(HISTORY_PATH)
    match = next((h for h in history if h.get("id") == outfit_id), None)
    if not match:
        sys.exit(f"No outfit history entry with id '{outfit_id}'.")
    match["favorite"] = value
    save(HISTORY_PATH, history)
    print(f"{'Marked' if value else 'Unmarked'} {outfit_id} as favorite.")


def cmd_feedback(args):
    history = load(HISTORY_PATH)
    match = next((h for h in history if h.get("id") == args.outfit_id), None)
    if not match:
        sys.exit(f"No outfit history entry with id '{args.outfit_id}'.")

    match["feedback"] = args.note
    if args.favorite:
        match["favorite"] = True
    elif args.unfavorite:
        match["favorite"] = False

    save(HISTORY_PATH, history)
    print(f"Recorded feedback for {args.outfit_id}: \"{args.note}\"")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_recent = sub.add_parser("recent")
    p_recent.add_argument("--days", type=int, default=14)

    p_log = sub.add_parser("log")
    p_log.add_argument("--occasion", required=True)
    p_log.add_argument("--items", required=True, help="comma-separated item ids")
    p_log.add_argument("--date", default=None)
    p_log.add_argument("--notes", default=None)
    p_log.add_argument("--activity", default=None, help="normalized activity it was worn for (vocab.yaml activities)")

    p_fav = sub.add_parser("favorite")
    p_fav.add_argument("outfit_id")

    p_unfav = sub.add_parser("unfavorite")
    p_unfav.add_argument("outfit_id")

    p_feedback = sub.add_parser("feedback")
    p_feedback.add_argument("outfit_id")
    p_feedback.add_argument("--note", required=True)
    p_feedback.add_argument("--favorite", action="store_true")
    p_feedback.add_argument("--unfavorite", action="store_true")

    args = parser.parse_args()

    if args.command == "recent":
        cmd_recent(args)
    elif args.command == "log":
        cmd_log(args)
    elif args.command == "favorite":
        set_favorite(args.outfit_id, True)
    elif args.command == "unfavorite":
        set_favorite(args.outfit_id, False)
    elif args.command == "feedback":
        cmd_feedback(args)


if __name__ == "__main__":
    main()
