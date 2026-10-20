#!/usr/bin/env python3
"""Forecast for an event's PLACE and HOUR. Deterministic; never guesses.

Before this, the agent fetched a weather page for a city it picked itself ("Dallas",
even for an event in New York) and then often forgot to pass the number on. The
temperature drives real rules (shorts at 80F+, no long sleeves at 85F+), so it has
to come from the event, not from improvisation.

Open-Meteo: free, no key. Returns None with a reason when it cannot know (event in
the past, beyond the 15-day horizon, place not found, network down) — callers must
then run without temperature rules rather than invent a number.

Usage:
  python3 event_forecast.py --location "100 Example Ave, New York, NY 10001" --start 2026-09-22T19:00:00-04:00
"""
import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST = "https://api.open-meteo.com/v1/forecast"
HORIZON_DAYS = 15
CITY_IN_ADDRESS = re.compile(r",\s*([A-Za-z][A-Za-z .'’-]+?),?\s+[A-Z]{2}\b(?:\s+\d{5})?")


def get(url, params):
    with urllib.request.urlopen(f"{url}?{urllib.parse.urlencode(params)}", timeout=12) as response:
        return json.loads(response.read())


def candidates(location):
    """Most specific first: the city inside a street address, then the text itself (venues, airports)."""
    location = (location or "").strip()
    out = []
    m = CITY_IN_ADDRESS.search(location)
    if m:
        out.append(m.group(1).strip())
    parts = [p.strip() for p in location.split(",") if p.strip()]
    if len(parts) >= 2 and not re.search(r"\d", parts[-2]):
        out.append(parts[-2])
    if location and not re.match(r"^\d", location):
        out.append(parts[0] if parts else location)
    return list(dict.fromkeys(x for x in out if x and len(x) > 2))


def locate(location):
    for name in candidates(location):
        try:
            hits = get(GEOCODE, {"name": name, "count": 1, "language": "en"}).get("results") or []
        except Exception:  # noqa: BLE001
            return None, "geocoding unavailable"
        if hits:
            h = hits[0]
            return {"lat": h["latitude"], "lon": h["longitude"], "place": ", ".join(x for x in (h.get("name"), h.get("admin1")) if x), "resolved_by": f"geocoded '{name}'"}, None
    try:                                     # travel-aware: where the user is right now
        sys.path.insert(0, str(ROOT.parent / "shared"))
        from current_location import get_coords
        lat, lon = get_coords()
        return {"lat": lat, "lon": lon, "place": "current location", "resolved_by": "no usable place on the event; used his current location"}, None
    except Exception:  # noqa: BLE001
        return None, "no place on the event and current location unavailable"


def forecast(location, start_iso, now=None):
    if os.environ.get("WARDROBE_NO_NETWORK") == "1":
        return None, "network disabled"
    now = now or datetime.now(timezone.utc)
    try:
        start = datetime.fromisoformat(start_iso)
    except (TypeError, ValueError):
        return None, "event has no usable start time"
    if start.tzinfo is None:
        return None, "event start has no timezone"
    start_utc = start.astimezone(timezone.utc)
    if start_utc < now - timedelta(hours=1):
        return None, "event is in the past"
    if start_utc > now + timedelta(days=HORIZON_DAYS):
        return None, f"more than {HORIZON_DAYS} days out — no forecast yet"
    where, problem = locate(location)
    if not where:
        return None, problem
    day = start_utc.date().isoformat()
    try:
        data = get(FORECAST, {"latitude": where["lat"], "longitude": where["lon"], "timezone": "GMT", "start_date": day, "end_date": day,
                              "hourly": "temperature_2m,apparent_temperature,precipitation_probability,wind_speed_10m",
                              "temperature_unit": "fahrenheit", "wind_speed_unit": "mph"})
    except Exception:  # noqa: BLE001
        return None, "forecast service unavailable"
    hours = data.get("hourly", {})
    target = start_utc.replace(minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:00")
    if target not in hours.get("time", []):
        return None, "forecast has no value for that hour"
    i = hours["time"].index(target)
    return {"feels_like_f": round(hours["apparent_temperature"][i]), "temp_f": round(hours["temperature_2m"][i]),
            "wind_mph": round(hours["wind_speed_10m"][i]), "rain_chance_pct": hours["precipitation_probability"][i],
            "place": where["place"], "resolved_by": where["resolved_by"], "source": "open-meteo"}, None


def daily_forecast(location, start_date, end_date, now=None):
    """Day-by-day highs, lows and rain chance for a place over a date range (packing_list.py).
    Clipped to today .. +15 days; says so with `partial` when the trip runs past that."""
    if os.environ.get("WARDROBE_NO_NETWORK") == "1":
        return None, "network disabled"
    today = (now or datetime.now(timezone.utc)).date()
    try:
        start, end = datetime.fromisoformat(start_date).date(), datetime.fromisoformat(end_date).date()
    except (TypeError, ValueError):
        return None, "trip dates unreadable"
    first, last = max(start, today), min(end, today + timedelta(days=HORIZON_DAYS))
    if last < first:
        return None, f"trip is more than {HORIZON_DAYS} days out — no forecast yet"
    where, problem = locate(location)
    if not where:
        return None, problem
    try:
        data = get(FORECAST, {"latitude": where["lat"], "longitude": where["lon"], "timezone": "auto", "start_date": first.isoformat(),
                              "end_date": last.isoformat(), "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                              "temperature_unit": "fahrenheit"})
    except Exception:  # noqa: BLE001
        return None, "forecast service unavailable"
    daily = data.get("daily", {})
    days = [{"date": d, "max_f": round(hi), "min_f": round(lo), "rain_pct": rp or 0}
            for d, hi, lo, rp in zip(daily.get("time", []), daily.get("temperature_2m_max", []), daily.get("temperature_2m_min", []), daily.get("precipitation_probability_max", []))
            if hi is not None and lo is not None]
    if not days:
        return None, "forecast has no values for those days"
    return {"days": days, "place": where["place"], "partial": last < end, "source": "open-meteo"}, None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--location", default="")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", help="with --end, print the day-by-day forecast for a trip instead")
    args = ap.parse_args()
    if args.end:
        result, problem = daily_forecast(args.location, args.start[:10], args.end[:10])
        print(json.dumps(result if result else {"forecast": None, "why": problem}, indent=2)); return
    result, problem = forecast(args.location, args.start)
    print(json.dumps(result if result else {"forecast": None, "why": problem}, indent=2))


if __name__ == "__main__":
    main()
