---
name: golf-booking
description: "Find and book tee times for the user at the courses HE has approved (config/approved_courses.json), through GolfNow, each booking gated behind his ✅ tap. Use when he asks to find, book or check a tee time at a named course or 'somewhere Saturday morning'."
metadata:
  openclaw:
    emoji: "⛳"
    requires:
      bins: ["python3", "openclaw"]
---

# Golf Booking ⛳

The user, 2026-10-11: *"how do we have something that books tee times for me at approved
courses I select?"* then *"lets build it but change the cc on golf now."*

Two decisions are his and stay his: **which courses** (the approved list) and **whether
to book** (the ✅ tap on a card that names course, date, time, golfers, price and the
card that will be charged). Everything else is the script's.

## Status: BUILT 2026-10-11 — search LIVE, booking in DRY-RUN, watcher built but OFF

- Search works against GolfNow's tee sheet for the GolfNow courses (verified live for
  Example Creek West and Cedar Hills).
- `confirm_booking.py` drives the pre-login tee-time page (mapped live) and then
  **stops at the checkout page with a screenshot** because `checkout.mapped` is false.
  The checkout sits behind the user's login, which this skill never automates, so it has not
  been seen yet. Flipping `mapped` to true is a deliberate step after one supervised
  walkthrough, with the capture saved to `scripts/flowmap/`.
- 2026-10-12: The user created a GolfNow account and is **signed in** in the OpenClaw browser
  (header: `link "AB"` → /my/profile, "Hi, the user"; capture in `scripts/flowmap/`). The
  profile page shows no saved payment method, so GolfNow will ask for the card at the
  first checkout — the user enters the **Card A** himself during the supervised
  walkthrough. The scripts never enter card details; `confirm_booking.py` only checks the
  checkout shows …0000 and refuses to pay with anything else. (The ClawHub skill's
  "…9999" card was its author's, not the user's.)

## The approved list — `config/approved_courses.json`

| Course | Provider | Bookable here |
|---|---|---|
| Example Creek West (facility 1001) | GolfNow | yes |
| Example Creek East (1002) | GolfNow | yes |
| Cedar Hills GC (1003) | GolfNow | yes |
| Oak Park Highlands | CPS Golf (example.cps.golf) | no — give the link / the shop's phone |
| Maple Park One / Two | Purpose Golf | no — give the link / the shop's phone |

GolfNow answers HTTP 500 for the last three (their GolfNow pages redirect to a details
page), so a second adapter is needed before they can be booked. The scripts say exactly
that instead of "no tee times".

Per-course `defaults` override the skill defaults (players 1, 07:00–18:30, $90/golfer
ceiling, $10/golfer fee allowance). Edit the JSON; nothing else changes.

## Plain-English Telegram routing

- **"Any tee times Saturday morning?" / "What's open at Example Creek Friday for 2?"** →
  `python3 scripts/search_tee_times.py --date saturday [--course "example creek"] [--players 2] [--earliest 07:00 --latest 11:00]`
  Read-only. Output is plain text already shaped for Telegram; relay it. Exit 3 = a course
  he named is not on the list — say so, do not search elsewhere.
- **"Book Example Creek Saturday 8:10 for 2"** →
  `python3 scripts/propose_booking.py --course "example creek" --date 2026-10-17 --time "8:10 AM" --players 2`
  This re-checks the slot and price on the tee sheet, writes the pending file and **sends
  the ✅/❌ card itself**. The JSON it prints is a receipt: say nothing further, and never
  type a "tap ✅" of your own (a typed one carries no button). Exit 3 = refused, with
  the sentence to relay (not approved / not on GolfNow / slot gone / over ceiling).
- **✅ tap** (`confirm:golfbooking`) → `confirm_booking.py` via callback-dispatch
  (300 s timeout). **❌** (`skip:golfbooking`) → pending file discarded, card edited.
- **"Oak Park tomorrow"** → it is approved but not on GolfNow: relay the booking link and
  phone from the search output. Do not improvise a booking.
- **Cancel** → not built. GolfNow's prepaid online rates are non-refundable ("Discounted
  tee times are non-refundable and non-cancelable" — Example Creek policy text, 2026-10-11),
  so there is nothing to cancel through; if he needs to, GolfPass+ members may change
  up to an hour before through GolfNow's own My Reservations.

## What the ✅ card says

```
⛳ Master Wayne - book a tee time?
  Example Creek Golf Club - West Course
  Saturday Oct 17 at 3:30 PM · 2 golfers · 18 holes, cart included
  $60.95/golfer, about $142 total with GolfNow's fee and tax
  Charged to Card A when booked. Non-refundable; a no-show is charged in full.
  First GolfNow booking: I will stop at the checkout page and send you a screenshot before anything is charged.

Tap ✅ and I will book it as you on GolfNow.
```

The total is a ceiling, not a quote: green fees plus `fee_allowance_per_golfer` (GolfNow
added $2.99 + $5.28 tax to one $60.95 golfer). `confirm_booking.py` reads the page's own
Total and refuses above the ceiling.

## Safety model (what `confirm_booking.py` checks before it clicks anything)

1. **Signed in?** Never automated. `menuitem "Log in"` in the header → abort, ask him to
   sign in once in the OpenClaw browser window.
2. **Same slot?** The live page's date, time, per-golfer price and course must equal the
   pending file. Any difference → abort, book nothing, keep the pending file.
3. **Golfer count set and Total ≤ ceiling.** Read off the page after selecting the count.
4. **The card.** Checkout must show exactly …0000 (Card A) and no other card.
5. **Dry run** until `checkout.mapped` is true: screenshot + accessibility capture to
   `scripts/flowmap/`, nothing charged, card edited to say so.
6. **Verify-after-write**: a confirmation marker on the page before "Booked" is reported;
   otherwise "check My Reservations by hand", never a guess.
7. **Own tab.** Every browser step runs in a tab this skill opened (`openclaw browser
   open`) and closes, because the browser is shared and a snapshot of the wrong tab is
   how the Maple Park page got read for a Example Creek tee time during the build.

On success: `data/tee_time_bookings.json` gets the row, a `Golf @ <course>` calendar
event is created with `--golf` (45-minute arrival buffer plus the drive), and the
proposal message is edited in place.

## Files

| Path | Role |
|---|---|
| `config/approved_courses.json` | THE list. Courses, providers, facility ids, defaults, expected card, checkout selectors + `mapped` flag |
| `scripts/golfnow.py` | GolfNow tee-sheet API adapter (fetch, parse, time helpers) |
| `scripts/courses.py` | approved-list lookup: find / bookable / reason / defaults |
| `scripts/search_tee_times.py` | read-only search, Telegram text or JSON |
| `scripts/propose_booking.py` | re-check → pending file → ✅/❌ card (action type `golfbooking`) |
| `scripts/browser_helpers.py` | own-tab browser driving + snapshot parsers |
| `scripts/confirm_booking.py` | the ✅ handler: guards → (dry-run stop) → reserve → verify → record → calendar |
| `scripts/deal_watch.py` | the watcher: deal + window + calendar bandwidth → at most one card a day (crons disabled) |
| `state/deal_watch_state.json` | slots already proposed, cards sent per day |
| `scripts/test_golf_booking.py` | offline suite (in `test-all.mjs`): sandboxed, no network, no Telegram |
| `scripts/flowmap/` | real captures: tee-sheet API response, pre-login page; checkout capture lands here on the walkthrough |
| `data/tee_time_bookings.json` | every booking made (created on the first) |
| `state/pending_golf_booking.json` | the one proposal waiting for a tap |

## The deal watcher — `scripts/deal_watch.py` (BUILT 2026-10-12, NOT LIVE)

The user, 2026-10-12: *"Right now I can get out at Oak Park for free at dark so I am doing
that. The idea with this would be if it finds a really good deal and my calendar has
bandwidth, it would be to book it."*

What it does, twice a day when enabled: scans the next 7 days at the GolfNow courses on
the list; a slot qualifies when it is a **deal** (GolfNow Hot Deal, or at/under the
course's `deal_price`: Example Creek $40 vs the usual $61, Cedar Hills $70 vs $96), in the
**window** (weekend 07:00–11:00, weekday 15:00–18:00), with at least 3 hours' notice, and
the **calendar has bandwidth**: clear from an hour before the tee time to five hours
after on all three calendars, no multi-day all-day block (a trip), not away from home for
a slot within a day, and for today sunset-golf's dinner rule. The cheapest relative to the
usual price wins. It sends the ordinary ✅ card with a `Deal:` line, at most **one a day**,
never the same slot twice, never while a proposal is waiting. **It never books.**

Quiet failure: a calendar it cannot read is not bandwidth, and nothing qualifying prints
one JSON line to the cron log and sends nothing.

Live dry run 2026-10-12 09:50: five hot deals in the week, all rejected for real reasons
(Mon 3:54 PM $39 at Example Creek West — a colleague's meeting, swim, dinner; Tue/Wed/Sun outside
the windows; Fri 3:06 PM — a colleague's meeting). Would have sent nothing.

Gate (CLAUDE.md rollout gate): `watch.enabled` is **false** AND both crons are
**disabled** — `golf-deal-watch-am` (09:50 CT) and `golf-deal-watch-pm` (15:35 CT),
isolated session, delivery none, failure alert after 2 to Telegram. Both must be flipped,
after the user approves the exact card and trigger. `deal_watch.py --dry-run` shows what the
next run would send.

## Not built, on purpose, until the user says so

- **CPS Golf (Oak Park) and Purpose Golf (Maple Park) adapters.**
- **Cancellation / modification** through GolfNow.
