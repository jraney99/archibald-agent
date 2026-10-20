---
name: flight-deal-tracker
description: Automatically watches flight prices for known upcoming trips and a short list of recurring destinations, and alerts on Telegram when a fare clears a set price threshold. No booking automation -- alert only.
metadata:
  openclaw:
    emoji: "✈️"
    requires:
      bins: ["flight-search", "python3", "gog"]
---

# Flight Deal Tracker ✈️

Watches flight fares in the background so the user doesn't have to check himself, and pings Telegram when a fare is worth jumping on. Built on the existing [`flight-search`](../flight-search/SKILL.md) CLI (Google Flights scrape, no API key -- Southwest has no public API, but its fares now show up on Google Flights).

Two things run daily on cron, both silent unless there's something to say:
- `check_deals.py` -- samples opportunistic routes + watches known trips, logs every price, alerts on a deal.
- `detect_trips_from_calendar.py` -- scans the calendar for events that look like trips to a watched city, proposes tracking them (never auto-starts).

This skill is **read-only / alert-only**. It never books, holds, or purchases anything -- the user always books manually once alerted.

## Plain-English Telegram Routing

- "Track flights for my trip to Austin the week of Oct 12" / "Watch flights to NYC Nov 3-7" →
  ```bash
  python3 scripts/manage_trips.py --add --label "Austin trip" --origin DAL --dest AUS \
      --depart 2026-10-12 --return 2026-10-19 [--target-price 65]
  ```
  Infer obvious defaults (home airport is DAL); ask one compact follow-up only if dates or destination are genuinely ambiguous.

- "What flights am I tracking?" / "Any deals?" →
  ```bash
  python3 scripts/check_deals.py --report
  ```
  Read-only, prints current best-known price per route/trip without touching alert state.

- "Stop tracking flights to Austin" / "Never mind on that LA trip" →
  ```bash
  python3 scripts/manage_trips.py --remove <trip_id>
  ```
  Get `<trip_id>` from `--list` if not already known.

- The user confirms or declines a calendar-detected trip (see below) →
  ```bash
  python3 scripts/manage_trips.py --confirm <trip_id>   # start tracking
  python3 scripts/manage_trips.py --remove <trip_id>    # skip it
  ```

- "Lower the NYC round-trip threshold to $290" / "Only alert me on LA flights under $220" →
  ```bash
  python3 scripts/manage_trips.py --set-threshold nyc 290
  ```
  Opportunistic-route thresholds are **round-trip** dollars (see below).

## How It Works

### Nonstop only
**A fare with a stop is never quoted, logged or alerted on, however cheap.** the user doesn't want connecting itineraries proposed to him (2026-09-26: a run pitched six DAL⇄LGB round trips at 1-2 stops). `cheapest()` filters to `stops == 0` before picking a price, so a connection can't win on price and never reaches `price_history.json` either -- thresholds retune against fares he'd actually book. If a sampled date/airport has no nonstop at all, that sample is simply skipped (`[skip] no nonstop fare …` in a non-`--quiet` run); the scrape still counts as successful quote coverage.

Escape hatch per route or per trip in `watchlist.json`: `"allow_connections": true`. Nothing sets it today.

Because Google returns its own best-first ordering and the script slices the top N, `SEARCH_RESULT_LIMIT` is 25 (was 5) so the nonstop option is actually in the slice. Same number of scrapes -- no runtime cost.

**Placeholder rows.** `fast_flights` sometimes emits an empty row -- airline `Unknown`, blank times, `price: 0`, `stop_info: "Unknown"` -- and `flight-search` coerces its unparseable stop count to `0`. It therefore reads as a **$0 nonstop** and wins every price comparison. `is_quote()` requires a positive fare and `is_nonstop()` treats an unknown stop count as *not* nonstop. Found 2026-09-26 by the dry run that verified the nonstop change: the deeper 25-row slice pulled these rows in and all 13 "deals" it would have sent were `$0 · Unknown · nonstop`. The 13 zero-price rows it logged were purged from `price_history.json` (they would have dragged the retune floor to $0).

Side effect to expect: **fewer and slightly pricier alerts.** LGB/OAK/BUR-type secondary airports often have no DAL nonstop, so they'll go quiet; the seeded thresholds were tuned on mixed history, so the next `retune_thresholds.py` runs will drift them upward toward true nonstop levels (capped at 35%/run).

### Opportunistic routes (`config/watchlist.json:opportunistic_routes`)
Seeded routes: LA (Southern California), New York, San Francisco, Austin -- all from DAL. No real dates exist, so `check_deals.py` spot-checks a **grid of getaway departures**: the next `SAMPLE_WEEKS` (3) **Thursdays, Fridays and Saturdays** in a ~75-day window (`SAMPLE_WEEKDAYS` / `SAMPLE_WEEKS` at the top of the script), each priced as a **round trip** at every length in the route's `return_offset_days` -- an `int` or a `list`; NYC/SF 3, Austin 2, **LA `[4, 7, 14]`** (LA trips run anywhere from a long weekend to a couple weeks). Every configured destination airport is checked. Southwest RT fare barely scales with trip length, so one `max_price_roundtrip` covers all lengths. Alerts the first time a round-trip fare clears the route's `max_price_roundtrip`, deduped per airport/departure/length so a cheaper shape still fires; never repeats an alert for a price that hasn't improved.

It's a **sampler, not exhaustive** -- an alert means a deal exists around then; silence doesn't rule out deals on days/lengths it didn't check. Legacy pre-2026-08-29 one-way rows in `price_history.json` are ignored once a route has round-trip data. Every active route must define `max_price_roundtrip`; a missing threshold fails loudly instead of silently deriving one from obsolete one-way configuration.

**Threshold retuning.** The seed thresholds (LA $320 / NYC $360 / SF $330 / Austin $200, set 2026-08-29) were each ~10-15% below a single first sample -- rough. `retune_thresholds.py` recomputes each as the **25th percentile** of the last 45 days of round-trip observations ("meaningfully cheaper than typical"), guardrailed: needs ≥10 distinct days of data, never moves >35% in one run, never drops below the cheapest fare actually seen. `--apply` writes; bare run just proposes via Telegram; **silent when nothing changes**. Cron `flight-threshold-retune-monthly` (`0 7 1 * *` CT) runs it `--apply` monthly -- first meaningful run ~2026-10-01. Manual override any time: `manage_trips.py --set-threshold <route_id> <round_trip_$>`. Edit `return_offset_days` / the `SAMPLE_*` constants directly.

### Known trips (`config/watchlist.json:trips`, `status: "watching"`)
Checked on their exact dates every day, priced as a **round trip** when the trip has a `return_date` (one-way if `return_date` is null). If a `target_price` is set, alerts when the fare is at or below it (including the first check). If not, the first observation is recorded silently as the baseline and it then alerts only when the fare drops to a **new low** below that -- the user already knows today's price when he adds a trip, so there's nothing to announce until it actually improves. Use `check_deals.py --report` to see the current best any time.

### Calendar-detected candidates (`status: "candidate"`)
`detect_trips_from_calendar.py` scans calendar events against the watched cities over a 180-day window. Every match is a **guess** -- it sends one confirm ping and does nothing further until the user confirms or declines.

Each evaluated event gets a verdict in `state/calendar_seen_events.json`. Only **`candidate_sent`** (a real ping went out -- kept even after the user declines, so it never re-pings) and **`past`** (the event's own date has passed) are permanent. Every other verdict (`no_match`, `return_leg_unpaired`, `covered_by_flight`) is **provisional** -- dropped and re-derived every run, because it depends on the current watchlist config and the rest of the calendar. This is what lets adding an airport to a route (e.g. `LGA`/`EWR` to `nyc`) rescue a flight that was filed `no_match` under the old config. Regression from freezing them: 2026-08-29, a real `DAL→LGA` flight stayed invisible and a dinner reservation whose street address read "…, New York, NY" got pitched as the trip. Tests: `test_detect_trips_travel.py`.

Booked-flight events (title `"{AIRLINE} {NUM} {ORIGIN} → {DEST} <> the user @ {Venue}"`) are parsed for their explicit airport codes to tell outbound from return -- not keyword+location guessing, which can't tell direction and misreads a return leg's own airport-city name as a fresh match (fixed 2026-08-26, see `feedback-flight-leg-direction-bug` memory). Outbound/return legs of the same route pair into one candidate with both dates. A leg dated today or earlier is never proposed -- by the time the cron runs there's no window left to catch a price drop and rebook.

Non-flight events (e.g. a plain "Weekend in LA" entry) fall back to keyword+home-metro-exclusion matching, but a non-flight event that lands **inside a window already covered by a booked flight** for the same route (a `watching` trip, or a flight-leg candidate from the same run) is marked `covered_by_flight` and never proposed separately -- it's just part of that trip.

### Price history & dedup
- `config/price_history.json` -- every observed price, opportunistic or trip, pruned past 180 days.
- `state/deal_alert_state.json` -- last alerted price per route/date or trip, so the same deal never fires twice.
- `workspace/state/monitoring.sqlite` -- quote coverage/source health and the durable notification outbox. A price enters alert-dedup state only after confirmed delivery; failed and ambiguous sends remain retryable.

## Alert Format

Opportunistic routes: **ONE message per run**, one line per route:
```
FLIGHT DEALS
LA  $267 LGB · Oct 24 → 31 (cheapest seen)
New York  $235 LGA · Oct 24 → 27
San Francisco  $287 SFO · Oct 24 → 27 (cheapest seen)
Austin  $157 AUS · Oct 22 → 24 (cheapest seen)
```

**Why so spare** (two rounds of the user's feedback, 2026-09-27/28). It replaced
one-message-per-fare, which sent **19 messages for 26 qualifying fares**: a Southwest
round-trip barely changes price with trip length, so one fare re-fired once per return
date and again per airport. The first digest kept a line per airport plus `typical $463`,
`16 other dates`, percentages and the card note -- he called that "too busy". All of it is
deliberately gone; only the cheapest airport per route survives. `(cheapest seen)` marks a
route at its observed low (he rejected a ⭐ for it), and **a route with no marker is the
signal that it is priced normally**. Ask him before adding anything back to this message.

`deliver_digest()` re-sends only when `digest_signature()` -- a hash of the rendered text,
not of the underlying fares -- changes, so an unchanged picture is silent rather than
repeating yesterday's text ([[feedback-notification-digestibility]]). State lives in the
single `digest:opportunistic` key in `deal_alert_state.json`. Full detail for a route is
available on request; it is not pushed.

### Airline and airport preferences (priced, never enforced)
The user flies **Southwest out of Love Field**, but told me on 2026-09-28 it "should not
disqualify a genuinely great deal". Both preferences are therefore expressed as a price,
not a filter:

- **Carrier.** `cheapest()` returns the Southwest fare unless another airline beats it by
  more than `PREFERRED_AIRLINE_MARGIN` ($75 -- roughly two checked bags plus free changes).
  A $100 gap still wins. Last 3 days of history: 627 Southwest fares, 19 Delta, so this
  bites rarely but was silently picking Delta before.
- **Airport.** `check_alternate_origin()` prices **DFW on the best dates Love Field already
  found** -- about one scrape per route -- and mentions it only when it beats the DAL fare
  by more than `ALT_ORIGIN_GROUND_COST` ($120, his ~$60-each-way Uber). The digest line
  reads `   DFW $250 American · saves $30 after Ubers`.

**Why DFW is a targeted check, not a second sweep:** measured 2026-09-28, the daily run
already takes **330-353s against a 420s cron timeout**. Pricing the full grid from both
airports would roughly double that and blow the limit. DFW rows are logged as
`kind: "alt_origin"` so `route_baseline()` keeps measuring Love Field against Love Field.

Known trips still alert individually -- one trip, one message, no flood -- and keep their
`(target: $X)` / `(new low since watching)` note. `format_alert()` is still their renderer.

**The third line is deal context, not the threshold** (approved 2026-09-26). A bare
`(threshold: $350)` can't tell a steal from an ordinary fare sitting $1 under the line --
on 2026-09-26 an LA fare at $297 (cheaper than 96% of nonstops seen) and one at $371
(cheaper than 70%) would have produced identical-looking alerts. `deal_context()` compares
the fare to **nonstop round-trip fares for the same route over the last 45 days** -- the same
universe that can trigger an alert -- and reports its percentile plus the median. Under
`MIN_CONTEXT_SAMPLES` (20) observations it says nothing and the old `(threshold: $X)` line is
used instead, rather than quoting a percentage of noise. Route label is shortened at `" ("`
so "LA (Southern California)" reads "LA". Known trips keep their own `(target: $X)` /
`(new low since watching)` note -- a single trip's history is too thin for a percentile.

Still open (deliberately not built): using that percentile as a **second trigger condition**
-- alert only when the fare is both under the ceiling AND in the cheap tail. Needs a few
weeks of clean nonstop history before a tail means anything; revisit ~mid-Nov 2026. The
ceiling must stay, or a permanently expensive market would get reported as a deal.
Known trip with a `return_date` prices round-trip the same way; a one-way trip (`return_date: null`) keeps the `DAL → LAX` / single-date form.

`· nonstop` is now always the stop line -- connections are filtered out before an alert is built (see **Nonstop only**).

Card note reuses the routing already established in `flight-search/SKILL.md` -- Card A is the standing recommendation for flight purchases. Refer to it by name, never by last-4.

## Cron Jobs

- `flight-trip-detect-daily` -- `45 6 * * *` America/Chicago, runs `detect_trips_from_calendar.py`.
- `flight-deal-check-daily` -- `50 6 * * *` America/Chicago, runs `check_deals.py` (timeout 420s -- the widened Thu/Fri/Sat grid is ~80s/run).
- `flight-threshold-retune-monthly` -- `0 7 1 * *` America/Chicago, runs `retune_thresholds.py --apply`.

All `command`-payload, `delivery.mode: "none"` (the scripts send their own Telegram messages via `../shared/send_telegram.py`) -- same convention as the nutrition/hydration silent-watchdog jobs. `delivery.to` is always the explicit chat id, never `"last"`. All three are **silent on a quiet run** -- no "checked, nothing found" message, ever.

## Failure Handling

If `flight-search` returns no result for a query, the script retries once, then records partial/unavailable quote coverage and skips that query. A full-run failure surfaces as a normal cron failure. Corrupt JSON state is preserved and raised rather than replaced with an empty default. Reports show recent future-trip samples instead of presenting an unlimited historical low as current.
