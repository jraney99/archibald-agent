#!/usr/bin/env python3
"""
Write the pending-booking file and send the user a Telegram proposal with
✅ / ❌ inline buttons. The ✅ tap routes (via RULES.md) to
confirm_booking.py.

This is the required human gate: nothing is booked until the user taps ✅ on a
proposal that names the exact restaurant, date, time and party size.

Before sending, this clicks through to the reservation-details page to
read the authoritative terms (the restaurant page itself does NOT surface
"credit card required" - only the booking flow does), then backs out
without booking. If a card / late-cancel fee applies, the proposal says so
and the pending file records card_required_ack=true so confirm_booking.py
will proceed. If money is taken at booking (deposit/prepay/ticketed), this
refuses to propose at all.

Usage:
    python3 propose_booking.py --slug "restaurant-a-dallas" --name "Restaurant A" \
        --date 2026-08-28 --time "5:30 PM" --party 2 \
        [--duration 90] [--note "anniversary"] [--skip-terms-check]
"""
import os
import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))
import browser_helpers as bh  # noqa: E402

CFG = json.loads((SCRIPT_DIR.parent / "config" / "restaurant_booking_config.json").read_text())
SEL = CFG["selectors"]
PENDING_FILE = Path(os.path.expanduser("~/.openclaw/pending_restaurant_booking.json")).expanduser()
PROPOSAL_SCRIPT = os.path.expanduser("~/.openclaw/workspace/Skills/shared/send_inline_proposal.py")


def seating_phrase(label: str) -> str:
    """"Indoors, Main Dining Room" from OpenTable's "Select standard seating in the Main
    Dining Room dining area". Shown because the seating is a real choice and the user found
    out he was on a patio only after the fact (2026-10-06)."""
    import re
    room = re.search(r"seating in the (.+?) dining area", label, re.I)
    room = room.group(1).strip() if room else label.strip()
    outside = re.search(r"outdoor|patio|terrace|sidewalk|rooftop|garden|street", label, re.I)
    return f"{'Outdoors' if outside else 'Indoors'}, {room}"


def fee_disclosure(amount, condition: str) -> str:
    """What the card on file actually risks, in the restaurant's own terms.

    Both halves are read off the live page and either can be missing, so each gap is
    named rather than papered over. Until 2026-10-06 this line asserted Restaurant A's
    "less than 1 day out" cutoff for every restaurant and fell back to the word "a fee"
    whenever the amount carried cents - which is how Restaurant B's "$35.00 per person,
    after 12:00 pm on the day" reached the user as "a fee ... less than 1 day out".
    """
    money = bh.fee_text(amount)
    if money and condition:
        return f"{money}/person if you no-show or cancel {condition}."
    if money:
        return f"{money}/person if you no-show or cancel late — the page doesn't give the cutoff."
    if condition:
        return f"A per-person fee if you no-show or cancel {condition} — amount not stated."
    return "A per-person no-show / late-cancel fee applies; the page gives neither amount nor cutoff."


def build_parser():
    """The argument surface, as its own function so a test can inspect it.

    THE PICKUP FIELDS (2026-10-09): outing-planner computes the pickup address, how early he must
    be there, and the drive it wants used as the calendar reminder. confirm_booking
    has read all three by name since 2026-10-08 - but this script, the only thing that
    writes the pending file, had no way to accept them, so they were computed and
    dropped on every booking. companion / outing_venue_id let the confirm step log the
    date against the outing history, which nothing did either.
    """
    p = argparse.ArgumentParser()
    p.add_argument("--slug", required=True)
    p.add_argument("--url", default="", help="full restaurant URL from search_availability (preferred)")
    p.add_argument("--name", required=True)
    p.add_argument("--date", required=True, help="YYYY-MM-DD")
    p.add_argument("--time", required=True, help='e.g. "5:30 PM"')
    p.add_argument("--party", type=int, required=True)
    p.add_argument("--duration", type=int, default=CFG["calendar"]["default_duration_minutes"])
    p.add_argument("--note", default="")
    p.add_argument("--skip-terms-check", action="store_true")
    p.add_argument("--pickup-address", default="",
                   help="pickup address; becomes the calendar event's location")
    p.add_argument("--pickup-lead-minutes", type=int, default=0,
                   help="how far before the table he must be AT the pickup")
    p.add_argument("--reminder-minutes", type=int, default=0,
                   help="popup offset; set to the drive so it fires at the leave time")
    p.add_argument("--get-ready-minutes", type=int, default=0,
                   help="a second, earlier popup offset: the start-getting-ready warning")
    p.add_argument("--companion", default="",
                   help="outing-planner: who this is with, so the confirm can log it")
    p.add_argument("--date-venue-id", default="",
                   help="outing-planner: the venue's stable id for the outing history")
    return p


def main():
    args = build_parser().parse_args()

    want_time = bh.norm_time(args.time)
    if not want_time:
        print(f"--time {args.time!r} is not a clock time like '5:30 PM'.", file=sys.stderr)
        sys.exit(2)
    try:
        dt = datetime.strptime(f"{args.date} {want_time}", "%Y-%m-%d %I:%M %p")
    except ValueError as e:
        print(f"bad --date/--time: {e}", file=sys.stderr)
        sys.exit(2)
    dt_iso = dt.strftime("%Y-%m-%dT%H:%M")
    pretty = dt.strftime("%A %b %-d at %-I:%M %p")

    card_required = False
    fee_per_person = None
    fee_condition = ""
    seating = ""
    if not args.skip_terms_check:
        bh.ensure_logged_in()
        terms = bh.verify_booking_terms(
            args.url or args.slug, dt_iso, args.party, want_time, SEL,
            CFG["safety"]["charge_now_patterns"], CFG.get("seating", {}),
        )
        if not terms["reached"]:
            print(f"Could not reach the booking-details page to check terms "
                  f"({terms['note']}). Not proposing. The {want_time} slot may be "
                  f"gone.", file=sys.stderr)
            sys.exit(2)
        if terms["charge_now"]:
            print(f"NOT proposing {args.name}: money is taken at booking "
                  f"({', '.join(sorted(set(terms['charge_now'])))}). Book by hand "
                  f"if the terms are OK.", file=sys.stderr)
            sys.exit(3)
        seating = terms.get("seating", "")
        card_required = terms["card_required"]
        fee_per_person = terms["fee_per_person"]
        fee_condition = terms["fee_condition"]

    lines = [
        "🍽️ Master Wayne - reserve a table?",
        f"  {args.name}",
        f"  {pretty} · party of {args.party}",
    ]
    # ON A PICK-UP, SAY WHEN HE HAS TO BE THERE (2026-10-09, the user said yes). The table
    # time alone is the one number that does not tell him when his evening starts, and
    # until he taps there is no calendar event carrying it.
    if args.pickup_lead_minutes:
        door = dt - timedelta(minutes=args.pickup_lead_minutes)
        lines.append(f"  be at pickup by {door.strftime('%-I:%M %p')}")
        if args.reminder_minutes:
            lines.append(f"  leave by {(door - timedelta(minutes=args.reminder_minutes)).strftime('%-I:%M %p')}")
    if seating:
        lines.append(f"  {seating_phrase(seating)}")
    if args.note:
        lines.append(f"  note: {args.note}")
    if card_required:
        lines.append(f"  ⚠️ Credit card required. {fee_disclosure(fee_per_person, fee_condition)}")
    lines.append("\nTap ✅ and I'll book it as you on OpenTable.")

    PENDING_FILE.write_text(json.dumps({
        "slug": args.slug, "url": args.url, "name": args.name,
        "date": args.date, "time": want_time,
        "party": args.party, "duration_minutes": args.duration, "note": args.note,
        "card_required_ack": bool(card_required),
        "fee_per_person": fee_per_person,
        "fee_condition": fee_condition,
        "seating": seating,
        "target_dt": dt.isoformat(),
        "proposed_at": datetime.now().isoformat(),
        # Carried through to confirm_booking: the evening's real shape, and the
        # bookkeeping that lets a confirmed outing record itself.
        "pickup_address": args.pickup_address,
        "pickup_lead_minutes": args.pickup_lead_minutes or None,
        "reminder_minutes": args.reminder_minutes or None,
        "get_ready_minutes": args.get_ready_minutes or None,
        "companion": args.companion,
        "outing_venue_id": args.outing_venue_id,
    }, indent=2))

    r = subprocess.run([
        "python3", PROPOSAL_SCRIPT,
        "--text", "\n".join(lines),
        "--action-type", "booking",
        "--confirm-label", "✅ Book it",
        "--skip-label", "❌ Skip",
    ], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"proposal send failed: {r.stderr.strip()}", file=sys.stderr)
        sys.exit(1)
    try:
        msg_id = json.loads(r.stdout).get("msg_id")
    except Exception:  # noqa: BLE001
        msg_id = None
    if msg_id:
        sys.path.insert(0, os.path.expanduser("~/.openclaw/workspace/Skills/shared"))
        from pending_proposal import save_message_id
        save_message_id(PENDING_FILE, msg_id)
    print(json.dumps({
        "status": "proposal_sent",
        "card_required": card_required,
        "fee_per_person": fee_per_person,
        "fee_condition": fee_condition,
        "seating": seating,
        # 2026-10-06: the agent followed this script's proposal with a typed prose copy
        # ending "Tap the check to confirm" - two messages for one decision, and the
        # typed one had no buttons, so a tap on it would have done nothing at all.
        "already_sent_to_telegram": True,
        "say_nothing_further": "The proposal is already in the user's chat with its real "
                               "buttons. Do not restate, summarise or re-offer it.",
    }))


if __name__ == "__main__":
    try:
        main()
    except bh.BrowserError as e:
        print(f"browser: {e}", file=sys.stderr)
        sys.exit(5)
