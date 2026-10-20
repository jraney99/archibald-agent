#!/usr/bin/env python3
"""Persist calendar outfit proposals and capture accept/reject feedback."""
import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATH = ROOT / "profile" / "outfit_proposals.json"
HISTORY = ROOT / "profile" / "outfit_history.json"
PENDING_WEAR = ROOT / "profile" / "pending_wear_report.json"
WEAR_ASK_WINDOW_HOURS = 48      # ask the evening of the event, not about something from last week
ENOUGH_WEARS = 3                # confirmed wears of one activity in 30 days: stop asking, we know
SIGNALS = ROOT / "profile" / "style_signals.json"
BOARD = Path(__file__).resolve().parent / "outfit_board.py"


def load(path):
    return json.loads(path.read_text()) if path.exists() else []


def save(path, value):
    path.write_text(json.dumps(value, indent=2))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    create.add_argument("--event-key", required=True)
    create.add_argument("--event-id", default="")
    create.add_argument("--event-title", required=True)
    create.add_argument("--event-start", required=True)
    create.add_argument("--occasion", required=True)
    create.add_argument("--activity", default="general")
    create.add_argument("--items", required=True)
    create.add_argument("--reason", required=True)
    create.add_argument("--assumption", default="")
    create.add_argument("--weather", default="")
    create.add_argument("--feels-like-f", type=float, default=None, help="forecast feels-like as a number, so comfort taps can be learned from")
    create.add_argument("--meta", default="", help="JSON from the brief: why, kind, person, venue, matched_phrase, forecast")
    action = sub.add_parser("action")
    action.add_argument("--id", required=True)
    action.add_argument("--action", choices=["planned", "worn", "skip", "alternate", "too-formal", "too-casual", "too-warm", "too-cold", "unavailable", "never"], required=True)
    action.add_argument("--note", default="")
    sub.add_parser("pending-wear")
    actual = sub.add_parser("actual", help="record what he actually wore instead of the proposal")
    actual.add_argument("--id", required=True)
    actual.add_argument("--items", default="", help="comma-separated closet ids he named; may be empty if nothing is catalogued")
    actual.add_argument("--note", required=True, help="his own words, including any pieces not in the closet")
    args = ap.parse_args()
    proposals = load(PATH)

    if args.command == "create":
        if any(p.get("event_key") == args.event_key and p.get("status") not in ("superseded", "cancelled") for p in proposals):
            sys.exit("Proposal already exists for this exact event version.")
        if args.event_id:
            for previous in proposals:
                if previous.get("event_id") == args.event_id and previous.get("event_key") != args.event_key and previous.get("status") not in ("worn", "cancelled"):
                    previous["status"] = "superseded"
        ids = [x.strip() for x in args.items.split(",") if x.strip()]
        proposal_id = args.event_key[:12]
        board = subprocess.run([sys.executable, str(BOARD), *ids, "--title", args.event_title, "--bare"], capture_output=True, text=True)
        if board.returncode or "MEDIA:" not in board.stdout:
            sys.exit(board.stdout or board.stderr or "Board creation failed")
        board_path = board.stdout.strip().split("MEDIA:", 1)[1]
        proposals.append({"id": proposal_id, "event_key": args.event_key, "event_id": args.event_id, "event_title": args.event_title,
                          "event_start": args.event_start, "occasion": args.occasion, "activity": args.activity, "items": ids,
                          "reason": args.reason, "assumption": args.assumption, "weather": args.weather, "feels_like_f": args.feels_like_f,
                          "meta": json.loads(args.meta) if args.meta else {},
                          "board_path": board_path, "status": "proposed", "created_at": datetime.now().astimezone().isoformat()})
        save(PATH, proposals)
        print(json.dumps(proposals[-1], indent=2))
        return

    if args.command == "pending-wear":
        # Approved 2026-09-19: ask after outfits he planned AND ones he never tapped
        # (asking only after "Plan this" logged zero wears in practice).
        now = datetime.now().astimezone()
        confirmed = {}
        for h in load(HISTORY):
            try:
                recent = (now.date() - datetime.strptime(h.get("date", ""), "%Y-%m-%d").date()).days <= 30
            except ValueError:
                continue
            if recent and h.get("activity"):
                confirmed[h["activity"]] = confirmed.get(h["activity"], 0) + 1
        due = []
        for p in proposals:
            try:
                start = datetime.fromisoformat(p.get("event_start", ""))
            except ValueError:
                continue
            hours_since = (now - start).total_seconds() / 3600
            if p.get("status") in ("planned", "proposed") and 0 < hours_since <= WEAR_ASK_WINDOW_HOURS \
                    and confirmed.get(p.get("activity") or "general", 0) < ENOUGH_WEARS:
                due.append(p)
        print(json.dumps(due, indent=2))
        return

    if args.command == "actual":
        match = next((p for p in proposals if p.get("id") == args.id), None)
        if not match:
            sys.exit(f"No outfit proposal with id '{args.id}'.")
        ids = [x.strip() for x in args.items.split(",") if x.strip()]
        if ids:
            logged = subprocess.run([sys.executable, str(Path(__file__).parent / "outfit_history.py"), "log",
                                     "--occasion", match["occasion"], "--items", ",".join(ids), "--date", match["event_start"][:10],
                                     "--activity", match.get("activity") or "general",
                                     "--notes", f"Worn instead of the proposal for {match['event_title']}: {args.note}"],
                                    capture_output=True, text=True)
            if logged.returncode:
                sys.exit(logged.stdout + logged.stderr)
            subprocess.run([sys.executable, str(Path(__file__).parent / "mark_after_wear.py"), "--items", ",".join(ids)], capture_output=True, text=True)
        match.update({"status": "worn-different", "actual_items": ids, "actual_note": args.note,
                      "response_at": datetime.now().astimezone().isoformat()})
        save(PATH, proposals)
        if PENDING_WEAR.exists() and json.loads(PENDING_WEAR.read_text()).get("proposal_id") == args.id:
            PENDING_WEAR.unlink()
        print(f"Recorded what you wore to {match['event_title']}: {', '.join(ids) if ids else 'nothing catalogued'} ({args.note}).")
        return

    match = next((p for p in proposals if p.get("id") == args.id), None)
    if not match:
        sys.exit(f"No outfit proposal with id '{args.id}'.")
    old = match.get("status")
    repeat_tap = old == args.action      # a second tap on the same button is not a second opinion
    match["status"] = args.action
    match["response_at"] = datetime.now().astimezone().isoformat()
    if args.note:
        match["response_note"] = args.note
    if args.action == "worn":
        result = subprocess.run([sys.executable, str(Path(__file__).parent / "outfit_history.py"), "log",
                                 "--occasion", match["occasion"], "--items", ",".join(match["items"]),
                                 "--date", match["event_start"][:10], "--notes", f"Calendar event: {match['event_title']}",
                                 "--activity", match.get("activity") or "general"],
                                capture_output=True, text=True)
        if result.returncode:
            sys.exit(result.stdout + result.stderr)
    if args.action in ("skip", "alternate", "too-formal", "too-casual", "too-warm", "too-cold", "unavailable") and not repeat_tap:
        signals = load(SIGNALS)
        signals.append({"date": datetime.now().date().isoformat(), "source": "outfit-proposal-response",
                        "confidence": 35, "action": args.action, "proposal_id": match.get("id"), "occasion": match.get("occasion"),
                        "activity": match.get("activity", "general"), "feels_like_f": match.get("feels_like_f"),
                        "items": match.get("items", []), "note": args.note})
        save(SIGNALS, signals)
    save(PATH, proposals)
    print(f"Updated {args.id}: {old} -> {args.action}.")


if __name__ == "__main__":
    main()
