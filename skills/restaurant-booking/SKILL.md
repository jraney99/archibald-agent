---
name: restaurant-booking
description: "Search OpenTable availability, book a table as the user, and cancel a reservation — each booking/cancel gated behind an explicit ✅. Use when the user asks to find, book, or cancel a restaurant reservation."
metadata:
  openclaw:
    emoji: "🍽️"
    requires:
      bins: ["python3", "openclaw", "gog"]
---

# Restaurant Booking 🍽️

Finds OpenTable availability and books / cancels a table **as the user**, driving
his logged-in session in the OpenClaw browser (CDP-attached persistent
Chrome — same mechanism as `playbypoint-golf`, chosen because OpenTable has
no public reservation API).

**Confirm-first, always.** Search is free. Booking and cancelling never
happen until the user taps ✅ on a proposal naming the exact restaurant, date,
time and party size (and disclosing any credit-card / late-cancel fee).

## Status: LIVE (booking + cancel verified end-to-end 2026-08-26)

Flow mapped and tested against a Dallas restaurant ("Restaurant A" below): booked
a real reservation, then cancelled it, both through the scripts. The user is
signed into OpenTable in the OpenClaw browser profile (persists across
window closes).

Selectors live in `config/restaurant_booking_config.json` → `selectors`.
Re-map with `scripts/map_flow.py <step>` if OpenTable changes its UI.

## Plain-English Telegram Routing

- **"Book Restaurant A Friday at 7 for 2"** / "Get us a table at Lucia Saturday night" →
  1. `python3 scripts/search_availability.py --name "Restaurant A" --date 2026-08-28 --time "7:00 PM" --party 2 --json`
     — exit 3 = ambiguous (show candidates, ask which); 4 = no match; 5 = browser/session error.
  2. On success with a slot at/near the requested time, pass the `restaurant.slug`
     **and** `restaurant.url` straight from the search JSON:
     `python3 scripts/propose_booking.py --slug <slug> --url "<url>" --name "<name>" --date <d> --time "<t>" --party <n> [--note "<occasion>"]`
     — this clicks through to read the real card/fee terms, then sends the ✅/❌ proposal.
       - exit 2 = couldn't reach the details page (slot likely gone).
       - exit 3 = money taken at booking (deposit/prepay/ticketed) → tell the user, do **not** propose.
  3. Do **not** book. The user's ✅ tap does that (→ RULES.md → `confirm_booking.py`).
  - Defaults: metro from config, party 2. Ask one compact follow-up only if date/time is genuinely missing.

- **"Any tables at Carbone this weekend?"** → `search_availability.py` only. Report slots, don't propose.

- **"Cancel my Restaurant A reservation"** →
  1. `python3 scripts/cancel_booking.py list` — read-only, shows upcoming reservations.
  2. `python3 scripts/cancel_booking.py propose --name "Restaurant A" --date 2026-08-28` — sends the ✅/❌ cancel proposal.
  3. The user's ✅ (`confirm:cancelbooking`) → `cancel_booking.py confirm`. It refuses if the cancel dialog warns of a charge (unless `fee_ack` is set in the pending file).

- **✅ / ❌ taps** → handled via RULES.md:
  `confirm:booking`→`confirm_booking.py`, `skip:booking`→delete pending file,
  `confirm:cancelbooking`→`cancel_booking.py confirm`, `skip:cancelbooking`→delete pending file.

## Files

| Path | Role |
|---|---|
| `scripts/browser_helpers.py` | OpenTable browser driving: session detect, search, slot read, booking-terms probe, charge scan, reservations dropdown parse |
| `scripts/search_availability.py` | Read-only availability check (the "find" half) |
| `scripts/propose_booking.py` | Reads real card/fee terms → pending file → Telegram ✅/❌ proposal |
| `scripts/confirm_booking.py` | On ✅: safety checks → book → verify → calendar (+ dedupe Gmail's auto-event) |
| `scripts/cancel_booking.py` | `list` / `propose` / `confirm` — cancel gated the same way |
| `scripts/map_flow.py` | Re-map the wizard if OpenTable's UI changes |
| `scripts/test_parsing.py` | Offline parser tests vs the `flowmap/` captures — run after any selector/parsing edit |
| `config/restaurant_booking_config.json` | Metro, defaults, calendar, charge-abort patterns, mapped selectors |
| `~/.openclaw/pending_restaurant_booking.json` | Transient: the proposed booking |
| `~/.openclaw/pending_restaurant_cancel.json` | Transient: the proposed cancellation |

## Safety model

- **Never automates the OpenTable login** — the user signs in by hand once;
  `ensure_logged_in()` only detects a live session and fails loudly if it's gone.
- **`propose_booking.py` reads the real terms** by clicking through to the
  reservation-details page (the restaurant page does NOT show "credit card
  required" — only the booking flow does), then backs out without booking.
  OpenTable doesn't hold the table until "Complete reservation".
- **Charge-at-booking language** (deposit / prepay / ticketed / amount due)
  → never proposed, and `confirm_booking.py` hard-aborts with a screenshot.
- **Card-required + late-cancel fee** → allowed only when the proposal
  disclosed it (`card_required_ack`); the proposal shows the $/person **and the
  restaurant's own cancellation cutoff**, both read off the live page. Neither is
  templated: Restaurant A charges $25/person "less than 1 day in advance", Restaurant B
  $35.00/person "after 12:00 pm on the day of the reservation". If the page states
  only one of the two, the proposal says so rather than filling in the other.
- **Seating is chosen, not taken.** `config.seating` is an ordered `prefer` list with
  an `avoid` list; the user's default is indoors, and an avoided option is used only when
  it is the one on offer. Before 2026-10-06 `find_ref(nth=0)` took whichever option
  OpenTable listed first, which put him on Restaurant B's patio. `propose_booking.py`
  previews the same seating it will book and names the room in the proposal.
- **One booking, one message.** The calendar event is titled `{Meal} <> the user @ {name}`
  — the house format from `rules/17`, and the exact shape the email daemon's duplicate
  guard compares against. Titling it `Dinner - {name} <> the user @ {name}` (as this did
  until 2026-10-06) is invisible to that guard, so the OpenTable confirmation *email*
  produced a second "🍽 Reservation detected" proposal for a table the agent had just
  booked itself. `meal_type()` deliberately mirrors the daemon's `_meal_type`
  thresholds; they have to agree or the titles diverge again.
- **Required agreements are ticked, marketing opt-ins never are.**
  `confirm_booking.py` ticks a box the form will not submit without ("I agree to the
  restaurant's terms and conditions" — `required_agreement_checkbox`) because the user's ✅
  already approved those disclosed terms. `never_tick_checkbox` keeps "Sign me up to
  receive dining offers" and the loyalty-points box out of that, and a box it cannot
  tick aborts **before** the Complete click, not after.
- **Slot re-verification** before booking; **verify-after-write** ("Reservation
  confirmed" / "Reservation canceled") before reporting success. When OpenTable
  instead renders its own refusal ("Cannot complete reservation. Please fix the errors
  in the form.") that is reported as a definite *nothing booked*, with the page's
  reason — not as "it may or may not have gone through".
- **Cancel fee guard** — `cancel_booking.py confirm` scans only the cancel
  *dialog* (not page boilerplate) and refuses if it warns of a charge.
- **Calendar** — creates a proper `Dinner - {name} <> the user @ {name}` event
  with a 60-min reminder and the confirmation #, and deletes Gmail's generic
  "Reservation at {name}" auto-event so there's no duplicate.

## The checkout differs per restaurant

The flow was mapped against Restaurant A in August 2026 and every selector was fitted to
that one checkout. Two things turned out to vary, and both broke a live Restaurant B
booking on 2026-10-06:

- **A required terms checkbox.** Restaurant A has none; Restaurant B will not submit without
  one. It renders as a visually-hidden `<input>` inside a styled label, so its snapshot
  line carries **no `[ref=]` of its own** — `find_ref()` cannot see it at all. The
  clickable ref belongs to the `generic` wrapping it, which is what `bh.checkboxes()`
  resolves.
- **Fee formatting.** Restaurant A wrote "$25 per person", Restaurant B "$35.00 per person".
  The original cents-free regex matched the first and silently missed the second, so
  the proposal told the user "a fee" where it meant "$35/person".

`scripts/flowmap/details_agreement_refused.txt` is the verbatim Restaurant B capture
(diner details redacted) that `test_parsing.py` asserts against; it runs in the daily
suite. Assume the next unfamiliar restaurant varies again — re-map with `map_flow.py`
and add its capture rather than loosening a selector.

## Known limitations

- OpenTable geolocates the search page from the browser profile (The user's is
  pinned near Hometown, CA), not `config.metro`. Literal names
  resolve fine; a chain ("Sixty Vines") or ambiguous name ("Lucia") can miss
  or resolve to the wrong city. Fix: drive the location picker / set the
  browser location to Dallas-Fort Worth.
- **`openclaw browser navigate` stops waiting after 20s and that is not adjustable.**
  The timeout is hardcoded in the CLI (`dist/browser-cli-actions-input` passes
  `timeoutMs: 2e4`, which takes precedence over the `--timeout` flag), while
  opentable.com's home page regularly takes longer. `browser_helpers.navigate()`
  therefore treats a transport timeout as "stopped watching", not "failed": it lets the
  load finish on the browser's clock and then asks `tabs` where the browser actually
  is. Nothing is re-sent, so a half-finished navigation is never duplicated. Before
  2026-10-06 that timeout aborted a whole booking on its first step.
- **OpenTable throttles bursts.** ~15+ opentable.com navigations in an hour
  (as happened during this skill's build/test) gets the session temporarily
  served `ERR_HTTP2_PROTOCOL_ERROR` on every opentable.com page for a while
  (other sites still load). Normal use — a booking every few days — never
  hits this. Don't loop `search_availability.py`. `propose_booking.py`
  already costs ~2x navigations (it probes the real terms); use
  `--skip-terms-check` only if you already know the restaurant's policy.

## Reservations booked outside this skill

The user sometimes books on OpenTable directly (web/app). Two Gmail filters on
`from:@opentable.com` strip INBOX, so the email daemon's main loop never sees
the confirmation. `email-monitor/daemon.py` → `_reservations_label_catchup()`
(added 2026-08-28) scans `from:opentable.com newer_than:2d` every poll cycle
and proposes a house-style calendar event for any confirmation not already
handled. Google's own "events from Gmail" also auto-creates a bare
`Reservation at <name>` event (`eventType: fromGmail`, which is
**API-read-only** — you can't PATCH its title/reminder, only delete+recreate).

## Later

- Resy (separate account; cleaner reverse-engineered API; worth it only for
  hard-to-get midnight-drop reservations). See conversation 2026-08-26.
- Modify (vs cancel+rebook), hotels, tickets — separate.
