#!/usr/bin/env python3
"""Purchase-decision log (DESIGN.md Module 6) — the only way "fewer
unnecessary purchases" and "more intentional buying" (DESIGN.md success
metrics) become measurable instead of asserted. Records every Shopping
Advisor recommendation, and later what the user actually did about it.

Usage:
  python3 purchase_decisions.py log --category <cat> --recommendation <buy|skip|already-own> --reasoning "..." [--item-description "..."] [--date YYYY-MM-DD]
  python3 purchase_decisions.py outcome <decision-id> --action <bought|skipped> [--linked-item <closet-item-id>]
  python3 purchase_decisions.py report [--days N]

`log` runs right after giving a recommendation — it's a record of advice
given, not a claim about the user's closet, so it writes immediately with no
confirmation needed. `outcome` writes immediately too, once the user states
what they actually did — that statement is the checkpoint, same precedent as
outfit_history.py's `log`.
"""
import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is required: pip install pyyaml")

ROOT = Path(__file__).resolve().parent.parent
DECISIONS_PATH = ROOT / "profile" / "purchase_decisions.json"
CLOSET_PATH = ROOT / "profile" / "closet.json"
VOCAB_PATH = ROOT / "vocab.yaml"


sys.path.insert(0, str(Path(__file__).resolve().parent))
from _store import load_json, save_json  # noqa: E402


def load(path):
    return load_json(path, [])


def save(path, data):
    save_json(path, data)


def parse_date(s):
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def flat_categories(vocab):
    cats = []
    for group in vocab["category_groups"].values():
        cats.extend(group)
    return cats


def cmd_log(args):
    with open(VOCAB_PATH) as f:
        vocab = yaml.safe_load(f)

    if args.category not in flat_categories(vocab):
        sys.exit(f"Unknown category '{args.category}'. Valid: {flat_categories(vocab)}")
    if args.recommendation not in vocab["purchase_recommendations"]:
        sys.exit(f"Unknown recommendation '{args.recommendation}'. Valid: {vocab['purchase_recommendations']}")

    decisions = load(DECISIONS_PATH)
    date_str = args.date or date.today().isoformat()
    same_day_count = sum(1 for d in decisions if d.get("date") == date_str)
    decision_id = f"decision-{date_str}-{same_day_count + 1:03d}"

    decisions.append({
        "id": decision_id,
        "date": date_str,
        "category": args.category,
        "item_description": args.item_description or "",
        "recommendation": args.recommendation,
        "reasoning_summary": args.reasoning,
        "user_action": "unknown",
        "linked_closet_item_id": None,
    })
    save(DECISIONS_PATH, decisions)
    print(f"Logged {decision_id}: {args.recommendation} — {args.category}")


def cmd_outcome(args):
    with open(VOCAB_PATH) as f:
        vocab = yaml.safe_load(f)

    if args.action not in ("bought", "skipped"):
        sys.exit(f"'{args.action}' is not a valid outcome. Valid: bought, skipped")

    decisions = load(DECISIONS_PATH)
    match = next((d for d in decisions if d.get("id") == args.decision_id), None)
    if not match:
        sys.exit(f"No purchase decision with id '{args.decision_id}'.")

    if args.linked_item:
        closet = load(CLOSET_PATH)
        if not any(item.get("id") == args.linked_item for item in closet):
            sys.exit(f"No closet item with id '{args.linked_item}' — not linking.")
        match["linked_closet_item_id"] = args.linked_item

    match["user_action"] = args.action
    save(DECISIONS_PATH, decisions)
    print(f"Recorded outcome for {args.decision_id}: {args.action}")


def cmd_report(args):
    decisions = load(DECISIONS_PATH)

    if args.days is not None:
        cutoff = date.today() - timedelta(days=args.days)
        unreadable = []
        filtered = []
        for d in decisions:
            parsed = parse_date(d.get("date"))
            if parsed is None:
                unreadable.append(d.get("id", "?"))
                continue
            if parsed >= cutoff:
                filtered.append(d)
        decisions = filtered
        if unreadable:
            print(f"Note: {len(unreadable)} decision(s) have a missing/invalid date and were excluded: {', '.join(unreadable)}")

    if not decisions:
        print("No purchase decisions logged" + (f" in the last {args.days} day(s)." if args.days else "."))
        return

    by_recommendation = {}
    by_outcome = {}
    overrides = []
    for d in decisions:
        rec = d.get("recommendation", "?")
        by_recommendation[rec] = by_recommendation.get(rec, 0) + 1
        outcome = d.get("user_action", "unknown")
        by_outcome[outcome] = by_outcome.get(outcome, 0) + 1
        if rec in ("skip", "already-own") and outcome == "bought":
            overrides.append(d["id"])

    print(f"{len(decisions)} decision(s)" + (f" in the last {args.days} day(s)" if args.days else "") + ":")
    print(f"  Recommendations: {by_recommendation}")
    print(f"  Outcomes: {by_outcome}")
    if overrides:
        print(f"  Overrides (advised skip/already-own, bought anyway): {len(overrides)} — {', '.join(overrides)}")
    else:
        print("  Overrides (advised skip/already-own, bought anyway): 0")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_log = sub.add_parser("log")
    p_log.add_argument("--category", required=True)
    p_log.add_argument("--recommendation", required=True)
    p_log.add_argument("--reasoning", required=True)
    p_log.add_argument("--item-description", default=None)
    p_log.add_argument("--date", default=None)

    p_outcome = sub.add_parser("outcome")
    p_outcome.add_argument("decision_id")
    p_outcome.add_argument("--action", required=True)
    p_outcome.add_argument("--linked-item", default=None)

    p_report = sub.add_parser("report")
    p_report.add_argument("--days", type=int, default=None)

    args = parser.parse_args()

    if args.command == "log":
        cmd_log(args)
    elif args.command == "outcome":
        cmd_outcome(args)
    elif args.command == "report":
        cmd_report(args)


if __name__ == "__main__":
    main()
