#!/usr/bin/env python3
"""
Daily flight-deal watcher.

Two things get checked, both against the existing `flight-search` CLI
(Google Flights scrape, no API key -- see ../../flight-search/SKILL.md):

1. Opportunistic routes (config/watchlist.json:opportunistic_routes) --
   destinations the user wants watched even with no trip planned (LA, NYC,
   SFO, Austin). No real dates exist, so it spot-
   checks a grid of realistic getaway departures -- the next SAMPLE_WEEKS
   Thursdays, Fridays and Saturdays -- each priced as a ROUND TRIP at every
   length in the route's `return_offset_days` (scalar or list; default a
   single 3-day long weekend). Alerts when a round-trip fare clears the
   route's `max_price_roundtrip` threshold. It is a sampler, not exhaustive:
   an alert means a deal exists around then; silence doesn't rule out deals
   on days/lengths it didn't check.

2. Known trips (config/watchlist.json:trips, status "watching") --
   searches the trip's actual dates (round-trip when it has a return_date)
   and alerts when the price is at/below target_price (if set) or a new low
   below the silently-recorded first-observation baseline (if not).

Only NONSTOP itineraries count. A connecting fare is never quoted, logged or
alerted on, however cheap -- the user doesn't want to be sent flights with stops.
A route or trip can opt out with "allow_connections": true in watchlist.json.

Opportunistic routes produce ONE "FLIGHT DEALS" message per run -- a block per
route, the best fare per airport on its own line -- not one message per fare.
It is re-sent only when those fares change; an unchanged picture stays silent.
Known trips still alert individually (one trip, one message, no flood).

Every observed price is appended to config/price_history.json regardless
of whether it triggers an alert. Alerts are deduped through
state/deal_alert_state.json so the same price never re-fires.

Usage:
    python3 check_deals.py              # normal cron run: sample, log, alert
    python3 check_deals.py --dry-run    # sample + log, print instead of sending Telegram
    python3 check_deals.py --report     # read-only: print current best known prices, no logging/alerting
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _state_lock import locked_state  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))
from monitoring import MonitoringStore  # noqa: E402

SKILL_DIR = Path(__file__).parent.parent
WATCHLIST_FILE = SKILL_DIR / "config" / "watchlist.json"
HISTORY_FILE = SKILL_DIR / "config" / "price_history.json"
ALERT_STATE_FILE = SKILL_DIR / "state" / "deal_alert_state.json"
SEND_TELEGRAM = SKILL_DIR.parent / "shared" / "send_telegram.py"
OUTBOX_DB = SKILL_DIR.parent.parent / "state" / "monitoring.sqlite"
OUTBOX_DOMAIN = "flight-deal-tracker"

# Opportunistic-route sampling grid. No real dates exist, so we spot-check a
# spread of realistic getaway departures rather than every day: the next
# SAMPLE_WEEKS occurrences of each weekday in SAMPLE_WEEKDAYS (Thu/Fri/Sat),
# inside the [+start_offset, +horizon] window. 3 weekdays x 3 weeks = up to 9
# departures per route. Each is priced at every length in the route's
# return_offset_days (scalar or list; default a single 3-day long weekend).
SAMPLE_WEEKDAYS = (3, 4, 5)          # Mon=0 … so Thu, Fri, Sat
SAMPLE_WEEKS = 3
SAMPLE_HORIZON_DAYS = 75
SAMPLE_START_OFFSET_DAYS = 7
INTER_SCRAPE_SLEEP_SEC = 0.5         # be gentle on the Google Flights scrape
# Google returns its own ordering (best-first), not cheapest-first, and we slice
# the top N -- so ask for a deep enough slice that the nonstop option is in it.
SEARCH_RESULT_LIMIT = 25
HISTORY_RETENTION_DAYS = 180
CARD_NOTE = "\U0001F4B3 Card A (3%)"

# Deal context on the alert line: where this fare sits among the nonstop fares
# recently seen for the same route. A bare threshold can't tell a steal from an
# ordinary fare that happens to sit $1 under the line.
CONTEXT_LOOKBACK_DAYS = 45
MIN_CONTEXT_SAMPLES = 20    # below this, say nothing rather than quote a percentage of noise
DIGEST_STATE_KEY = "digest:opportunistic"

# The user flies Southwest out of Love Field by preference, but said it "should not
# disqualify a genuinely great deal" (2026-09-28). Both preferences are therefore
# priced, never enforced:
#   - another carrier at DAL must beat the Southwest fare by PREFERRED_AIRLINE_MARGIN
#     (two checked bags' worth) before it is chosen;
#   - DFW must beat the Love Field fare by ALT_ORIGIN_GROUND_COST, his ~$60-each-way
#     Uber, before it is even mentioned.
PREFERRED_AIRLINE = "southwest"
PREFERRED_AIRLINE_MARGIN = 75
ALT_ORIGIN = "DFW"
ALT_ORIGIN_GROUND_COST = 120

# Per-route override: opportunistic_routes[].return_offset_days (int or [int,…]).
DEFAULT_RETURN_OFFSET_DAYS = 3


def nonstop_only(entry: dict) -> bool:
    """Connecting itineraries are not deals. Per-route/per-trip escape hatch:
    "allow_connections": true."""
    return not entry.get("allow_connections", False)


def route_return_offsets(route: dict) -> list[int]:
    raw = route.get("return_offset_days", DEFAULT_RETURN_OFFSET_DAYS)
    offsets = raw if isinstance(raw, (list, tuple)) else [raw]
    return [int(o) for o in offsets if int(o) > 0] or [DEFAULT_RETURN_OFFSET_DAYS]


def route_roundtrip_threshold(route: dict) -> int:
    """Round-trip alert threshold for a migrated opportunistic route."""
    return int(route["max_price_roundtrip"])


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def next_sample_departures(weekdays, weeks: int, horizon_days: int, start_offset_days: int) -> list[date]:
    """The next `weeks` occurrences of each weekday in `weekdays`, within the
    [today+start_offset, today+horizon] window. Sorted, de-duplicated."""
    today = date.today()
    start = today + timedelta(days=start_offset_days)
    end = today + timedelta(days=horizon_days)
    want = set(weekdays)
    counts = {wd: 0 for wd in want}
    picked = []
    d = start
    while d <= end and any(counts[wd] < weeks for wd in want):
        if d.weekday() in want and counts[d.weekday()] < weeks:
            picked.append(d)
            counts[d.weekday()] += 1
        d += timedelta(days=1)
    return sorted(picked)


def search_flight(origin: str, dest: str, depart: str, return_date: str = None) -> dict | None:
    cmd = ["flight-search", origin, dest, "--date", depart, "--output", "json",
           "--limit", str(SEARCH_RESULT_LIMIT)]
    if return_date:
        cmd += ["--return", return_date]
    for attempt in range(2):
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if result.returncode == 0 and result.stdout.strip():
                return json.loads(result.stdout)
        except Exception:
            pass
    return None


def is_quote(flight: dict) -> bool:
    """A row we can actually price. fast_flights emits a placeholder row --
    airline "Unknown", blank times, price 0, stop_info "Unknown" -- and
    flight-search coerces its unparseable stop count to 0, so it otherwise
    reads as a $0 nonstop and wins every price comparison (caught 2026-09-26)."""
    price = flight.get("price")
    return isinstance(price, (int, float)) and not isinstance(price, bool) and price > 0


def is_nonstop(flight: dict) -> bool:
    """Confirmed nonstop. An unknown stop count (stop_info set) is NOT a
    nonstop, however stops reads -- flight-search defaults it to 0."""
    return (flight.get("stops") or 0) == 0 and not flight.get("stop_info")


def is_preferred_airline(flight: dict) -> bool:
    return PREFERRED_AIRLINE in (flight.get("airline") or "").lower()


def cheapest(flights_json: dict, nonstop: bool = True) -> dict | None:
    """The itinerary the user would actually book, or None if the scrape gave us
    nothing real (and, with `nonstop`, nothing without a stop).

    Not simply the lowest fare: Southwest wins unless another carrier beats it by
    more than PREFERRED_AIRLINE_MARGIN, which is roughly what two checked bags and
    free changes are worth to him. A big enough gap still wins -- the preference
    prices the difference rather than hiding it."""
    flights = [f for f in (flights_json.get("flights") or []) if is_quote(f)]
    if nonstop:
        flights = [f for f in flights if is_nonstop(f)]
    if not flights:
        return None
    best = min(flights, key=lambda f: f["price"])
    preferred = [f for f in flights if is_preferred_airline(f)]
    if not preferred:
        return best
    best_preferred = min(preferred, key=lambda f: f["price"])
    if best_preferred["price"] - best["price"] <= PREFERRED_AIRLINE_MARGIN:
        return best_preferred
    return best


def route_baseline(entries: list[dict], route_id: str, today: str,
                   nonstop: bool = True) -> list[int]:
    """Sorted round-trip fares seen for this route in the lookback window -- the
    yardstick both the digest and the per-fare context line measure against.
    Nonstop-only by default, matching what can trigger an alert; a route with
    "allow_connections" has no nonstop history, so the digest retries without it."""
    cutoff = (date.fromisoformat(today) - timedelta(days=CONTEXT_LOOKBACK_DAYS)).isoformat()
    return sorted(e["price"] for e in entries
                  if e.get("kind") == "opportunistic" and e.get("route_id") == route_id
                  and e.get("trip_type") == "roundtrip"
                  and e.get("date_checked", "") >= cutoff
                  and isinstance(e.get("price"), (int, float)) and e["price"] > 0
                  and (not nonstop or (e.get("stops") or 0) == 0))


def standing(baseline: list[int], price: int) -> str:
    """"cheapest seen" / "cheaper than 96%" -- where this fare sits in the baseline."""
    if price <= baseline[0]:
        return "cheapest seen"
    return f"cheaper than {round(100.0 * sum(1 for x in baseline if x > price) / len(baseline))}%"


def short_label(label: str) -> str:
    return label.split(" (")[0]


def span(depart: str, return_date: str) -> str:
    """2026-10-24, 2026-10-31 -> "Oct 24 → 31"  (month repeated only when it changes)."""
    a, b = date.fromisoformat(depart), date.fromisoformat(return_date)
    tail = f"{b.day}" if a.month == b.month else f"{b.strftime('%b')} {b.day}"
    return f"{a.strftime('%b')} {a.day} → {tail}"


def check_alternate_origin(deals: list[dict], history_entries: list, today: str,
                          quiet: bool) -> int:
    """Price ALT_ORIGIN (DFW) on the best dates Love Field already found, and mark
    a deal when DFW wins by more than the ground cost of getting there.

    Deliberately NOT a second full sweep: the daily run already takes ~350s of its
    420s budget, so pricing the whole grid twice would blow the cron timeout. Only
    the dates the user would actually fly get checked -- about one scrape per route.

    DFW rows are logged as kind "alt_origin" so they stay out of `route_baseline`,
    which must keep measuring Love Field against Love Field."""
    checked = 0
    for route_id in dict.fromkeys(d["route_id"] for d in deals):
        rows = [d for d in deals if d["route_id"] == route_id]
        best = min(rows, key=lambda d: d["price"])
        time.sleep(INTER_SCRAPE_SLEEP_SEC)
        checked += 1
        result = search_flight(ALT_ORIGIN, best["dest"], best["depart"], best["return_date"])
        if not result:
            if not quiet:
                print(f"[warn] no {ALT_ORIGIN} result for {best['dest']} {best['depart']}")
            continue
        alt = cheapest(result)
        if not alt:
            continue
        history_entries.append({
            "date_checked": today, "kind": "alt_origin", "route_id": route_id,
            "origin": ALT_ORIGIN, "dest": best["dest"], "depart_date": best["depart"],
            "return_date": best["return_date"], "trip_type": "roundtrip",
            "price": alt["price"], "airline": alt.get("airline"), "stops": alt.get("stops"),
        })
        saving = best["price"] - (alt["price"] + ALT_ORIGIN_GROUND_COST)
        if saving > 0:
            best["alt"] = {"origin": ALT_ORIGIN, "price": alt["price"],
                           "airline": alt.get("airline") or "", "saving": saving}
    return checked


def format_digest(deals: list[dict], entries: list[dict], today: str) -> str | None:
    """ONE message for the whole run: a header and a single line per route --
    cheapest fare, its airport, its dates, and "(cheapest seen)" when nothing
    cheaper has been observed.

    Two rounds of the user's feedback shaped this (2026-09-27/28). It replaced
    one-message-per-fare, which sent 19 messages for 26 qualifying fares -- a
    Southwest round-trip barely changes price with trip length, so one fare
    re-fired once per return date and again per airport. He then called the
    per-airport digest "too busy": the alternate airports, `typical $463`,
    `16 other dates`, the percentages and the card note are all deliberately
    gone. A route with no marker is the signal that it is priced normally."""
    if not deals:
        return None
    lines = ["FLIGHT DEALS"]
    for route_id in dict.fromkeys(d["route_id"] for d in deals):
        rows = [d for d in deals if d["route_id"] == route_id]
        baseline = (route_baseline(entries, route_id, today)
                    or route_baseline(entries, route_id, today, nonstop=False))
        if not baseline:
            continue
        best = min(rows, key=lambda d: d["price"])
        mark = " (cheapest seen)" if best["price"] <= baseline[0] else ""
        lines.append(f"{short_label(best['label'])}  ${best['price']} {best['dest']} "
                     f"· {span(best['depart'], best['return_date'])}{mark}")
        alt = best.get("alt")
        if alt:
            airline = f" {alt['airline']}" if alt["airline"] else ""
            lines.append(f"   {alt['origin']} ${alt['price']}{airline} "
                         f"· saves ${alt['saving']} after Ubers")
    if len(lines) == 1:
        return None
    return "\n".join(lines)


def digest_signature(text: str) -> str:
    """The rendered message itself is the dedup key. Keying off the underlying
    fares instead would re-send a byte-identical message whenever a fare the
    digest doesn't show happened to move."""
    return hashlib.sha1(text.encode()).hexdigest()


def deal_context(entries: list[dict], *, route_id: str, label: str, price: int,
                 today: str) -> str | None:
    """"Cheaper than 96% of LA nonstops seen · typical $443", or None when there
    isn't enough comparable history to say that honestly.

    Compares against NONSTOP round-trip fares only -- the same universe that can
    trigger an alert -- over the last CONTEXT_LOOKBACK_DAYS."""
    prices = route_baseline(entries, route_id, today)
    if len(prices) < MIN_CONTEXT_SAMPLES:
        return None
    cheaper_than = round(100.0 * sum(1 for x in prices if x > price) / len(prices))
    typical = round(prices[len(prices) // 2])
    return (f"Cheaper than {cheaper_than}% of {short_label(label)} nonstops seen "
            f"· typical ${typical}")


def format_alert(label: str, origin: str, dest: str, depart: str, price: int, flight: dict,
                 threshold_note: str, return_date: str | None = None) -> str:
    airline = flight.get("airline", "")
    stops = flight.get("stops", 0)
    stop_str = "nonstop" if stops == 0 else f"{stops} stop{'s' if stops != 1 else ''}"
    if return_date:
        route_line = f"✈️ Deal: {origin} ⇄ {dest} ({label})"
        when = f"{depart} → {return_date} · round trip"
    else:
        route_line = f"✈️ Deal: {origin} → {dest} ({label})"
        when = depart
    return (
        f"{route_line}\n"
        f"${price} · {when} · {airline} · {stop_str}\n"
        f"{threshold_note} · {CARD_NOTE}"
    )


def send_telegram(text: str, dry_run: bool) -> dict | bool:
    if dry_run:
        print(f"--- would send Telegram ---\n{text}\n")
        return True
    result = subprocess.run(
        ["python3", str(SEND_TELEGRAM), "--text", text],
        capture_output=True, text=True, timeout=20,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip()[:300] or f"sender exited {result.returncode}")
    try:
        receipt = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("sender returned invalid JSON") from exc
    from monitoring import valid_receipt
    if not valid_receipt(receipt):
        raise RuntimeError("sender did not confirm delivery")
    return receipt


def deliver_alert(text: str, notice_key: str, dry_run: bool,
                  outbox: MonitoringStore | None = None) -> bool:
    """Deliver once and retain failures for retry.

    The alert dedup state is updated only after this function returns True.
    """
    if dry_run:
        send_telegram(text, True)
        return False
    if outbox is None:
        return send_telegram(text, False) is not False
    outbox.enqueue(notice_key, OUTBOX_DOMAIN, text, priority="immediate")
    return outbox.deliver(notice_key, lambda queued_text: send_telegram(queued_text, False))


def deliver_digest(deals: list[dict], entries: list[dict], alert_state: dict, today: str,
                   dry_run: bool, outbox: MonitoringStore | None = None) -> bool:
    """Send the run's single FLIGHT DEALS message, if it would say anything new.

    Silent when nothing clears a ceiling, and silent when the fares are the same
    ones the last digest already reported -- a daily message that repeats
    yesterday's text verbatim is the noise pattern the user flagged on 2026-07-31.
    """
    text = format_digest(deals, entries, today)
    if text is None:
        return False
    signature = digest_signature(text)
    if alert_state.get(DIGEST_STATE_KEY, {}).get("signature") == signature:
        return False
    notice_key = f"{OUTBOX_DOMAIN}:digest:{today}:{signature[:10]}"
    if deliver_alert(text, notice_key, dry_run, outbox):
        alert_state[DIGEST_STATE_KEY] = {"signature": signature, "last_sent_at": today}
        return True
    return False


def retry_pending(outbox: MonitoringStore, quiet: bool = False) -> None:
    for notice in outbox.pending(OUTBOX_DOMAIN):
        delivered = outbox.deliver(
            notice["notice_key"], lambda queued_text: send_telegram(queued_text, False))
        if not delivered and not quiet:
            print(f"[warn] alert delivery remains pending: {notice['notice_key']}")


def check_opportunistic_routes(watchlist: dict, history_entries: list, alert_state: dict,
                               today: str, dry_run: bool, quiet: bool,
                               outbox: MonitoringStore | None = None,
                               deals: list | None = None) -> dict:
    """Price the sample grid, log every nonstop fare, and COLLECT the ones that
    clear their route ceiling into `deals`. Sending is the caller's job -- the
    whole run produces one digest message (see format_digest)."""
    sample_dates = next_sample_departures(SAMPLE_WEEKDAYS, SAMPLE_WEEKS,
                                          SAMPLE_HORIZON_DAYS, SAMPLE_START_OFFSET_DAYS)
    first_scrape = True
    attempted = successful = 0
    for route in watchlist.get("opportunistic_routes", []):
        if not route.get("enabled", True):
            continue
        origin = route["origin"]
        threshold = route_roundtrip_threshold(route)
        destinations = route.get("destinations") or []
        for dest_index, dest in enumerate(destinations):
            for depart in sample_dates:
                depart_str = depart.isoformat()
                for offset in route_return_offsets(route):
                    return_str = (depart + timedelta(days=offset)).isoformat()
                    if not first_scrape:
                        time.sleep(INTER_SCRAPE_SLEEP_SEC)
                    first_scrape = False
                    attempted += 1
                    result = search_flight(origin, dest, depart_str, return_str)
                    if not result:
                        if not quiet:
                            print(f"[warn] no result for {origin}->{dest} {depart_str}..{return_str}")
                        continue
                    successful += 1
                    best = cheapest(result, nonstop_only(route))
                    if not best:
                        if not quiet:
                            print(f"[skip] no nonstop fare for {origin}->{dest} "
                                  f"{depart_str}..{return_str}")
                        continue
                    price = best["price"]
                    history_entries.append({
                        "date_checked": today, "kind": "opportunistic", "route_id": route["id"],
                        "origin": origin, "dest": dest, "depart_date": depart_str, "return_date": return_str,
                        "trip_type": "roundtrip",
                        "price": price, "airline": best.get("airline"), "stops": best.get("stops"),
                    })
                    if price > threshold:
                        continue
                    if deals is not None:
                        deals.append({
                            "route_id": route["id"], "label": route["label"], "dest": dest,
                            "depart": depart_str, "return_date": return_str, "price": price,
                            "airline": best.get("airline"),
                        })
    return {"attempted": attempted, "successful": successful}


def check_trips(watchlist: dict, history_entries: list, alert_state: dict, today: str,
                dry_run: bool, quiet: bool,
                outbox: MonitoringStore | None = None) -> dict:
    attempted = successful = 0
    for trip in watchlist.get("trips", []):
        if trip.get("status") != "watching":
            continue
        origin, dest = trip["origin"], trip["destination"]
        depart, return_date = trip["depart_date"], trip.get("return_date")
        attempted += 1
        result = search_flight(origin, dest, depart, return_date)
        if not result:
            if not quiet:
                print(f"[warn] no result for trip {trip['id']} ({origin}->{dest} {depart})")
            continue
        successful += 1
        best = cheapest(result, nonstop_only(trip))
        if not best:
            if not quiet:
                print(f"[skip] no nonstop fare for trip {trip['id']} "
                      f"({origin}->{dest} {depart})")
            continue
        price = best["price"]
        history_entries.append({
            "date_checked": today, "kind": "trip", "trip_id": trip["id"],
            "origin": origin, "dest": dest, "depart_date": depart, "return_date": return_date,
            "price": price, "airline": best.get("airline"), "stops": best.get("stops"),
        })

        target = trip.get("target_price")
        rt = return_date  # round-trip label if this trip has a return leg
        if target is not None:
            is_deal, note = price <= target, f"(target: ${target})"
        else:
            prior_prices = [e["price"] for e in history_entries
                            if e.get("kind") == "trip" and e.get("trip_id") == trip["id"]
                            and e["date_checked"] != today]
            prior_min = min(prior_prices) if prior_prices else None
            # First observation is just the baseline — don't alert on it (The user
            # already knows today's price; there's nothing to compare against).
            is_deal = prior_min is not None and price < prior_min
            note = "(new low since watching)"
        if not is_deal:
            continue

        key = f"trip:{trip['id']}"
        last = alert_state.get(key)
        if last and last.get("last_alerted_price", 10**9) <= price:
            continue
        text = format_alert(trip["label"], origin, dest, depart, price, best, note, return_date=rt)
        notice_key = f"{OUTBOX_DOMAIN}:{key}:{price}"
        if deliver_alert(text, notice_key, dry_run, outbox):
            alert_state[key] = {"last_alerted_price": price, "last_alerted_at": today}
    return {"attempted": attempted, "successful": successful}


def print_report(watchlist: dict, history_entries: list) -> None:
    print("Flight Deal Tracker -- current status\n")
    recent_cutoff = (date.today() - timedelta(days=30)).isoformat()
    today = date.today().isoformat()
    for route in watchlist.get("opportunistic_routes", []):
        threshold = route_roundtrip_threshold(route)
        # Only round-trip samples count — legacy one-way rows aren't comparable.
        recent = [e for e in history_entries
                  if e.get("kind") == "opportunistic" and e.get("route_id") == route["id"]
                  and e.get("trip_type") == "roundtrip"
                  and e.get("date_checked", "") >= recent_cutoff
                  and e.get("depart_date", "") >= today]
        if not recent:
            print(f"{route['label']}: no recent future round-trip data (threshold ${threshold})")
            continue
        best = min(recent, key=lambda e: e["price"])
        span = f"{best['depart_date']}→{best['return_date']}" if best.get("return_date") else best["depart_date"]
        print(f"{route['label']}: recent best ${best['price']} ({span}) (round-trip threshold ${threshold})")
    for trip in watchlist.get("trips", []):
        recent = [e for e in history_entries if e.get("kind") == "trip" and e.get("trip_id") == trip["id"]]
        status = trip.get("status")
        if not recent:
            print(f"Trip '{trip['label']}' ({status}): no data yet")
            continue
        best = min(recent, key=lambda e: e["price"])
        print(f"Trip '{trip['label']}' ({status}): best seen ${best['price']} on {best['depart_date']}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Sample and log price history, but print alerts instead of sending them AND don't touch alert-dedup state (so a real run still fires them)")
    parser.add_argument("--report", action="store_true", help="Read-only: print current best known prices, no logging/alerting")
    parser.add_argument("--quiet", action="store_true", help="Suppress warnings for missing search results")
    args = parser.parse_args()

    watchlist = load_json(WATCHLIST_FILE, {"opportunistic_routes": [], "trips": []})
    today = date.today().isoformat()

    if args.report:
        history = load_json(HISTORY_FILE, {"entries": []})
        print_report(watchlist, history["entries"])
        return

    outbox = None if args.dry_run else MonitoringStore(OUTBOX_DB)
    if outbox is not None:
        retry_pending(outbox, args.quiet)

    with locked_state(HISTORY_FILE, default={"entries": []}) as history:
        with locked_state(ALERT_STATE_FILE, default={}) as alert_state:
            # A dry run logs price history but must NOT persist alert-dedup
            # state — otherwise a "would send" alert it printed silently
            # suppresses the real alert on the next cron run. Hand the check
            # functions a throwaway copy and leave the on-disk dict untouched.
            working_state = dict(alert_state) if args.dry_run else alert_state
            deals: list[dict] = []
            route_coverage = check_opportunistic_routes(
                watchlist, history["entries"], working_state, today,
                args.dry_run, args.quiet, outbox, deals)
            if deals:
                check_alternate_origin(deals, history["entries"], today, args.quiet)
            deliver_digest(deals, history["entries"], working_state, today,
                           args.dry_run, outbox)
            trip_coverage = check_trips(
                watchlist, history["entries"], working_state, today,
                args.dry_run, args.quiet, outbox)
            if outbox is not None:
                attempted = route_coverage["attempted"] + trip_coverage["attempted"]
                successful = route_coverage["successful"] + trip_coverage["successful"]
                status = "complete" if successful == attempted else (
                    "partial" if successful else "unavailable")
                outbox.record_source(
                    "flight-deal-tracker:quotes", status,
                    coverage={"attempted": attempted, "successful": successful},
                    detail=None if status == "complete" else "one or more quote searches failed",
                )
            cutoff = (date.today() - timedelta(days=HISTORY_RETENTION_DAYS)).isoformat()
            history["entries"] = [e for e in history["entries"] if e["date_checked"] >= cutoff]


if __name__ == "__main__":
    main()
