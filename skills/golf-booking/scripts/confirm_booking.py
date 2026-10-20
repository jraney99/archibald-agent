#!/usr/bin/env python3
"""Book the pending tee time on GolfNow. Runs when the user taps ✅ (confirm:golfbooking).

His tap is the human confirmation for an external, paid, non-refundable action. This
script then earns the right to click by re-checking everything, in order:

  1. Pending file exists and names one slot.
  2. GolfNow is signed in in the OpenClaw browser (never automated; abort if not).
  3. The tee-time page still shows the SAME course, date, time and per-golfer price the
     proposal quoted. Any difference -> abort, book nothing.
  4. The golfer count is set and the page's own Total is at or under the ceiling the
     proposal carried (green fees + fee allowance). Over -> abort with a screenshot.
  5. Checkout: the card shown is the one in config (Card A, last-4 0000).
     A different card -> abort. Tee Time Protection declined. No marketing box ticked.
  6. Until config.checkout.mapped is true (or with --dry-run), STOP at the checkout page:
     screenshot, save the accessibility capture to flowmap/ for mapping, charge nothing,
     tell the user. The ClawHub notes describe this page but it has not been seen in this
     browser, and a paid, non-refundable click is not something to attempt on notes.
  7. Click reserve; verify-after-write (a confirmation marker on the page) before
     reporting success. Then: record the booking (data/tee_time_bookings.json), add the
     calendar event (45-minute arrival buffer via --golf), edit the proposal in place.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SHARED = SCRIPT_DIR.parent.parent / "shared"
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SHARED))
import browser_helpers as bh  # noqa: E402
import courses as course_list  # noqa: E402
from data_paths import path  # noqa: E402
from pending_proposal import edit_or_fallback, load_message_id  # noqa: E402

SCREENSHOT = SCRIPT_DIR.parent / "state" / "last_checkout.png"
FLOWMAP = SCRIPT_DIR / "flowmap"
ADD_CAL = SHARED / "add_calendar_event.py"
ROUND_MINUTES = {9: 135, 18: 270}


def abort(msg: str, code: int = 2):
    print(msg, file=sys.stderr)
    sys.exit(code)


# ----------------------------------------------------------------------------------
# Pure checks (tested offline)
# ----------------------------------------------------------------------------------

def page_matches(pending: dict, page: dict) -> list:
    """Every way the live page disagrees with what the user approved. Empty = safe."""
    problems = []
    if page["date"] and page["date"] != pending["date"]:
        problems.append(f"date on page {page['date']} vs approved {pending['date']}")
    if page["time"] and page["time"] != pending["time"]:
        problems.append(f"time on page {page['time']} vs approved {pending['time']}")
    if page["price"] is not None and abs(page["price"] - float(pending["price_per_golfer"])) > 0.005:
        problems.append(f"price on page ${page['price']:.2f} vs approved ${pending['price_per_golfer']:.2f}")
    if not (page["date"] and page["time"] and page["price"] is not None):
        problems.append("page did not show a date, time and price (slot gone or layout changed)")
    want = (pending.get("short") or pending["course"]).lower().split(" - ")[0].split()[0]
    if page["course"] and want not in page["course"].lower():
        problems.append(f"course on page {page['course']!r} vs approved {pending['course']!r}")
    return problems


def total_ok(total, pending: dict) -> bool:
    return total is not None and total <= float(pending["max_total"]) + 0.005


def card_ok(last4s: list, expected: dict) -> bool:
    """True only when the checkout shows exactly the expected card and no other."""
    want = str(expected.get("last4", "")).strip()
    return bool(want) and last4s == [want]


def record_booking(pending: dict, confirmation: str, total) -> Path:
    p = path("tee_time_bookings.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(p.read_text()) if p.exists() else {"bookings": []}
    data["bookings"].append({
        "booked_at": datetime.now().isoformat(timespec="seconds"),
        "course_id": pending["course_id"], "course": pending["course"],
        "date": pending["date"], "time": pending["time"], "players": pending["players"],
        "holes": pending["holes"], "price_per_golfer": pending["price_per_golfer"],
        "total_charged": total, "card": pending.get("expected_card", {}).get("name"),
        "provider": "golfnow", "confirmation": confirmation, "rate_id": pending["rate_id"],
        "source": "confirm_booking",
    })
    p.write_text(json.dumps(data, indent=2))
    return p


def add_to_calendar(pending: dict, confirmation: str) -> str:
    start = datetime.fromisoformat(pending["target_dt"])
    end = start + timedelta(minutes=ROUND_MINUTES.get(int(pending.get("holes") or 18), 270))
    desc = (f"GolfNow tee time, {pending['players']} golfer(s), {pending['holes']} holes, "
            f"${pending['price_per_golfer']:.2f}/golfer prepaid.")
    if confirmation:
        desc += f" Confirmation {confirmation}."
    base = ["python3", str(ADD_CAL), "--who", "Golf", "--venue", pending["short"],
            "--title", f"Golf @ {pending['short']}",
            "--from", start.strftime("%Y-%m-%dT%H:%M:%S"), "--to", end.strftime("%Y-%m-%dT%H:%M:%S"),
            "--tz", "America/Chicago", "--location", pending.get("address") or pending["course"],
            "--description", desc, "--golf"]
    try:
        from drive_time import origin_coords_and_label
        origin = origin_coords_and_label()[2]
    except Exception:  # noqa: BLE001
        origin = ""
    attempts = []
    if origin and origin != "current location":
        attempts.append(base + ["--origin-address", origin])
    attempts.append(base + ["--reminder-minutes", "90"])
    last = ""
    for cmd in attempts:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if r.returncode == 0:
            return "added (45 min before the tee time plus the drive)" if "--origin-address" in cmd else "added (90 min reminder)"
        last = (r.stderr or r.stdout).strip()[:200]
    return f"calendar add FAILED: {last}"


def save_capture(name: str, snap: str) -> Path:
    FLOWMAP.mkdir(parents=True, exist_ok=True)
    p = FLOWMAP / f"{name}_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.txt"
    p.write_text(snap)
    return p


# ----------------------------------------------------------------------------------
# The flow
# ----------------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true",
                    help="stop at the checkout page with a screenshot; charge nothing (also the default while checkout.mapped is false)")
    a = ap.parse_args(argv)

    cfg = course_list.load()
    sel = cfg.get("checkout", {}).get("selectors", {})
    pf = path("pending_golf_booking.json")
    if not pf.exists():
        abort("No pending tee-time proposal.", 1)
    pending = json.loads(pf.read_text())
    msg_id = load_message_id(pf)
    dry = a.dry_run or not cfg.get("checkout", {}).get("mapped", False)

    tab = bh.open_tab(pending["detail_url"])
    try:
        snap = bh.snapshot()
        if bh.logged_out(snap, sel.get("logged_out_marker", r"^log in$")) \
                or not bh.logged_in(snap, sel.get("logged_in_marker", "/my/profile")):
            abort("GolfNow is not signed in in my browser, so nothing was booked. Sign into golfnow.com "
                  "once in the OpenClaw browser window, then tap ✅ on the proposal again.", 3)

        page = bh.read_tee_time_page(snap, sel)
        problems = page_matches(pending, page)
        if problems:
            bh.screenshot(str(SCREENSHOT))
            abort(f"Nothing booked. The {pending['short']} page no longer matches what you approved: "
                  + "; ".join(problems) + ". Ask me to search again.", 4)

        want = str(pending["players"])
        if page["golfers_selected"] != pending["players"]:
            ref = page["golfer_refs"].get(want)
            if not ref:
                bh.screenshot(str(SCREENSHOT))
                abort(f"Nothing booked. I could not select {want} golfers on the page (only "
                      f"{', '.join(sorted(page['golfer_refs'])) or 'no'} option(s) offered).", 4)
            bh.click(ref)
            time.sleep(3)
            snap = bh.snapshot()
            page = bh.read_tee_time_page(snap, sel)
            if page["golfers_selected"] != pending["players"]:
                bh.screenshot(str(SCREENSHOT))
                abort(f"Nothing booked. The page did not switch to {want} golfers.", 4)

        if not total_ok(page["total"], pending):
            bh.screenshot(str(SCREENSHOT))
            shown = f"${page['total']:.2f}" if page["total"] is not None else "no total"
            abort(f"Nothing booked. GolfNow shows {shown} for {want} golfer(s); the proposal's ceiling was "
                  f"${pending['max_total']:.2f}. Ask me to re-propose if that price is fine.", 4)
        total_before = page["total"]

        if not page["continue_ref"]:
            bh.screenshot(str(SCREENSHOT))
            abort("Nothing booked. No 'Continue to Book' button on the page.", 4)
        bh.click(page["continue_ref"])
        time.sleep(bh.SETTLE_SECONDS)
        snap = bh.snapshot()
        if bh.logged_out(snap, sel.get("logged_out_marker", r"^log in$")) or re.search(r"/login|golfid", bh.current_url(), re.I):
            abort("GolfNow asked for a login at checkout, so nothing was booked. Sign in once in the "
                  "OpenClaw browser window and tap ✅ again.", 3)

        # ---- checkout -----------------------------------------------------------
        shot = bh.screenshot(str(SCREENSHOT))
        last4s = bh.card_last4(snap, sel.get("card_marker", r"(ending in|\*{2,}|••|x{2,})\s*(\d{4})"))
        total = bh.money_after(snap, sel.get("total_label", r"^total$")) or total_before
        if dry:
            cap = save_capture("checkout", snap)
            text = (f"Stopped at GolfNow's checkout for {pending['short']} {pending['date']} {pending['time']} "
                    f"({want} golfer(s)) - nothing charged. This first checkout needs a look before I "
                    f"click anything paid. Card shown: {', '.join(last4s) or 'not readable'}; total: "
                    f"{('$%.2f' % total) if total else 'not readable'}. Screenshot and page capture saved.")
            print(json.dumps({"status": "stopped_at_checkout", "screenshot": shot, "capture": str(cap),
                              "cards_seen": last4s, "total": total,
                              "note": "pending file kept; set checkout.mapped after the walkthrough"}), file=sys.stderr)
            print(edit_or_fallback(msg_id, text + " 🎩"))
            return 0

        if not card_ok(last4s, pending.get("expected_card", {})):
            abort(f"Nothing booked. Checkout shows card(s) ending {', '.join(last4s) or '(none readable)'}, "
                  f"not your {pending.get('expected_card', {}).get('name', 'approved card')} "
                  f"(…{pending.get('expected_card', {}).get('last4', '????')}). Fix the card on GolfNow, then tap ✅ again.", 4)
        if not total_ok(total, pending):
            abort(f"Nothing booked. Checkout total ${total:.2f} is over the ${pending['max_total']:.2f} ceiling.", 4)
        if cfg["checkout"].get("decline_tee_time_protection", True):
            ref = bh.find_ref(snap, sel.get("protection_decline", "no,? thanks|decline"))
            if ref:
                bh.click(ref)
                time.sleep(2)
                snap = bh.snapshot()
        agree = bh.find_ref(snap, sel.get("agree_terms_checkbox", "i agree"), role="checkbox")
        if agree and not re.search(sel.get("never_tick_checkbox", "sign me up"), agree, re.I):
            bh.click(agree)
            time.sleep(1)
            snap = bh.snapshot()
        reserve = bh.find_ref(snap, sel.get("reserve_button", "make your reservation"), role="button")
        if not reserve:
            bh.screenshot(str(SCREENSHOT))
            abort("Nothing booked. Could not find the reservation button on the checkout page.", 4)
        bh.screenshot(str(SCREENSHOT))
        bh.click(reserve)
        time.sleep(bh.SETTLE_SECONDS)
        snap = bh.snapshot()
        if not re.search(sel.get("confirmed_marker", "confirmed"), snap, re.I):
            bh.screenshot(str(SCREENSHOT))
            save_capture("after_reserve_unconfirmed", snap)
            abort("Clicked reserve but GolfNow did not show a confirmation. CHECK GolfNow's My Reservations "
                  "by hand - it may or may not have gone through. Screenshot saved.", 5)
        m = re.search(r"confirmation (?:number|#)?[:\s]*([A-Z0-9-]{5,})", snap, re.I)
        confirmation = m.group(1) if m else ""
    finally:
        bh.close_tab(tab)

    record_booking(pending, confirmation, total)
    cal = add_to_calendar(pending, confirmation)
    pf.unlink(missing_ok=True)
    when = datetime.fromisoformat(pending["target_dt"]).strftime("%A %b %-d, %-I:%M %p")
    print(edit_or_fallback(msg_id,
          f"✅ Booked {pending['short']} - {when}, {want} golfer(s)"
          + (f", ${total:.2f} on {pending.get('expected_card', {}).get('name', 'your card')}" if total else "")
          + (f" (confirmation {confirmation})" if confirmation else "")
          + f". Calendar: {cal}. 🎩"))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except bh.BrowserError as e:
        print(f"Nothing booked - browser error: {e}", file=sys.stderr)
        sys.exit(5)
