#!/usr/bin/env python3
"""Deterministic entry point for every button under an outfit board.

One tap, one message: whatever the tap changes is written back into the board's own
caption (and photo, after a swap) and the reply is NO_REPLY. A reply is printed only
when the board cannot be edited, so nothing is ever silently dropped.
Buttons and wording approved by the user 2026-09-20 and 2026-09-28.
"""
import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
SCRIPT = SCRIPTS / "outfit_proposals.py"
ROOT = SCRIPTS.parent
PENDING_WEAR = ROOT / "profile" / "pending_wear_report.json"
sys.path.insert(0, str(SCRIPTS))
from proposal_caption import never_ack  # noqa: E402

ACTIONS = ["planned", "worn", "skip", "not-worn", "alternate", "too-formal", "too-casual", "too-warm", "too-cold", "unavailable", "never",
           "swap-top", "swap-bottoms", "swap-shoes",
           # 2026-10-13: the four-button board. These only change which buttons are shown.
           "menu-main", "menu-swap", "menu-wrong"]
FALLBACK = {"planned": "Outfit planned. 🎩", "worn": "Outfit and wear history recorded. 🎩", "skip": "Skipped. 🎩",
            "alternate": "Recorded—ask me for another option and I’ll avoid this combination. 🎩",
            "too-formal": "Recorded as too formal. 🎩", "too-casual": "Recorded as too casual. 🎩",
            "too-warm": "Recorded as too warm. 🎩", "too-cold": "Recorded as too cold. 🎩",
            "unavailable": "Recorded that an item is unavailable. 🎩"}
ASK_WHAT_WORN = "Noted. What did you wear instead? A few words or a photo is plenty. 🎩"


def switch_off(meta):
    """The 🚫 button: one tap stops outfits for that person, venue or kind of event (approved 2026-09-20)."""
    profile, kind, today = ROOT / "profile", meta.get("kind") or "", datetime.now().date().isoformat()
    def update(name, default, change):
        path = profile / name
        data = json.loads(path.read_text()) if path.exists() else default
        change(data); path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    if kind.startswith("meeting-") and meta.get("person"):
        person = meta["person"]; key = (person.get("email") or person.get("name") or "").lower()
        update("people.json", {"self_emails": [], "internal_domains": [], "people": {}}, lambda d: d.setdefault("people", {}).update(
            {key: {"name": person.get("name"), "category": "no-outfits", "evidence": f"The user {today}: tapped 🚫 on an outfit board for a meeting with them."}}))
    elif kind == "dining" and meta.get("venue_key"):
        update("venues.json", {}, lambda d: d.update({meta["venue_key"]: {"dressy": False, "name": meta.get("venue_name"), "evidence": f"The user {today}: tapped 🚫 on its outfit board"}}))
    elif kind == "hosted-event" and meta.get("matched_phrase"):
        update("triage_preferences.json", {}, lambda d: d.setdefault("never_phrases", []).append(meta["matched_phrase"]))
    elif kind in ("flight", "golf-round"):
        update("triage_preferences.json", {}, lambda d: d.setdefault("never_kinds", []).append(kind))
    else:
        return None
    return never_ack(meta)


def find_proposal(proposal_id):
    proposals = json.loads((ROOT / "profile/outfit_proposals.json").read_text())
    return next((p for p in proposals if p.get("id") == proposal_id), None)


def note(proposal_id, text):
    """Remember a tap on the board itself, so the caption can show it."""
    path = ROOT / "profile/outfit_proposals.json"
    proposals = json.loads(path.read_text())
    for p in proposals:
        if p.get("id") == proposal_id:
            p.setdefault("meta", {}).setdefault("notes", []).append(text)
    path.write_text(json.dumps(proposals, indent=2))


def edit(proposal_id, keep_buttons=True, new_photo=False, menu=None):
    from send_outfit_proposal import edit_board       # needs the bot token; imported only when a real edit is attempted
    proposal = find_proposal(proposal_id)
    return bool(proposal) and edit_board(proposal, keep_buttons=keep_buttons, new_photo=new_photo, menu=menu)


def record(proposal_id, action, note_text=""):
    cmd = [sys.executable, str(SCRIPT), "action", "--id", proposal_id, "--action", action]
    if note_text:
        cmd += ["--note", note_text]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode:
        print(result.stdout + result.stderr); raise SystemExit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", required=True)
    ap.add_argument("--action", choices=ACTIONS, required=True)
    args = ap.parse_args()
    proposal = find_proposal(args.id)
    if not proposal:
        print(f"No outfit proposal with id '{args.id}'."); raise SystemExit(1)

    if args.action.startswith("menu-"):
        which = args.action.split("-", 1)[1]
        menu = None if which == "main" else which
        # nothing is recorded by opening a menu; only the keyboard changes
        print("NO_REPLY" if edit(args.id, menu=menu) else "Tap one of the options under the board. 🎩"); return

    if args.action.startswith("swap-"):
        import outfit_swap
        swap, problem = outfit_swap.apply(args.id, args.action.split("-", 1)[1])
        if problem:
            print(f"{problem} 🎩"); return
        print("NO_REPLY" if edit(args.id, new_photo=True) else f"Swapped {swap['old_label']} → {swap['new_label']}. 🎩"); return

    if args.action == "never":
        ack = switch_off(proposal.get("meta") or {})
        if not ack:
            print("Nothing to switch off for this board. 🎩"); return
        record(args.id, "never")
        note(args.id, ack.replace(" 🎩", ""))
        print("NO_REPLY" if edit(args.id, keep_buttons=False) else ack); return

    if args.action == "worn":
        record(args.id, "worn")
        subprocess.run([sys.executable, str(SCRIPTS / "mark_after_wear.py"), "--items", ",".join(proposal.get("items", []))], capture_output=True, text=True)
        print("NO_REPLY" if edit(args.id, keep_buttons=False) else FALLBACK["worn"]); return

    if args.action == "not-worn":
        record(args.id, "skip", "Wore something else.")
        # The gateway tells the agent this question is open (callback-dispatch/pending-context.mjs);
        # it logs the answer with `outfit_proposals.py actual` per references/21-wear-reports.md.
        PENDING_WEAR.write_text(json.dumps({"proposal_id": args.id, "event_title": proposal.get("event_title"),
                                            "activity": proposal.get("activity", "general"), "occasion": proposal.get("occasion"),
                                            "proposed_items": proposal.get("items", []),
                                            "asked_at": datetime.now().astimezone().isoformat(timespec="seconds")}, indent=1))
        edit(args.id, keep_buttons=False)
        print(ASK_WHAT_WORN); return

    record(args.id, args.action)
    if args.action in ("too-formal", "too-casual"):
        import rule_proposals
        offer = rule_proposals.check(proposal.get("activity", "general"), args.action, proposal.get("occasion"))
        if offer and rule_proposals.send(offer):
            print("NO_REPLY"); return          # the rule question IS the reply: one message for one tap
    if args.action in ("too-formal", "too-casual", "too-warm", "too-cold"):
        note(args.id, args.action.replace("-", " ") + ".")
        print("NO_REPLY" if edit(args.id) else FALLBACK[args.action]); return
    print(FALLBACK[args.action])


if __name__ == "__main__":
    main()
