#!/usr/bin/env python3
"""
Offline unit tests for the restaurant-booking parsers, run against the
accessibility-tree captures in scripts/flowmap/ (from the 2026-08-26
mapping session) plus a small synthetic dropdown fixture.

    python3 scripts/test_parsing.py

No browser, no network. Run after editing any parsing/selector logic.
"""
import json
import re
import sys
from pathlib import Path
from datetime import datetime
from urllib.parse import parse_qs, urlparse

SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))
import browser_helpers as bh  # noqa: E402
from cancel_booking import _match_block  # noqa: E402
from confirm_booking import meal_type  # noqa: E402
from propose_booking import fee_disclosure, seating_phrase  # noqa: E402
from search_availability import search_term  # noqa: E402

FLOW = SCRIPT_DIR / "flowmap"
CFG = json.loads((SCRIPT_DIR.parent / "config" / "restaurant_booking_config.json").read_text())
SEL = CFG["selectors"]

DROPDOWN_FIXTURE = '''\
              - button "Upcoming reservations dropdown" [ref=e46] [cursor=pointer]:
                - generic [ref=e3431]: Upcoming reservations
                      - link "Restaurant A" [active] [ref=e3439] [cursor=pointer]:
                        - /url: https://www.opentable.com/r/restaurant-a-dallas
                        - text: 5:30 PM
                        - text: Aug 28, 2026
                          - link "View" [ref=e3455] [cursor=pointer]:
                          - link "Modify" [ref=e3456] [cursor=pointer]:
                        - link "Cancel" [ref=e3459] [cursor=pointer]:
                      - link "Example Trattoria" [ref=e3466] [cursor=pointer]:
                        - /url: https://www.opentable.com/r/example-trattoria
                        - text: 8:00 PM
                        - text: Sep 2, 2026
                        - link "Cancel" [ref=e3486] [cursor=pointer]:
                  - link "View all reservations" [ref=e3487] [cursor=pointer]:
                - link "A photo of Example Bistro" [ref=e142] [cursor=pointer]:
                  - /url: https://www.opentable.com/r/example-bistro
'''

n = 0


def check(cond, label):
    global n
    n += 1
    if not cond:
        print(f"FAIL: {label}")
        sys.exit(1)
    print(f"ok  {label}")


def main():
    # --- configured metro is encoded in the actual OpenTable search context ---
    query = parse_qs(urlparse(bh.search_url(
        "Lucia Dallas", "2026-09-20T19:00", 2, CFG["metro_id"])).query)
    check(query["metroid"] == [str(CFG["metro_id"])],
          "search URL pins the configured OpenTable metro")
    check(search_term("Lucia", CFG["metro"], CFG["metro_id"]) == "Lucia",
          "metro-pinned search keeps the restaurant term unambiguous")
    check(search_term("Lucia", CFG["metro"], None) == f"Lucia {CFG['metro']}",
          "unmapped search retains the metro hint in the term")

    # --- norm_time ---
    for s, exp in [("5:30 PM", "5:30 PM"), ("5:30PM", "5:30 PM"),
                   ("at 5:30 pm today", "5:30 PM"), ("nope", ""),
                   ("Reserve table at Restaurant A at 9:45 PM on August 28, for a party of 2", "9:45 PM")]:
        check(bh.norm_time(s) == exp, f"norm_time({s!r}) == {exp!r}")

    # --- slot reading from the real restaurant-page capture ---
    slots_snap = (FLOW / "slots.txt").read_text()
    times = sorted({bh.norm_time(l) for _, l in
                    bh.find_all_refs(slots_snap, r"reserve table at", role="button")})
    check(times == ["5:00 PM", "5:15 PM", "5:30 PM", "5:45 PM", "9:45 PM"],
          f"slot times parsed: {times}")

    # --- card-required + fee from the details-page capture ---
    review = (FLOW / "review.txt").read_text()
    check(bh.card_required(review) is True, "card_required(review) is True")
    m = bh._FEE_RE.search(review)
    check(m and m.group(1) == "25", "fee_per_person parsed as 25")
    check(bool(re.search(SEL["details_page_marker"], review.lower())), "details_page_marker hits review")
    check(bool(bh.find_ref(review, SEL["complete_button"], role="button")), "complete_button found on review")
    check(bh.scan_patterns(review, CFG["safety"]["charge_now_patterns"]) == [],
          "no charge-now language on a plain card-hold page")

    # --- confirmation marker + number ---
    conf = (FLOW / "confirmation.txt").read_text()
    check(bool(re.search(SEL["confirmed_marker"], conf.lower())), "confirmed_marker hits confirmation")
    for u, exp in [("x?confirmationNumber=12345&y=1", "12345"),
                   ("x?confnumber=99999", "99999"), ("x?none", "")]:
        check(bh.confirmation_number(u) == exp, f"confirmation_number({u!r}) == {exp!r}")

    # --- dropdown parsing + cancel matching ---
    blocks = bh.reservation_blocks(DROPDOWN_FIXTURE)
    check(len(blocks) == 2, f"reservation_blocks -> 2 (got {len(blocks)}, no rec-carousel bleed)")
    check(blocks[0]["name"] == "Restaurant A" and blocks[0]["cancel_ref"] == "e3459", "Restaurant A block")
    check(_match_block(blocks, "Restaurant A", "2026-08-28")["cancel_ref"] == "e3459", "match Restaurant A on its date")
    check(_match_block(blocks, "Restaurant A", "2026-12-31") is None, "refuse Restaurant A on wrong date")
    check(_match_block(blocks, "Restaurant A", "2026-09-02") is None, "refuse Restaurant A on the other reservation's date")
    check(_match_block(blocks, "Nope", "2026-08-28") is None, "refuse unknown restaurant")

    # --- Restaurant B: the required-agreement checkbox (2026-10-06) ------------
    # Restaurant A had no such box, so the August flow never looked for one and a Restaurant
    # B booking died on a Complete click the form refused.
    barsa = (FLOW / "details_agreement_refused.txt").read_text()

    check(bh.find_ref(barsa, SEL["required_agreement_checkbox"], role="checkbox") is None,
          "find_ref still cannot see the agreement box (it carries no ref) - the bug")

    boxes = {c["label"]: c for c in bh.checkboxes(barsa)}
    agree = next(c for lbl, c in boxes.items() if "I agree" in lbl)
    check(agree["ref"] == "e149", f"agreement box clicks its wrapping generic (got {agree['ref']})")
    check(agree["checked"] is False, "agreement box reads as unticked")

    todo = bh.required_agreements(barsa, SEL["required_agreement_checkbox"],
                                  SEL["never_tick_checkbox"])
    check([c["label"] for c in todo] == ["I agree to the restaurant\u2019s terms and conditions"],
          f"only the required agreement is ticked for the user (got {[c['label'] for c in todo]})")
    labels = list(boxes)
    check(any("Sign me up" in l for l in labels) and any("Collect 100 points" in l for l in labels),
          "the marketing and loyalty boxes are present in the capture")
    check(all("Sign me up" not in c["label"] and "Collect" not in c["label"] for c in todo),
          "a marketing opt-in is never ticked on his behalf")

    ticked = barsa.replace('checkbox "I agree to the restaurant\u2019s terms and conditions"',
                           'checkbox "I agree to the restaurant\u2019s terms and conditions" [checked]')
    check(bh.required_agreements(ticked, SEL["required_agreement_checkbox"],
                                 SEL["never_tick_checkbox"]) == [],
          "once ticked there is nothing left to agree to")
    check(bh.required_agreements(review, SEL["required_agreement_checkbox"],
                                 SEL["never_tick_checkbox"]) == [],
          "a restaurant without an agreement box (Restaurant A) needs no extra step")

    # --- the page states plainly that it refused; don't call that unknown ------
    refused = bh.submit_refused(barsa, SEL["submit_refused_marker"])
    check(refused == ["Cannot complete reservation. Please fix the errors in the form."],
          f"submit_refused reads OpenTable's own refusal (got {refused})")
    check("This checkbox is required" in bh.alerts(barsa), "the specific reason is read too")
    check(bh.submit_refused(conf, SEL["submit_refused_marker"]) == [],
          "a real confirmation screen is not read as a refusal")
    check(not re.search(SEL["confirmed_marker"], barsa.lower()),
          "and the refused page never claims a confirmation")

    # --- fee amount: the cents that got lost ---------------------------------
    check(bh.fee_per_person(barsa) == 35.0, f"Restaurant B fee is $35 (got {bh.fee_per_person(barsa)})")
    check(bh.fee_per_person(review) == 25.0, "Restaurant A's $25 still parses")
    check(bh.fee_per_person('- paragraph: a charge of $12.50 per guest applies') == 12.5,
          "cents and 'per guest' both parse")
    check(bh.fee_per_person('- paragraph: the tasting menu is $185 per person') is None,
          "a menu price is not a penalty")
    check((bh.fee_text(35.0), bh.fee_text(12.5), bh.fee_text(None)) == ("$35", "$12.50", ""),
          "fee_text drops a pointless .00 and keeps real cents")
    check(bh.scan_patterns(barsa, CFG["safety"]["no_show_fee_patterns"]),
          "the config fee patterns now match a cents amount too")
    check(bh.scan_patterns(barsa, CFG["safety"]["charge_now_patterns"]) == [],
          "a card hold with a 2% service fee is not charge-at-booking")

    # --- fee cutoff: quote the restaurant, never Restaurant A's deadline ----------
    check(bh.fee_condition(barsa) == "after 12:00 pm on the day of the reservation",
          f"Restaurant B's real cutoff (got {bh.fee_condition(barsa)!r})")
    check(bh.fee_condition(review) == "less than 1 day in advance",
          f"Restaurant A's real cutoff (got {bh.fee_condition(review)!r})")
    check(bh.fee_condition("- paragraph: no fees here") == "", "silence when unstated")

    line = fee_disclosure(bh.fee_per_person(barsa), bh.fee_condition(barsa))
    check(line == "$35/person if you no-show or cancel after 12:00 pm on the day of the reservation.",
          f"the proposal discloses amount AND cutoff (got {line!r})")
    check("1 day" not in fee_disclosure(35.0, ""),
          "with no cutoff read, none is invented")
    check("$" not in fee_disclosure(None, "after noon"),
          "with no amount read, none is invented")

    # --- seating: indoors unless there is no indoors (2026-10-06) -----------
    SEAT = CFG["seating"]
    patio = ("e1", "Select outdoor seating in the Patio dining area")
    inside = ("e2", "Select standard seating in the Main Dining Room dining area")
    bar = ("e3", "Select bar seating in the Bar dining area")
    check(bh.pick_seating([patio, inside], SEAT["prefer"], SEAT["avoid"]) == inside,
          "the patio listed first no longer wins by default")
    check(bh.pick_seating([patio], SEAT["prefer"], SEAT["avoid"]) == patio,
          "a patio-only restaurant still books")
    check(bh.pick_seating([bar, inside], SEAT["prefer"], SEAT["avoid"]) == inside,
          "prefer order is honoured over listing order")
    check(bh.pick_seating([], SEAT["prefer"], SEAT["avoid"]) is None,
          "no options means no click (a single implicit seating shows none)")
    check(bh.find_ref((FLOW / "details.txt").read_text(), SEL["seating_option_button"],
                      role="button") == "e47",
          "the Restaurant A capture still matches the seating selector")
    check((seating_phrase(inside[1]), seating_phrase(patio[1]))
          == ("Indoors, Main Dining Room", "Outdoors, Patio"),
          "the proposal names the room in plain words")

    # --- calendar title must be the house format (rules/17) -----------------
    # "Dinner - Restaurant B <> the user @ Restaurant B" broke the convention AND defeated
    # the email daemon's duplicate guard, so one booking produced two messages.
    for hour, exp in [(9, "Breakfast"), (10, "Breakfast"), (11, "Lunch"), (13, "Lunch"),
                      (14, "Brunch"), (16, "Brunch"), (17, "Dinner"), (18, "Dinner")]:
        check(meal_type(datetime(2026, 10, 8, hour, 45)) == exp,
              f"{hour}:45 is {exp} (same thresholds as the email daemon)")
    title = f"{meal_type(datetime(2026, 10, 8, 18, 45))} <> the user @ Restaurant B"
    check(title == "Dinner <> the user @ Restaurant B", f"house calendar title (got {title!r})")

    # --- navigate: where we asked to go vs where we landed -------------------
    check(bh._canon("https://www.opentable.com/") == "https://www.opentable.com",
          "_canon normalises the home URL")
    check(bh._canon("https://www.opentable.com/r/restaurant-b-dallas?dateTime=x&covers=2")
          == "https://www.opentable.com/r/restaurant-b-dallas", "_canon drops the query")
    check(bh._canon("https://www.opentable.com/booking/details?x=1")
          != bh._canon("https://www.opentable.com/"),
          "a checkout page is not mistaken for the home page")

    # --- dialog subtree isolation ---
    check(bh.dialog_subtree("- generic:\n  - text: hi") == "", "dialog_subtree empty when no dialog")
    dlg = ('- dialog "Are you sure?" [ref=e1]:\n'
           '  - text: Fri, Aug 28 at 5:30 PM\n'
           '  - button "Confirm cancellation" [ref=e2]\n'
           '- paragraph: policy $25 per person boilerplate\n')
    sub = bh.dialog_subtree(dlg)
    check("Confirm cancellation" in sub and "boilerplate" not in sub,
          "dialog_subtree excludes trailing page boilerplate")

    print(f"\nALL {n} CHECKS PASS")


if __name__ == "__main__":
    main()
