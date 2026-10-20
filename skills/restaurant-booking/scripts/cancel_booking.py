#!/usr/bin/env python3
"""
Cancel an OpenTable reservation, as the user, after an explicit ✅.

Cancellation can cost money (restaurants with a card-required policy charge
a per-person fee for cancellations less than ~1 day out), so this follows
the same gate as booking:

    list   -> show upcoming reservations (read-only)
    propose --name X --date YYYY-MM-DD
           -> send a Telegram ✅/❌ proposal (confirm:cancelbooking)
    confirm (via RULES.md on the ✅ tap)
           -> actually cancel, refusing if the cancel dialog shows a fee
              that wasn't disclosed in the proposal

Flow mapped live 2026-08-26: confirmation/My-Reservations page -> "Cancel"
button -> dialog "Are you sure you want to cancel this reservation?" ->
"Confirm cancellation" -> "Reservation canceled".

Usage:
    python3 cancel_booking.py list
    python3 cancel_booking.py propose --name "Restaurant A" --date 2026-08-28
    python3 cancel_booking.py confirm
"""
import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))
import browser_helpers as bh  # noqa: E402
sys.path.insert(0, str(SCRIPT_DIR.parent.parent / "shared"))
from pending_proposal import save_message_id, load_message_id, edit_or_fallback  # noqa: E402

CFG = json.loads((SCRIPT_DIR.parent / "config" / "restaurant_booking_config.json").read_text())
SEL = CFG["selectors"]
PENDING_FILE = Path("~/.openclaw/pending_restaurant_cancel.json")
PROPOSAL_SCRIPT = "~/.openclaw/workspace/Skills/shared/send_inline_proposal.py"


def _match_block(blocks, name, date):
    """Pick the dropdown block matching restaurant name AND date. If a
    candidate block exposes a date, it must match; a candidate with no
    visible date is allowed only when it's the sole name match. Refuses
    ambiguous or date-mismatched matches (returns None)."""
    dt = datetime.strptime(date, "%Y-%m-%d")
    date_variants = {dt.strftime("%b %-d, %Y"), dt.strftime("%b %d, %Y")}
    nl = name.lower()
    cands = [b for b in blocks if nl in b["name"].lower()]
    if not cands:
        return None
    dated = [b for b in cands if any(d in b["when_text"] for d in date_variants)]
    if dated:
        return dated[0] if len(dated) == 1 else None
    # no candidate's shown date matches the request
    if any(b["when_text"] for b in cands):
        return None  # dates were shown and none matched -> refuse
    return cands[0] if len(cands) == 1 else None  # no dates shown at all


def do_list():
    bh.ensure_logged_in()
    snap = bh.open_reservations_dropdown()
    blocks = bh.reservation_blocks(snap)
    if not blocks:
        print("No upcoming reservations in the header dropdown.")
        return
    for b in blocks:
        print(f"  {b['name']} - {b['when_text'] or '(date not shown)'}")


def do_propose(name: str, date: str):
    bh.ensure_logged_in()
    dt = datetime.strptime(date, "%Y-%m-%d")

    # Verify the reservation actually exists (and is uniquely identifiable)
    # BEFORE showing the user a ✅/❌ — otherwise the proposal just echoes back
    # whatever --name/--date was passed, and a typo'd or ambiguous name that
    # still happens to uniquely substring-match something in do_confirm()'s
    # matcher would cancel a reservation the user never actually reviewed. Same
    # matcher do_confirm() uses, so "propose says yes" implies "confirm can
    # find it."
    snap = bh.open_reservations_dropdown()
    blocks = bh.reservation_blocks(snap)
    block = _match_block(blocks, name, date)
    if not block:
        names = ", ".join(f"{b['name']} ({b['when_text']})" for b in blocks) or "none"
        print(f"Could not uniquely match an upcoming '{name}' on {date}. "
              f"Dropdown shows: {names}. Not proposing a cancellation.", file=sys.stderr)
        sys.exit(2)

    text = (
        f"🍽️ Master Wayne - cancel this reservation?\n"
        f"  {block['name']}\n"
        f"  {block['when_text'] or dt.strftime('%A %b %-d')}\n\n"
        f"I'll check for a cancellation fee before confirming. Tap ✅ to cancel it."
    )
    PENDING_FILE.write_text(json.dumps({
        "name": name, "date": date, "proposed_at": datetime.now().isoformat(),
    }, indent=2))
    r = subprocess.run([
        "python3", PROPOSAL_SCRIPT, "--text", text,
        "--action-type", "cancelbooking",
        "--confirm-label", "✅ Cancel it", "--skip-label", "❌ Keep it",
    ], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"proposal send failed: {r.stderr.strip()}", file=sys.stderr)
        sys.exit(1)
    try:
        msg_id = json.loads(r.stdout).get("msg_id")
    except Exception:  # noqa: BLE001
        msg_id = None
    if msg_id:
        save_message_id(PENDING_FILE, msg_id)
    print(json.dumps({"status": "cancel_proposal_sent"}))


def do_confirm():
    if not PENDING_FILE.exists():
        print("No pending cancellation.", file=sys.stderr)
        sys.exit(1)
    pending = json.loads(PENDING_FILE.read_text())
    msg_id = load_message_id(PENDING_FILE)
    bh.ensure_logged_in()

    snap = bh.open_reservations_dropdown()
    blocks = bh.reservation_blocks(snap)
    block = _match_block(blocks, pending["name"], pending["date"])
    if not block:
        names = ", ".join(f"{b['name']} ({b['when_text']})" for b in blocks) or "none"
        print(f"Could not uniquely match an upcoming '{pending['name']}' on "
              f"{pending['date']}. Dropdown shows: {names}. Nothing cancelled.",
              file=sys.stderr)
        sys.exit(2)
    if not block["cancel_ref"]:
        print(f"No 'Cancel' link for {block['name']} (already cancelled?). "
              f"Check by hand.", file=sys.stderr)
        sys.exit(2)
    bh.click(block["cancel_ref"])  # -> booking/view page for this reservation
    time.sleep(4)
    snap = bh.snapshot()

    # The showCancelModal URL param doesn't always auto-open the dialog;
    # click the on-page "Cancel" button if the dialog isn't up yet.
    if not re.search(SEL["cancel_dialog_marker"], snap.lower()):
        page_cancel = bh.find_ref(snap, SEL["cancel_button"], role="button")
        if page_cancel:
            bh.click(page_cancel)
            time.sleep(3)
            snap = bh.snapshot()

    if not re.search(SEL["cancel_dialog_marker"], snap.lower()):
        print("Cancel confirmation dialog did not appear. Nothing cancelled.",
              file=sys.stderr)
        sys.exit(2)

    # Fee check - scan ONLY the dialog subtree, not the page (the
    # reservation view always carries the policy text as boilerplate).
    dlg = bh.dialog_subtree(snap)
    fee = bh.scan_patterns(dlg, CFG["safety"]["no_show_fee_patterns"] +
                           [r"you will be charged", r"cancellation fee of",
                            r"subject to a \$?\d", r"charge of \$\d"])
    if fee and not pending.get("fee_ack"):
        bh.screenshot(str(SCRIPT_DIR.parent / "last_cancel.png"))
        print(f"STOPPED: the cancel dialog warns of a charge "
              f"({', '.join(sorted(set(fee)))}). Not cancelling. Re-run "
              f"'confirm' with fee_ack=true in the pending file only if you "
              f"accept the charge.", file=sys.stderr)
        sys.exit(4)

    confirm_ref = bh.find_ref(snap, SEL["cancel_confirm_button"], role="button")
    if not confirm_ref:
        print("Could not find 'Confirm cancellation'. Nothing cancelled.", file=sys.stderr)
        sys.exit(2)
    bh.click(confirm_ref)
    time.sleep(4)
    snap = bh.snapshot()
    if not re.search(SEL["canceled_marker"], snap.lower()):
        print("Clicked confirm but the page doesn't show 'Reservation canceled'. "
              "CHECK OpenTable manually.", file=sys.stderr)
        sys.exit(5)

    PENDING_FILE.unlink()
    print(edit_or_fallback(msg_id, f"✅ Cancelled: {pending['name']} ({pending['date']}). 🎩"))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    pp = sub.add_parser("propose")
    pp.add_argument("--name", required=True)
    pp.add_argument("--date", required=True, help="YYYY-MM-DD")
    sub.add_parser("confirm")
    args = ap.parse_args()

    if args.cmd == "list":
        do_list()
    elif args.cmd == "propose":
        do_propose(args.name, args.date)
    elif args.cmd == "confirm":
        do_confirm()


if __name__ == "__main__":
    try:
        main()
    except bh.BrowserError as e:
        print(f"browser: {e}", file=sys.stderr)
        sys.exit(5)
