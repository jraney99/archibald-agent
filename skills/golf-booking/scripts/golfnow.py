#!/usr/bin/env python3
"""GolfNow adapter: the one tee-time provider this skill can read and book.

Why an API for the search and a browser for the booking: GolfNow's site is a JS app, but
its tee-sheet POST endpoint answers unauthenticated (reverse-engineered in the ClawHub
golf-tee-times skill and re-verified here 2026-10-11 against Example Creek West and Cedar
Hills). Booking needs the user's login and a saved card, so that half runs in his
OpenClaw browser session (browser_helpers.py).

What the API told us on 2026-10-11, which the design depends on:
  - It is facility-specific. SearchType 1 + FacilityId works; an area search returns
    nothing. So every approved course carries its GolfNow facility id.
  - A course GolfNow does not sell answers HTTP 500 with an empty body (Oak Park 1004,
    Maple Park 1005/1006 - their GolfNow pages redirect to a details page). That is
    reported as ProviderError, never as "no tee times".
  - Prices are per golfer. `displayRate.value` is the green fee; the page adds a
    convenience fee and sales tax, so the booking total is only known at checkout.
  - The rate id doubles as the tee-time id in the booking URL:
    /tee-times/facility/{facilityId}/tee-time/{defaultTeeTimeRateId}.
  - Rates named "Prepaid - Online Rate" are charged at booking and the course policy
    text says they are non-refundable and a no-show is charged in full.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from datetime import date, datetime

API = "https://www.golfnow.com/api/tee-times/tee-time-results"
SITE = "https://www.golfnow.com"
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Origin": SITE,
}
TIMEOUT = 20


class ProviderError(RuntimeError):
    """GolfNow could not answer for this course - distinct from an empty tee sheet."""


def payload(facility_id: int, day: date, players: int, lat: float, lon: float) -> dict:
    return {
        "Radius": 50, "Latitude": lat, "Longitude": lon,
        "PageSize": 50, "PageNumber": 0, "SearchType": 1,
        "SortBy": "Date", "SortDirection": 0, "Date": day.strftime("%b %d %Y"),
        "BestDealsOnly": False, "PriceMin": "0", "PriceMax": "10000",
        "Players": str(players), "Holes": "3", "FacilityType": 0,
        "RateType": "all", "TimeMin": "10", "TimeMax": "42",
        "FacilityId": int(facility_id), "SortByRollup": "Date.MinDate", "View": "Grouping",
        "ExcludeFeaturedFacilities": True, "TeeTimeCount": 50,
        "PromotedCampaignsOnly": "false",
        "CurrentClientDate": day.strftime("%Y-%m-%dT05:00:00.000Z"),
    }


def fetch(facility_id: int, day: date, players: int, lat: float = 32.78, lon: float = -96.80) -> dict:
    """The raw tee-sheet response for one facility on one day."""
    body = json.dumps(payload(facility_id, day, players, lat, lon)).encode()
    req = urllib.request.Request(API, data=body, headers=HEADERS, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code >= 500:
            raise ProviderError(
                f"GolfNow answered HTTP {exc.code} for facility {facility_id}. That is what it "
                f"does for a course it does not sell (the course page redirects to a details "
                f"page) - book it with the course directly.") from None
        raise ProviderError(f"GolfNow answered HTTP {exc.code} for facility {facility_id}") from None
    except urllib.error.URLError as exc:
        raise ProviderError(f"GolfNow unreachable: {exc.reason}") from None
    try:
        return json.loads(raw)
    except ValueError:
        raise ProviderError(f"GolfNow returned something that is not JSON for facility {facility_id}") from None


def money(value) -> float:
    """A number out of GolfNow's mixed scalar / {'value': ...} price objects."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace("$", "").replace(",", "").strip())
        except ValueError:
            return 0.0
    if isinstance(value, dict):
        for key in ("value", "amount", "price"):
            if key in value:
                return money(value[key])
    return 0.0


def _time_label(dt: datetime) -> str:
    return dt.strftime("%-I:%M %p")


def _local_time(t: dict) -> datetime:
    """GolfNow writes the COURSE-LOCAL clock time with a +00:00 suffix ('2026-10-17T15:30:00
    +00:00' for a 3:30 PM Dallas tee time). The offset is noise; the digits are the time."""
    raw = (t.get("time") or {}).get("date") or ""
    return datetime.fromisoformat(raw[:19])


def slots(data: dict) -> list:
    """Every tee time in a response as a flat dict, one per tee time (its default rate).

    Keys: time (datetime, course-local), time_label, price (per golfer), holes, cart,
    rate_name, prepaid, hot, trade, rate_id, detail_url, player_rule, course (name),
    facility_id, address, phone, restricted.
    """
    out = []
    for t in (data.get("ttResults") or {}).get("teeTimes") or []:
        rates = t.get("teeTimeRates") or []
        default_id = t.get("defaultTeeTimeRateId")
        rate = next((r for r in rates if r.get("teeTimeRateId") == default_id), rates[0] if rates else {})
        fac = t.get("facility") or {}
        addr = fac.get("address") or {}
        when = _local_time(t)
        price = money(t.get("displayRate")) or money(((rate.get("singlePlayerPrice") or {}).get("greensFees")))
        rate_name = rate.get("rateName") or ""
        out.append({
            "time": when,
            "time_label": _time_label(when),
            "price": round(price, 2),
            "holes": int(rate.get("holeCount") or t.get("multipleHolesRate") or 18),
            "cart": bool(rate.get("isCartIncluded")),
            "rate_name": rate_name,
            # GolfNow collects every online rate at booking (hot deals included); the flag is
            # kept for the record, and it is True unless the rate is explicitly pay-at-course.
            "prepaid": "pay at course" not in rate_name.lower(),
            "hot": bool(rate.get("isHotDeal")),
            "trade": bool(rate.get("isTradeOffer")),
            "rate_id": default_id,
            "detail_url": SITE + (t.get("detailUrl") or f"/tee-times/facility/{t.get('facilityId')}/tee-time/{default_id}"),
            "player_rule": t.get("playerRule") or rate.get("playerRule") or "Any",
            "restricted": bool(t.get("isReservationRestricted")),
            "course": fac.get("name") or "",
            "facility_id": fac.get("facilityId") or t.get("facilityId"),
            "address": ", ".join(x for x in (addr.get("line1"), addr.get("city"), addr.get("stateProvinceCode"), addr.get("postalCode")) if x),
            "phone": fac.get("phoneNumber") or "",
        })
    out.sort(key=lambda s: s["time"])
    return out


_TIME_RE = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*([AaPp])\.?[Mm]?")


def norm_time(text: str) -> str:
    """'3:30 PM' from '3:30pm', '330 pm', '3 PM', 'at 3:30 PM'. '' if there is no time."""
    m = _TIME_RE.search(text or "")
    if not m:
        return ""
    hour = int(m.group(1))
    minute = int(m.group(2) or 0)
    if not (1 <= hour <= 12 and 0 <= minute < 60):
        return ""
    return f"{hour}:{minute:02d} {m.group(3).upper()}M"


def clock(hhmm: str) -> int:
    """'07:30' -> minutes since midnight; also accepts '7:30 AM'."""
    label = norm_time(hhmm)
    if label:
        dt = datetime.strptime(label, "%I:%M %p")
        return dt.hour * 60 + dt.minute
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def within(slot: dict, earliest: str, latest: str) -> bool:
    minute = slot["time"].hour * 60 + slot["time"].minute
    return clock(earliest) <= minute <= clock(latest)


def estimate_total(price: float, players: int, fee_allowance_per_golfer: float) -> float:
    """The most this booking may cost before confirm_booking refuses. Green fees plus the
    allowance for GolfNow's convenience fee and sales tax ($8.27 on one $60.95 golfer,
    2026-10-11); the real Total is read off the checkout page and compared to this."""
    return round(players * price + players * fee_allowance_per_golfer, 2)
