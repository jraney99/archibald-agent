#!/usr/bin/env python3
"""
Confirm a pending OpenTable reservation. Runs when the user taps ✅
(confirm:booking) on a propose_booking.py proposal. The user's tap IS the
explicit human confirmation required before an external/irreversible
action (SECURITY.md) - this does not pause for a second confirmation
before clicking the final "Complete reservation".

Hard safety rules (priority order):
  1. Must be logged in, else abort.
  2. The restaurant page must still show the approved slot (restaurant +
     date + time + party). Any mismatch -> abort, book nothing.
  3. On the reservation-details page:
       - "charge now" language (deposit / prepay / ticketed / amount due)
         -> ALWAYS abort with a screenshot.
       - a credit-card-required hold with a no-show fee -> abort UNLESS
         the proposal disclosed it (pending card_required_ack == true).
  4. Tick any agreement the form will not submit without ("I agree to the
     restaurant's terms and conditions") - and only those. The user's tap
     approved the terms the proposal disclosed; a marketing opt-in is
     never ticked for him.
  5. Screenshot the details page before clicking Complete.
  6. Verify-after-write: the confirmation screen must say "Reservation
     confirmed" before this reports success. If instead OpenTable renders
     its own "Cannot complete reservation" refusal, that is a definite NO,
     reported as such - not an "it may or may not have gone through".
On success: add to Google Calendar (with the confirmation number) and
delete the pending file.

Flow mapped live 2026-08-26 against Restaurant A and extended
2026-10-06 against Restaurant B, whose required terms
checkbox Restaurant A does not have. Selectors live in
config/restaurant_booking_config.json -> "selectors".
"""
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))
import browser_helpers as bh  # noqa: E402
sys.path.insert(0, str(SCRIPT_DIR.parent.parent / "shared"))
from pending_proposal import edit_or_fallback  # noqa: E402
sys.path.insert(0, str(SCRIPT_DIR))
from propose_booking import seating_phrase  # noqa: E402

CFG = json.loads((SCRIPT_DIR.parent / "config" / "restaurant_booking_config.json").read_text())
SEL = CFG["selectors"]
SAFETY = CFG["safety"]
SEATING = CFG.get("seating", {})
PENDING_FILE = Path("~/.openclaw/pending_restaurant_booking.json")
SCREENSHOT_PATH = str(SCRIPT_DIR.parent / "last_checkout.png")
ADD_CAL = "~/.openclaw/workspace/Skills/shared/add_calendar_event.py"


def abort(msg: str, code: int = 2):
    print(msg, file=sys.stderr)
    sys.exit(code)


GOG = "/opt/homebrew/bin/gog"
GCAL_ACCOUNT = "owner@example.com"


def _event_location(pending: dict) -> str:
    """What goes in the calendar event's location field.

    WHY THE ADDRESS AND NOT THE NAME (2026-10-08): this wrote `pending["name"]`, so
    The user's Restaurant B event read "location: Restaurant B". Waze reads his calendar and
    offers a time-to-leave ping with live traffic — far better than any estimate made
    days ahead — but only for an event it can actually route to. A bare name is not
    routable, and the same restaurant name can exist in more than one city.

    Falls back to the name when the lookup cannot confirm the place. A bare name makes
    him look it up; a confidently wrong address drives him across town.
    """
    # WHEN HE IS PICKING SOMEONE UP, THE PICKUP IS THE FIRST DRIVE (2026-10-08). Waze
    # pings him to leave in time to ARRIVE at the event's location, so pointing it at
    # the restaurant would time the wrong leg and get him to the pickup late. The caller
    # (outing-planner) sets pickup_address when that applies; every other booking is
    # unaffected and still points at the restaurant.
    pickup = (pending.get("pickup_address") or "").strip()
    if pickup:
        return pickup
    name = pending["name"]
    try:
        sys.path.insert(0, str(SCRIPT_DIR.parent.parent / "shared"))
        from drive_time import place_address
        city = CFG.get("calendar", {}).get("city", "Dallas, TX")
        found = place_address(name, city)
    except Exception:
        found = None
    return f"{name}, {found}" if found else name


def _dedupe_gmail_autoevent(name: str, dt: datetime) -> str:
    """Gmail auto-creates a generic "Reservation at <name>" event from the
    OpenTable confirmation email. If one exists for this date, delete it so
    it doesn't duplicate the proper event this script creates."""
    day = dt.strftime("%Y-%m-%d")
    r = subprocess.run(
        [GOG, "calendar", "list", "--account", GCAL_ACCOUNT, "primary",
         "--from", f"{day}T00:00:00", "--to", f"{day}T23:59:59", "-j"],
        capture_output=True, text=True)
    if r.returncode != 0:
        return ""
    try:
        events = json.loads(r.stdout).get("events", [])
    except Exception:
        return ""
    for ev in events:
        summ = (ev.get("summary") or "").lower()
        desc = (ev.get("description") or "")
        if summ == f"reservation at {name.lower()}" and "created from an email" in desc:
            subprocess.run(
                [GOG, "calendar", "delete", "--account", GCAL_ACCOUNT, "primary",
                 ev["id"], "--force"], capture_output=True, text=True)
            return " (removed Gmail's duplicate auto-event)"
    return ""


def meal_type(when: datetime) -> str:
    """Breakfast / Lunch / Brunch / Dinner by the hour, matching the email daemon's
    _meal_type exactly - the two must agree or their calendar titles won't."""
    hour = when.hour
    if hour < 11:
        return "Breakfast"
    if hour < 14:
        return "Lunch"
    if hour < 17:
        return "Brunch"
    return "Dinner"


def add_to_calendar(pending: dict, confirmation: str) -> str:
    dt = datetime.fromisoformat(pending["target_dt"])
    end = dt + timedelta(minutes=pending.get("duration_minutes", 90))
    # The block starts when he leaves for the pickup, not when the table is held - the
    # evening is already underway by the reservation time, and an event starting at the
    # booking would have Waze timing him to arrive as the starter lands.
    lead = int(pending.get("pickup_lead_minutes") or 0)
    start = dt - timedelta(minutes=lead) if pending.get("pickup_address") else dt
    desc = f"OpenTable reservation. Party of {pending['party']}."
    if lead and pending.get("pickup_address"):
        desc += f" Table at {dt.strftime('%-I:%M %p')}; pickup {lead} min before."
    if confirmation:
        desc += f" Confirmation #{confirmation}."
    if pending.get("note"):
        desc += f" Note: {pending['note']}."
    r = subprocess.run([
        "python3", ADD_CAL,
        "--who", meal_type(dt),
        "--venue", pending["name"],
        "--from", start.strftime("%Y-%m-%dT%H:%M:%S"),
        "--to", end.strftime("%Y-%m-%dT%H:%M:%S"),
        "--tz", CFG["calendar"]["tz"],
        "--location", _event_location(pending),
        "--description", desc,
        # On a pick-up the reminder is the drive to the pickup address, so the popup fires at the
        # leave time rather than at a fixed offset - airport-timing/pickup_time.py does
        # the same, and it makes the alert a property of the event instead of something
        # hoped for from Waze.
        "--reminder-minutes", str(pending.get("reminder_minutes")
                                  or CFG["calendar"]["reminder_minutes"]),
        # A SECOND, EARLIER PING (2026-10-09). The user: "should the agent alert me that I
        # need to be ready to leave at x time too?" The leave-at reminder assumes he is
        # already showered and dressed; at his desk when it fires, he is late. Only on
        # a booking with a pick-up, and only when the caller asked for it.
        *(["--extra-reminder-minutes", str(pending["get_ready_minutes"])]
          if pending.get("get_ready_minutes") else []),
    ], capture_output=True, text=True)
    if r.returncode != 0:
        return f"calendar add FAILED: {r.stderr.strip()[:200]}"
    return ("added" + _dedupe_gmail_autoevent(pending["name"], dt)
            + log_date_if_date_night(pending))


# RELATIVE, NOT ABSOLUTE (2026-10-09): this file inserted a hard-coded
# ~/.openclaw/... onto sys.path[0] at import time, so any test running
# against a copy of the tree silently got the LIVE modules from that point on. A
# sibling skill is found relative to this file, the way the rest of the imports here
# already do it.
DATE_CLI = str(SCRIPT_DIR.parent.parent / "outing-planner" / "scripts")


def log_date_if_date_night(pending: dict) -> str:
    """Record a confirmed outing against the outing history, if it came from outing-planner.

    WHY (2026-10-09): nothing closed this loop. outing_log.json was written by exactly
    one thing - the user typing `date_cli.py log` - and no code anywhere called it. So
    every stateful promise of the skill was inert: the outing history never advanced, the
    budget envelope never moved, the monthly cap never tripped, and
    due_date() always returned "today" because the history it reads was always empty.
    Re-running propose after a booking produced a second date in the same month.

    Keyed on companion so an ordinary restaurant booking is untouched.
    """
    if not pending.get("companion"):
        return ""
    try:
        sys.path.insert(0, DATE_CLI)
        import date_planner as dp
        when = datetime.fromisoformat(pending["target_dt"]).date()
        rows = dp.load(dp.LOG, {"dates": []})
        venue_id = pending.get("outing_venue_id") or dp.venue_id(pending["name"])
        # at-most-once: a re-confirmed booking must not double-count the envelope
        if any(r.get("venue_id") == venue_id and r.get("date") == when.isoformat()
               and r.get("partner") == pending["companion"]
               for r in rows.get("dates", [])):
            return " (already logged)"
        rows.setdefault("dates", []).append({
            "venue": pending["name"], "venue_id": venue_id,
            "partner": pending["companion"], "date": when.isoformat(),
            "month": when.strftime("%Y-%m"), "cost": None, "kind": "dinner",
            "logged_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": "confirm_booking",
        })
        dp.LOG.parent.mkdir(parents=True, exist_ok=True)
        dp.LOG.write_text(json.dumps(rows, indent=2))
        return f" (logged against {pending['companion']}'s outing history)"
    except Exception as exc:  # noqa: BLE001
        return f" (could not log the date: {str(exc)[:80]})"


def fee_reminder(pending: dict) -> str:
    """Repeat back the terms the user approved, in the restaurant's own words.

    Same amount and same cutoff the proposal disclosed, read from the pending file
    rather than restated from a template - the template said "< 1 day out" for every
    restaurant, which was Restaurant A's deadline, not Restaurant B's noon-that-day one.
    """
    money = bh.fee_text(pending.get("fee_per_person"))
    when = pending.get("fee_condition") or ""
    if money and when:
        return f"{money}/person if you no-show or cancel {when}."
    if money:
        return f"{money}/person for a no-show or late cancellation — check your email for the cutoff."
    return "a per-person no-show / late-cancel fee applies — check your email for the terms."


def main():
    if not PENDING_FILE.exists():
        abort("No pending restaurant booking.", 1)
    pending = json.loads(PENDING_FILE.read_text())
    msg_id = pending.get("source_tg_msg_id")
    target = datetime.fromisoformat(pending["target_dt"])
    dt_iso = target.strftime("%Y-%m-%dT%H:%M")
    want_time = bh.norm_time(pending["time"])
    if not want_time:
        abort(f"Pending file has an unparseable time {pending.get('time')!r}. "
              f"Nothing booked.", 1)
    restaurant = pending.get("url") or pending["slug"]

    # 1. Session --------------------------------------------------------
    bh.ensure_logged_in()

    # 2. Re-open the restaurant page and re-verify the slot ------------
    info = bh.read_slots(restaurant, dt_iso, pending["party"], slot_pattern=SEL["slot_button"])
    slot = next((s for s in info["slots"] if s["time"] == want_time), None)
    if not slot:
        abort(
            f"{pending['name']} no longer shows a {want_time} table for "
            f"{pending['party']} on {pending['date']} (someone likely took it). "
            f"Nothing booked. Open now: "
            + (", ".join(s["time"] for s in info["slots"]) or "none")
        )
    bh.click(slot["ref"])
    time.sleep(4)
    snap = bh.snapshot()

    # 2b. Optional "select a seating option" step ---------------------
    # Indoors unless that is not on offer. Until 2026-10-06 this took whichever option
    # OpenTable happened to list first, which put the user on Restaurant B's patio.
    seat = bh.pick_seating(
        bh.find_all_refs(snap, SEL["seating_option_button"], role="button"),
        SEATING.get("prefer", ()), SEATING.get("avoid", ()))
    seating_label = seat[1] if seat else ""
    if seat:
        bh.click(seat[0])
        time.sleep(4)
        snap = bh.snapshot()

    # We must now be on the reservation-details page.
    if not re.search(SEL["details_page_marker"], snap.lower()) \
       and not bh.find_ref(snap, SEL["complete_button"], role="button"):
        bh.screenshot(SCREENSHOT_PATH)
        abort(
            "Did not land on the reservation-details page after selecting the "
            f"slot (unexpected page). Screenshot: {SCREENSHOT_PATH}. Nothing "
            "booked.", 4
        )

    # 3. Charge / fee safety scan -------------------------------------
    charge_now = bh.scan_patterns(snap, SAFETY["charge_now_patterns"])
    if charge_now:
        bh.screenshot(SCREENSHOT_PATH)
        abort(
            f"STOPPED before booking {pending['name']}: the reservation page shows "
            f"charge-at-booking language ({', '.join(sorted(set(charge_now)))}). "
            f"Screenshot: {SCREENSHOT_PATH}. Book this by hand if the terms are OK.",
            4,
        )
    fee_hits = bh.scan_patterns(snap, SAFETY["no_show_fee_patterns"])
    is_card_req = bh.card_required(snap)
    if (fee_hits or is_card_req) and not pending.get("card_required_ack"):
        bh.screenshot(SCREENSHOT_PATH)
        abort(
            f"STOPPED: {pending['name']} requires a credit card / has a late-cancel "
            f"fee ({', '.join(sorted(set(fee_hits))) or 'card required'}) and the "
            f"proposal you approved didn't disclose it. Screenshot: {SCREENSHOT_PATH}. "
            f"Re-run propose_booking.py (it now reads the real terms) and approve "
            f"the new proposal.", 4
        )

    # 4. Optional special request, then screenshot the final state ----
    if pending.get("note"):
        req_ref = bh.find_ref(snap, SEL["special_request_textbox"], role="textbox")
        if req_ref:
            bh.fill([{"ref": req_ref, "value": pending["note"]}])
            snap = bh.snapshot()

    # 4b. Agree to the restaurant's own terms -------------------------
    # Restaurant A had no such box, so the flow mapped in August never learned to look for
    # one. Restaurant B does, and on 2026-10-06 the Complete click was simply refused:
    # "This checkbox is required". Ticking it is not a new decision - the user's tap
    # approved these exact terms, and the proposal disclosed them - but only a box the
    # reservation cannot proceed without is ever ticked for him, never a marketing
    # opt-in. One pass, then verify: clicking a box twice would untick it.
    todo = bh.required_agreements(snap, SEL["required_agreement_checkbox"],
                                  SEL["never_tick_checkbox"])
    for box in todo:
        if not box["ref"]:
            bh.screenshot(SCREENSHOT_PATH)
            abort(f"{pending['name']} requires agreeing to \"{box['label']}\", but that "
                  f"checkbox has nothing clickable in the page snapshot. Screenshot: "
                  f"{SCREENSHOT_PATH}. Nothing booked - book this one by hand, and "
                  f"re-map the flow with map_flow.py.", 4)
        bh.click(box["ref"])
    if todo:
        time.sleep(3)
        snap = bh.snapshot()
        still = bh.required_agreements(snap, SEL["required_agreement_checkbox"],
                                       SEL["never_tick_checkbox"])
        if still:
            bh.screenshot(SCREENSHOT_PATH)
            abort(f"Could not tick {pending['name']}'s required agreement "
                  f"({', '.join(b['label'] for b in still)}). Screenshot: "
                  f"{SCREENSHOT_PATH}. Nothing booked.", 4)

    bh.screenshot(SCREENSHOT_PATH)

    complete_ref = bh.find_ref(snap, SEL["complete_button"], role="button")
    if not complete_ref:
        abort(f"Could not find the 'Complete reservation' button. Screenshot: "
              f"{SCREENSHOT_PATH}. Nothing booked.", 4)
    bh.click(complete_ref)
    time.sleep(6)

    # 5. Verify-after-write ------------------------------------------
    # Two different outcomes hide behind "no confirmation screen". OpenTable says which
    # one it is, so read that before claiming not to know: when it renders "Cannot
    # complete reservation. Please fix the errors in the form." the answer is a plain
    # no. Sending the user to check OpenTable by hand over a refusal the page spelled out
    # is what happened on 2026-10-06.
    snap = bh.snapshot()
    if not re.search(SEL["confirmed_marker"], snap.lower()):
        bh.screenshot(SCREENSHOT_PATH)
        refused = bh.submit_refused(snap, SEL["submit_refused_marker"])
        if refused:
            reasons = [a for a in bh.alerts(snap) if a not in refused]
            abort(
                f"Nothing booked - OpenTable refused the form for {pending['name']}: "
                + "; ".join(refused + reasons)
                + f". No reservation exists, so nothing needs undoing. Screenshot: "
                  f"{SCREENSHOT_PATH}.", 4
            )
        abort(
            "Clicked 'Complete reservation' but the page does not show "
            f"'Reservation confirmed'. Screenshot: {SCREENSHOT_PATH}. CHECK "
            "OpenTable manually - it may or may not have gone through.", 5
        )
    confirmation = bh.confirmation_number(bh.current_url("/booking/confirmation"))

    cal_status = add_to_calendar(pending, confirmation)
    PENDING_FILE.unlink()
    result_text = (
        f"✅ Booked {pending['name']} - {target.strftime('%A %b %-d, %-I:%M %p')}, "
        f"party of {pending['party']}"
        + (f" (confirmation #{confirmation})" if confirmation else "")
        + (f", {seating_phrase(seating_label)}" if seating_label else "")
        + f". Calendar: {cal_status}."
        + (f"\n⚠️ Card on file; {fee_reminder(pending)}"
           if pending.get("card_required_ack") else "")
        + " 🎩"
    )
    print(edit_or_fallback(msg_id, result_text))


if __name__ == "__main__":
    try:
        main()
    except bh.BrowserError as e:
        print(f"Nothing booked - browser error: {e}", file=sys.stderr)
        sys.exit(5)
