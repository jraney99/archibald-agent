#!/usr/bin/env python3
"""
Airport leave-time calculator — pure function, no I/O, no network.

Replaces the old flat "be at the airport 90 minutes early" buffer in
daemon.py::_schedule_flight_reminders and travel_reminder.py. Everything is
computed backward from *gate close*, not from the scheduled departure:

    gate_close      = departure - gate_close_lead        (default 15 min)
    arrive_terminal = gate_close - walk - security_p85 - buffer
    leave_home      = arrive_terminal - drive - parking

Design decisions (see the Airport Departure Timing spec):
  * Security is sized to the p85 of the wait distribution, not the mean
    (mean ~5 min, tail ~40; p85 is the compromise). The caller supplies the
    p85 value — live from TSA when available, otherwise a static per-airport
    fallback — and tells us which via `security_source`.
  * `buffer_minutes` is the single risk knob. Default 20. Everything else
    here is measured input, not tuned.

Adjustment rules, applied to arrive_terminal after the base calc:
  * checked bag        -> floor arrive_terminal at departure - 60
                          (bag cutoff is 45 min; missing it is unrecoverable)
  * international       -> 30 min earlier (document check; PreCheck doesn't help)
  * departure 06:00-08:59 local -> 10 min earlier (PreCheck lanes open late)
  * precip / below freezing at origin -> 15 min earlier

Fail soft: the function never raises on a missing data source. The caller
passes a fallback value plus the matching `*_source` flag, and every term
that came from a fallback is listed in the returned `estimated_terms` so the
outbound message can say so.

Run `python3 compute_leave_time.py` for a hardcoded-input demo, or
`python3 test_compute_leave_time.py` for the test suite.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional


INTERNATIONAL_ADJ_MIN = 30
EARLY_MORNING_ADJ_MIN = 10
WEATHER_ADJ_MIN = 15
CHECKED_BAG_FLOOR_MIN = 60
DEFAULT_GATE_CLOSE_LEAD_MIN = 15
DEFAULT_BUFFER_MIN = 20
# Used only when the caller fails to supply a numeric drive/walk value at all.
CONSERVATIVE_DRIVE_MIN = 45
CONSERVATIVE_WALK_MIN = 15


def _parse(dt_str: str) -> datetime:
    d = datetime.fromisoformat(dt_str)
    if d.tzinfo is None:
        raise ValueError(f"departure must be timezone-aware ISO 8601, got: {dt_str!r}")
    return d


def _fmt(dt: datetime) -> str:
    return dt.strftime("%-I:%M %p")


def _fmt_day(dt: datetime) -> str:
    return dt.strftime("%a %b %-d %-I:%M %p")


def compute_leave_time(inp: dict) -> dict:
    """Compute when to walk out the door for a flight.

    Required input keys:
        scheduled_departure : tz-aware ISO 8601 string

    Optional input keys (defaults in parens):
        effective_departure   : tz-aware ISO string, overrides scheduled when the
                                inbound aircraft is late (default: scheduled)
        inbound_delay_minutes : int, for the explanatory note only (0)
        airport               : IATA code, for notes ("")
        airport_known         : bool, False => "unlearned airport" note + low
                                confidence (True)
        terminal              : terminal id, for notes ("")
        buffer_minutes        : int (20)
        gate_close_lead_minutes : int (15)
        walk_minutes          : int; None => conservative default + estimated
        walk_estimated        : bool, True when walk_minutes is a static seed
                                rather than a learned median (False)
        security_p85          : int minutes; None => 0 + flagged estimated
        security_source       : "live" | "static" ("static")
        drive_minutes         : int; None => conservative default + estimated
        drive_source          : "live" | "static" ("static")
        parking_minutes       : int (0)
        checked_bag           : bool (False)
        international          : bool (False)
        precip_or_freezing    : bool (False)
        weather_source        : "live" | "unavailable" (None -> not considered)
        stage                 : "rough" | "final" | "recheck" ("final")

    Returns a dict with leave_home / arrive_terminal / gate_close (ISO +
    display), a full breakdown, estimated_terms, adjustments_applied,
    checked_bag_floor_hit, confidence, and human-readable notes.
    """
    sched_dep = _parse(inp["scheduled_departure"])
    eff_dep = _parse(inp["effective_departure"]) if inp.get("effective_departure") else sched_dep
    inbound_delay = int(inp.get("inbound_delay_minutes") or 0)

    airport = inp.get("airport", "") or ""
    airport_known = inp.get("airport_known", True)
    terminal = inp.get("terminal", "") or ""
    stage = inp.get("stage", "final")

    buffer_min = int(inp.get("buffer_minutes") if inp.get("buffer_minutes") is not None else DEFAULT_BUFFER_MIN)
    gate_lead = int(inp.get("gate_close_lead_minutes") if inp.get("gate_close_lead_minutes") is not None
                    else DEFAULT_GATE_CLOSE_LEAD_MIN)
    parking_min = int(inp.get("parking_minutes") or 0)

    estimated: list[str] = []
    notes: list[str] = []      # situational / actionable — shown as bullets
    caveats: list[str] = []    # routine "this term is an estimate" explanations

    # ── walk ──────────────────────────────────────────────────────────────
    walk_min = inp.get("walk_minutes")
    if walk_min is None:
        walk_min = CONSERVATIVE_WALK_MIN
        estimated.append("walk")
        caveats.append(f"Walk time missing — conservative {CONSERVATIVE_WALK_MIN} min used.")
    else:
        walk_min = int(walk_min)
        if inp.get("walk_estimated"):
            estimated.append("walk")
            caveats.append("Walk time is a starting estimate; sharpens after a few logged flights.")

    # ── security (p85) ────────────────────────────────────────────────────
    sec_p85 = inp.get("security_p85")
    sec_source = inp.get("security_source", "static")
    if sec_p85 is None:
        sec_p85 = 0
        estimated.append("security")
        notes.append(
            f"No security-wait data for {airport or 'this airport'} — security term set to 0, treat with caution."
        )
    else:
        sec_p85 = int(sec_p85)
        if sec_source != "live":
            estimated.append("security")
            caveats.append(f"Security wait is a static estimate (no live TSA data for {airport or 'this airport'}).")

    # ── drive ─────────────────────────────────────────────────────────────
    drive_min = inp.get("drive_minutes")
    drive_source = inp.get("drive_source", "static")
    if drive_min is None:
        drive_min = CONSERVATIVE_DRIVE_MIN
        estimated.append("drive")
        caveats.append(f"Drive time missing — conservative {CONSERVATIVE_DRIVE_MIN} min used.")
    else:
        drive_min = int(drive_min)
        if drive_source != "live":
            estimated.append("drive")
            caveats.append("Drive time is an estimate (no live traffic data).")

    # ── base calculation ──────────────────────────────────────────────────
    gate_close = eff_dep - timedelta(minutes=gate_lead)
    arrive_terminal = gate_close - timedelta(minutes=walk_min + sec_p85 + buffer_min)

    # ── adjustments (each pushes arrive_terminal earlier) ─────────────────
    adjustments: list[dict] = []

    if inp.get("international"):
        arrive_terminal -= timedelta(minutes=INTERNATIONAL_ADJ_MIN)
        adjustments.append({"rule": "international", "minutes": INTERNATIONAL_ADJ_MIN})

    if 6 <= eff_dep.hour < 9:
        arrive_terminal -= timedelta(minutes=EARLY_MORNING_ADJ_MIN)
        adjustments.append({"rule": "early_morning", "minutes": EARLY_MORNING_ADJ_MIN})

    weather_source = inp.get("weather_source")
    if inp.get("precip_or_freezing"):
        arrive_terminal -= timedelta(minutes=WEATHER_ADJ_MIN)
        adjustments.append({"rule": "weather", "minutes": WEATHER_ADJ_MIN})
    elif weather_source == "unavailable":
        estimated.append("weather")
        caveats.append("Weather not checked; no precipitation buffer applied.")

    # ── checked-bag floor ────────────────────────────────────────────────
    checked_bag_floor_hit = False
    if inp.get("checked_bag"):
        floor = eff_dep - timedelta(minutes=CHECKED_BAG_FLOOR_MIN)
        if arrive_terminal > floor:
            arrive_terminal = floor
            checked_bag_floor_hit = True
            notes.append("Checked bag: arrival floored at departure − 60 (bag cutoff is unrecoverable).")

    # ── leave home ───────────────────────────────────────────────────────
    leave_home = arrive_terminal - timedelta(minutes=drive_min + parking_min)

    # ── notes for inbound delay / unlearned airport / stage ──────────────
    if inbound_delay > 0 or eff_dep != sched_dep:
        notes.insert(
            0,
            f"Inbound aircraft delayed ~{inbound_delay or int((eff_dep - sched_dep).total_seconds() // 60)} min; "
            f"working deadline moved to {_fmt(eff_dep)} (scheduled {_fmt(sched_dep)}).",
        )

    if not airport_known:
        notes.insert(0, f"{airport or 'This airport'} has no saved profile — conservative defaults, treat as unlearned.")

    if stage == "rough":
        notes.insert(0, "Rough estimate for planning — the real number comes ~3h before departure.")

    # ── confidence ───────────────────────────────────────────────────────
    if (
        stage == "rough"
        or not airport_known
        or (sec_source != "live" and drive_source != "live")
    ):
        confidence = "low"
    else:
        confidence = "normal"

    return {
        "leave_home": leave_home.isoformat(),
        "leave_home_display": _fmt(leave_home),
        "arrive_terminal": arrive_terminal.isoformat(),
        "arrive_terminal_display": _fmt(arrive_terminal),
        "gate_close": gate_close.isoformat(),
        "gate_close_display": _fmt(gate_close),
        "scheduled_departure_display": _fmt_day(sched_dep),
        "effective_departure_display": _fmt_day(eff_dep),
        "breakdown": {
            "gate_close_lead": gate_lead,
            "walk": walk_min,
            "security_p85": sec_p85,
            "buffer": buffer_min,
            "drive": drive_min,
            "parking": parking_min,
            "adjustments": adjustments,
            "total_minutes_before_departure": int((eff_dep - leave_home).total_seconds() // 60),
        },
        "estimated_terms": sorted(set(estimated)),
        "adjustments_applied": [a["rule"] for a in adjustments],
        "checked_bag_floor_hit": checked_bag_floor_hit,
        "confidence": confidence,
        "airport": airport,
        "terminal": terminal,
        "notes": notes,
        "caveats": caveats,
    }


def one_line_reason(result: dict) -> str:
    """A single human-readable clause explaining the number, for the push."""
    b = result["breakdown"]
    bits = [f"{b['drive']}m drive", f"{b['security_p85']}m security (p85)", f"{b['walk']}m walk"]
    if b["parking"]:
        bits.insert(1, f"{b['parking']}m parking")
    for a in b["adjustments"]:
        bits.append(f"+{a['minutes']}m {a['rule'].replace('_', ' ')}")
    if result["checked_bag_floor_hit"]:
        bits.append("bag-cutoff floor")
    est = result["estimated_terms"]
    tail = f" · estimated: {', '.join(est)}" if est else ""
    return ", ".join(bits) + tail


if __name__ == "__main__":
    import json

    demo = {
        "scheduled_departure": "2026-09-15T13:40:00-05:00",
        "airport": "DAL",
        "terminal": "MAIN",
        "buffer_minutes": 20,
        "gate_close_lead_minutes": 10,
        "walk_minutes": 10,
        "security_p85": 12,
        "security_source": "live",
        "drive_minutes": 22,
        "drive_source": "live",
        "parking_minutes": 0,
        "weather_source": "live",
        "stage": "final",
    }
    out = compute_leave_time(demo)
    print(json.dumps(out, indent=2))
    print()
    print(f"Leave by {out['leave_home_display']} — {one_line_reason(out)}")
