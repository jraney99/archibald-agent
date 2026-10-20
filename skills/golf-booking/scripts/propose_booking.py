#!/usr/bin/env python3
"""Propose ONE tee time to the user with real ✅/❌ buttons. Nothing is booked here.

    python3 propose_booking.py --course "example creek" --date 2026-10-17 --time "3:30 PM" --players 2

Checks, in order: the course is approved AND on GolfNow; the slot is still on the tee
sheet at the quoted price; the price is within the course's ceiling. Then it writes the
pending file and sends the card. The user's ✅ runs confirm_booking.py through the
callback-dispatch plugin; ❌ discards the pending file.

Money: GolfNow's online rates are PREPAID - the card is charged when the reservation is
made, and the course policy text (Example Creek, 2026-10-11) says discounted times are
non-refundable and a no-show is charged in full. The card therefore says so, names the
card that will be charged, and gives the ceiling confirm_booking will refuse above.

The JSON this prints is a receipt. The card is already in the user's chat with its buttons;
do not restate it, and never type a 'tap ✅' of your own (rules/30, restaurant-booking).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SHARED = SCRIPT_DIR.parent.parent / "shared"
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SHARED))
import courses as course_list  # noqa: E402
import golfnow  # noqa: E402
from data_paths import path  # noqa: E402

ACTION_TYPE = "golfbooking"
PROPOSAL_SCRIPT = SHARED / "send_inline_proposal.py"


def pending_file() -> Path:
    return path("pending_golf_booking.json")


def proposal_text(course: dict, slot: dict, players: int, cfg: dict, pending: dict) -> str:
    when = slot["time"].strftime("%A %b %-d at %-I:%M %p")
    card = cfg.get("payment", {}).get("expected_card", {}).get("name", "your card on file")
    golfers = f"{players} golfer{'s' if players != 1 else ''}"
    lines = [
        "⛳ Master Wayne - book a tee time?",
        f"  {course['name']}",
        f"  {when} · {golfers} · {slot['holes']} holes{', cart included' if slot['cart'] else ''}",
        f"  ${slot['price']:.2f}/golfer, about ${pending['estimated_total']:.0f} total with GolfNow's fee and tax",
    ]
    if pending.get("deal_line"):
        lines.append(f"  {pending['deal_line']}")
    # Every GolfNow online rate is collected by GolfNow at booking, and the course policy
    # text calls discounted (hot deal) times non-refundable too (Example Creek, 2026-10-11).
    lines.append(f"  Charged to {card} when booked. Non-refundable; a no-show is charged in full.")
    if not cfg.get("checkout", {}).get("mapped"):
        lines.append("  First GolfNow booking: I will stop at the checkout page and send you a screenshot before anything is charged.")
    lines.append("")
    lines.append("Tap ✅ and I will book it as you on GolfNow.")
    return "\n".join(lines)


def send_proposal(text: str) -> int:
    """Send the card; return its Telegram message id (or None)."""
    r = subprocess.run([
        "python3", str(PROPOSAL_SCRIPT),
        "--text", text, "--action-type", ACTION_TYPE,
        "--confirm-label", "✅ Book it", "--skip-label", "❌ Skip",
    ], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"proposal send failed: {(r.stderr or r.stdout).strip()[:300]}")
    try:
        return json.loads(r.stdout).get("msg_id")
    except Exception:  # noqa: BLE001
        return None


def build(cfg: dict, course_name: str, day: datetime, time_text: str, players: int, note: str,
          fetch=None, deal_line: str = "") -> tuple:
    """(pending dict, text) or raises LookupError/ValueError with the sentence for the user.
    `fetch` is resolved at call time so a patched golfnow.fetch is honoured."""
    fetch = fetch or golfnow.fetch
    course = course_list.find(cfg, course_name)
    if course is None:
        raise LookupError(f"{course_name!r} is not on your approved course list, so I will not book it. "
                          f"Add it to the list first if you want it.")
    if isinstance(course, list):
        raise LookupError(f"{course_name!r} could be " + " or ".join(c["short"] for c in course) + ". Which one?")
    if not course_list.bookable(course):
        raise LookupError(course_list.not_bookable_reason(course))

    want = golfnow.norm_time(time_text)
    if not want:
        raise ValueError(f"--time {time_text!r} is not a clock time like '3:30 PM'.")
    d = course_list.defaults(cfg, course)
    home = cfg.get("home", {})
    data = fetch(course["facility_id"], day.date(), players, home.get("lat", 32.78), home.get("lon", -96.80))
    found = golfnow.slots(data)
    slot = next((s for s in found if s["time_label"] == want), None)
    if slot is None:
        nearby = ", ".join(s["time_label"] for s in found[:12]) or "none"
        raise LookupError(f"{course['short']} has no {want} tee time for {players} on "
                          f"{day.strftime('%b %-d')}. Open: {nearby}.")
    if slot["restricted"]:
        raise LookupError(f"GolfNow marks the {want} slot as reservation-restricted; book it with the course.")
    ceiling = d.get("max_price_per_golfer")
    if ceiling is not None and slot["price"] > ceiling:
        raise LookupError(f"{course['short']} {want} is ${slot['price']:.2f}/golfer, over your "
                          f"${ceiling:.0f} ceiling for this course. Say so if you want it anyway.")

    est = golfnow.estimate_total(slot["price"], players, d.get("fee_allowance_per_golfer", 10))
    pending = {
        "course_id": course["id"], "course": course["name"], "short": course["short"],
        "facility_id": course["facility_id"], "address": course.get("address", ""),
        "rate_id": slot["rate_id"], "detail_url": slot["detail_url"],
        "date": day.strftime("%Y-%m-%d"), "time": want, "target_dt": slot["time"].isoformat(),
        "players": players, "holes": slot["holes"], "cart": slot["cart"],
        "price_per_golfer": slot["price"], "rate_name": slot["rate_name"], "prepaid": slot["prepaid"],
        "estimated_total": est, "max_total": est,
        "expected_card": cfg.get("payment", {}).get("expected_card", {}),
        "note": note, "deal_line": deal_line,
        "proposed_at": datetime.now().isoformat(timespec="seconds"),
    }
    return pending, proposal_text(course, slot, players, cfg, pending)


def propose(pending: dict, text: str, send=None) -> dict:
    """Write the pending file and send the card. Shared by main() and deal_watch.py so the
    watcher cannot grow a second, slightly different way of proposing. Raises RuntimeError
    with the user's sentence if a proposal is already waiting or the send fails."""
    send = send or send_proposal
    pf = pending_file()
    if pf.exists():
        old = json.loads(pf.read_text())
        raise RuntimeError(f"A tee-time proposal is already waiting ({old.get('short')} {old.get('date')} "
                           f"{old.get('time')}). Answer or skip that one first.")
    pf.parent.mkdir(parents=True, exist_ok=True)
    pf.write_text(json.dumps(pending, indent=2))
    try:
        msg_id = send(text)
    except RuntimeError:
        pf.unlink(missing_ok=True)
        raise
    if msg_id:
        from pending_proposal import save_message_id
        save_message_id(pf, msg_id)
    return {"status": "proposal_sent", "course": pending["course"], "date": pending["date"],
            "time": pending["time"], "players": pending["players"],
            "price_per_golfer": pending["price_per_golfer"], "estimated_total": pending["estimated_total"],
            "already_sent_to_telegram": True,
            "say_nothing_further": "The card is in the user's chat with its real buttons. Do not restate or re-offer it."}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--course", required=True)
    p.add_argument("--date", required=True, help="YYYY-MM-DD")
    p.add_argument("--time", required=True, help='e.g. "3:30 PM"')
    p.add_argument("--players", type=int, default=None)
    p.add_argument("--note", default="")
    a = p.parse_args(argv)
    cfg = course_list.load()
    players = a.players or cfg.get("defaults", {}).get("players", 1)
    if not 1 <= players <= 4:
        print("--players must be 1 to 4.", file=sys.stderr)
        return 2
    try:
        day = datetime.strptime(a.date, "%Y-%m-%d")
    except ValueError:
        print(f"--date {a.date!r} must be YYYY-MM-DD.", file=sys.stderr)
        return 2
    try:
        pending, text = build(cfg, a.course, day, a.time, players, a.note)
    except (LookupError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except golfnow.ProviderError as exc:
        print(str(exc), file=sys.stderr)
        return 4

    try:
        receipt = propose(pending, text)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 5 if "already waiting" in str(exc) else 1
    print(json.dumps(receipt))
    return 0


if __name__ == "__main__":
    sys.exit(main())
