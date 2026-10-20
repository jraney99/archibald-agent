#!/usr/bin/env python3
"""
Compute a leave-by popup-reminder offset for recurring personal appointments
(coaching, doctor, haircut, etc.) so the reminder fires with enough lead time
to drive there and still arrive a few minutes early — no separate calendar
block needed.

Origin is the precise home address matching the user's current gym-profile
location (same signal detect_travel.py maintains in hevy_config.json),
falling back to his live coordinates (current_location.py) when he's at
neither home base (hotel/traveling). Destination is geocoded via TomTom
using the same API key already used for flight/airport drive-time reminders.
"""
import json
import re
import math
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent))
from current_location import get_coords  # noqa: E402

TRAVEL_CONFIG = Path("~/.openclaw/workspace/Skills/email-monitor/travel_config.json")
HEVY_CONFIG   = Path("~/.openclaw/workspace/Skills/hevy-workout/config/hevy_config.json")

_geocode_cache = {}  # address -> (lat, lon), memoized for the life of the process


def _travel_cfg() -> dict:
    try:
        return json.loads(TRAVEL_CONFIG.read_text())
    except Exception:
        return {}


def _active_profile() -> str:
    try:
        cfg = json.loads(HEVY_CONFIG.read_text())
        return cfg.get("equipment", {}).get("active_profile", "")
    except Exception:
        return ""


def origin_coords_and_label() -> Tuple[float, float, str]:
    """Origin for the drive-time calc: the precise home address matching the
    active gym profile (gym_b -> LA home, gym_a -> Dallas home),
    falling back to live coordinates when the user is at neither home base."""
    cfg      = _travel_cfg()
    homes    = cfg.get("homes", {})
    profile  = _active_profile()
    if profile == "gym_b" and "la" in homes:
        h = homes["la"]
        return h["lat"], h["lng"], h.get("label", "Home")
    if profile == "gym_a" and "dallas" in homes:
        h = homes["dallas"]
        return h["lat"], h["lng"], h.get("label", "Home")
    lat, lon = get_coords()
    return lat, lon, "current location"


def geocode(address: str, strict: bool = True) -> Optional[Tuple[float, float]]:
    """strict=True (default, unchanged behavior) requires a street-level match
    ("Point Address"/"Address Range") — right for real addresses (hotels,
    appointments) where a coarse match could silently land on the wrong
    street. strict=False additionally accepts a "Geography" match (a named
    place/neighborhood/municipality, e.g. "Greenwich Village, New York, NY")
    — right for a deliberately vague answer like "my friend's place in
    Greenwich Village", where there's no street address to ask for and a
    neighborhood-level estimate is the whole point. Confidence is still
    required either way — that's what actually catches a garbage/ambiguous
    input, not the type filter."""
    cache_key = (address, strict)
    if cache_key in _geocode_cache:
        return _geocode_cache[cache_key]
    key = _travel_cfg().get("tomtom_key", "")
    if not key:
        return None
    q   = urllib.parse.quote(address)
    url = f"https://api.tomtom.com/search/2/geocode/{q}.json?key={key}&limit=1"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.loads(r.read())
        result = data["results"][0]
        allowed_types = ("Point Address", "Address Range") if strict else \
                        ("Point Address", "Address Range", "Geography", "Street")
        result_type = result.get("type")
        if result_type not in allowed_types:
            return None
        score = result.get("matchConfidence", {}).get("score", 0)
        # Point Address/Address Range is TomTom's most precise category —
        # the type itself is most of the safety signal there. A real,
        # correct address (verified: the user's own apartment address, with a
        # unit number) can still score as low as ~0.74 purely
        # because an apartment/unit number isn't in TomTom's building
        # database, with no actual location ambiguity — a flat 0.75 bar
        # rejected a perfectly good address. Geography/Street matches carry
        # real city/region-level ambiguity risk (see the wrong-city
        # case below) and keep the stricter bar.
        min_score = 0.5 if result_type in ("Point Address", "Address Range") else 0.75
        if score < min_score:
            return None
        pos    = result["position"]
        coords = (pos["lat"], pos["lon"])
        _geocode_cache[cache_key] = coords
        return coords
    except Exception:
        return None


POI_SCAN = 5   # how many POI results to consider before giving up

# TomTom returns its own abbreviations in freeformAddress ("24 Example Vlg"),
# and its geocoder then will not resolve them at street level. Expand on the way out
# so a stored address is one the router can actually use.
_EXPAND = {" Vlg": " Village", " Expy": " Expressway", " Fwy": " Freeway",
           " Pkwy": " Parkway", " Plz": " Plaza"}


def _expand(address: str) -> str:
    for short, long in _EXPAND.items():
        address = address.replace(short + ",", long + ",")
        if address.endswith(short):
            address = address[: -len(short)] + long
    return address

# Characters that carry meaning for a restaurant name and must survive normalisation.
# WHY (2026-10-09): the old guard was `re.sub(r"[^a-z0-9]", "", name.lower())`, which
# deletes "&" and strips accents to nothing. That silently refused four addresses TomTom
# had returned correctly:
#     "Salt and Pepper"         vs POI "Salt & Pepper"       -> andpepper / pepper
#     "greenpoint ... and ..."  vs POI "Green Point ... & ..." -> and vanishes one side
#     "cafe oceano"             vs POI "Café Oceano"         -> cafeoceano / cafoceano
# Each then fell through to geocoding the bare NAME, which with strict=False matches a
# same-named STREET: Café Oceano (in a shopping village) resolved to Oceano Avenue,
# 2.5 miles the wrong way, at a perfectly plausible 14 mph.
_FOLD = {"&": "and", "+": "and"}
_ACCENTS = str.maketrans("áàâäãåéèêëíìîïóòôöõúùûüñçÁÀÂÄÃÅÉÈÊËÍÌÎÏÓÒÔÖÕÚÙÛÜÑÇ",
                         "aaaaaaeeeeiiiiooooouuuuncAAAAAAEEEEIIIIOOOOOUUUUNC")


def normalise_place_name(name: str) -> str:
    """A restaurant name reduced to what two spellings of it have in common."""
    text = (name or "").translate(_ACCENTS).lower()
    for symbol, word in _FOLD.items():
        text = text.replace(symbol, f" {word} ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    # "and"/"the" are the words that differ between a sign and a listing
    words = [w for w in text.split() if w not in ("and", "the", "a", "of")]
    return "".join(words)


# MATCHING A SIGN TO A LISTING (2026-10-09). Two real cases pull in opposite
# directions, and a single loose test cannot serve both:
#
#   "Casa Verde"           vs POI "Casa Verde Tex-Mex"     -> MUST match (official
#                                                             names carry extra words)
#   "Oceane"               vs POI "Ocean East"             -> must NOT match (a
#                                                             different place that
#                                                             happens to share a stem)
#
# A substring test accepts both. A length ratio rejects both - it broke
# test_place_address.py's Casa Verde case. What separates them is word boundaries:
# "Casa Verde" appears in "Casa Verde Tex-Mex" as whole words, while "oceane" only
# appears inside "ocean east" by running across the gap between two words.
#
# So: accept an exact normalised match, or the asked-for name as a whole-WORD prefix of
# the official one. "greenpoint" vs "Green Point" needs the first (spacing differs);
# "Casa Verde" vs "Casa Verde Tex-Mex" needs the second.
def _tokens(name: str) -> list[str]:
    text = (name or "").translate(_ACCENTS).lower()
    for symbol, word in _FOLD.items():
        text = text.replace(symbol, f" {word} ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return [w for w in text.split() if w not in ("and", "the", "a", "of")]


def _name_matches(wanted: str, found: str) -> bool:
    a, b = normalise_place_name(wanted), normalise_place_name(found)
    if not a or not b:
        return False
    if a == b:
        return True
    ta, tb = _tokens(wanted), _tokens(found)
    short, long = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return bool(short) and long[:len(short)] == short


def place_address(name: str, city: str = "Dallas, TX") -> Optional[str]:
    """A named place's full street address, or None.

    WHY (2026-10-08): the restaurant-booking skill wrote the restaurant's NAME into the
    calendar event's location field, so a dinner event read "location: <restaurant
    name>". Waze reads his calendar and offers a time-to-leave ping, but only for an
    event it can route to, and a bare name is not routable — the same name exists in
    other cities too. A real address turns every restaurant booking into a departure alert
    with live traffic, which is better than any estimate made days ahead.

    geocode() above cannot do this: it accepts only "Point Address" / "Address Range"
    results, and a restaurant name resolves to a POI, which it rejects by design.

    Returns None rather than a guess. A WRONG address is worse than a bare name — the
    name at least makes him look it up, while a confident wrong one drives him across
    town. So the POI's own name must match what was asked for.
    """
    key = _travel_cfg().get("tomtom_key", "")
    if not key or not (name or "").strip():
        return None
    query = f"{name}, {city}" if city and city.lower() not in name.lower() else name
    # SCAN THE TOP FEW, NOT JUST THE FIRST (2026-10-09): TomTom ranks a fuzzy
    # competitor above an exact match often enough to matter. One "<name> ristorante"
    # query returned a different ristorante first and the real one third, so a limit of 1
    # plus a name guard refused a place it had actually found.
    url = (f"https://api.tomtom.com/search/2/poiSearch/{urllib.parse.quote(query)}.json"
           f"?key={key}&limit=1&countrySet=US")
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            results = json.loads(response.read()).get("results") or []
        for hit in results:
            address = (hit.get("address") or {}).get("freeformAddress")
            found = ((hit.get("poi") or {}).get("name") or "")
            if not address:
                continue
            # The match has to be the place asked for. TomTom will happily return the
            # nearest something for a name it does not know, and a confident wrong
            # address is the one failure mode worth refusing outright.
            if _name_matches(name, found):
                return _expand(address)
        return None
    except Exception:
        return None


def place_address_and_coords(name: str, city: str = "Dallas, TX"):
    """(address, (lat, lon)) for a named place, or (None, None).

    WHY THE COORDINATES TOO (2026-10-09): the POI search already knows exactly where
    the place is. Storing only the address string means every drive calculation
    re-geocodes it, and a geocode of an address TomTom cannot resolve at street level
    (its own "24 Example Vlg") falls back to matching the NAME against a
    same-named street. Keeping the position removes that whole failure mode and one
    API round-trip per leg.
    """
    key = _travel_cfg().get("tomtom_key", "")
    if not key or not (name or "").strip():
        return None, None
    query = f"{name}, {city}" if city and city.lower() not in name.lower() else name
    url = (f"https://api.tomtom.com/search/2/poiSearch/{urllib.parse.quote(query)}.json"
           f"?key={key}&limit=1&countrySet=US")
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            results = json.loads(response.read()).get("results") or []
        for hit in results:
            address = (hit.get("address") or {}).get("freeformAddress")
            found = ((hit.get("poi") or {}).get("name") or "")
            pos = hit.get("position") or {}
            if not address or "lat" not in pos:
                continue
            if _name_matches(name, found):
                return _expand(address), (pos["lat"], pos["lon"])
        return None, None
    except Exception:
        return None, None


def drive_minutes(origin_lat: float, origin_lon: float,
                   dest_lat: float, dest_lon: float,
                   depart_at: Optional[datetime] = None) -> Optional[int]:
    """Driving minutes with traffic. depart_at (timezone-aware, in the future) uses
    TomTom's predicted traffic for that time instead of right now."""
    key = _travel_cfg().get("tomtom_key", "")
    if not key:
        return None
    url = (
        f"https://api.tomtom.com/routing/1/calculateRoute/"
        f"{origin_lat},{origin_lon}:{dest_lat},{dest_lon}/json"
        f"?key={key}&traffic=true&travelMode=car"
    )
    if depart_at is not None:
        url += "&departAt=" + urllib.parse.quote(depart_at.isoformat(timespec="seconds"))
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            data    = json.loads(r.read())
        seconds = data["routes"][0]["summary"]["travelTimeInSeconds"]
        return max(5, math.ceil(seconds / 60))
    except Exception:
        return None


def leave_by_minutes(dest_address: str, origin_address: Optional[str] = None,
                      arrival_buffer: int = 5, strict: bool = True,
                      max_plausible_minutes: int = 180) -> Optional[int]:
    """Minutes-before-event-start a Calendar popup reminder should fire so
    the user leaves in time to arrive `arrival_buffer` minutes early. Origin
    defaults to home/current-location (origin_coords_and_label); pass
    origin_address (e.g. the immediately preceding calendar event's
    location) to compute from wherever the user is actually coming from instead.
    Returns None if geocoding/routing fails. Interactive calendar creation
    must request a verified address/offset, never silently use a flat default.

    strict=False allows a "Street"/"Geography" match (still confidence-
    gated) in addition to a precise address — real calendar event locations
    are often a business/venue name (e.g. "<Shop Name> <Plaza Name> -") rather
    than a literal street address, and TomTom often can't resolve those as
    "Point Address"/"Address Range" even with high confidence. Silently
    skipping a real, upcoming, timed calendar event's wake-time requirement
    is a worse failure than a coarser drive-time estimate.

    max_plausible_minutes guards a real, verified failure mode: an
    under-specified address with no city/state (e.g. a calendar event
    titled just a house number and street name with no city) can geocode to the *wrong
    city* at full confidence and a "Point Address" type match — neither
    type nor confidence catches this, since TomTom always returns its best
    guess rather than failing. A same-day calendar event's drive time
    coming back at several hours is a far more reliable tell that something
    resolved to the wrong city than a fundamentally wrong-city match ever
    is. Every caller of this function assumes a local/same-day commute
    (the separate flight/lodging system in email-monitor's daemon.py
    handles actual travel), so this cap applies unconditionally rather
    than being opt-in — a caller with a genuine need for a longer plausible
    drive can pass a larger value explicitly."""
    dest = geocode(dest_address, strict=strict)
    if not dest:
        return None
    if origin_address:
        origin = geocode(origin_address, strict=strict)
        if not origin:
            return None
        origin_lat, origin_lon = origin
    else:
        origin_lat, origin_lon, _ = origin_coords_and_label()
    minutes = drive_minutes(origin_lat, origin_lon, dest[0], dest[1])
    if minutes is None:
        return None
    if minutes > max_plausible_minutes:
        return None
    return minutes + arrival_buffer
