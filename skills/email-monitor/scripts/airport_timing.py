#!/usr/bin/env python3
"""
Config-resolution layer between travel_config.json and compute_leave_time().

daemon.py (when a flight is booked) and travel_reminder.py (the T-3h run) both
call `build_inputs()` to turn a flight + a drive-time estimate into the input
dict that compute_leave_time() expects, pulling per-airport walk time, security
fallback, terminal mapping and the buffer/gate-lead knobs out of the config.

No network here. `resolve_security()` returns the static fallback; fetch_tsa.py
(added in a later step) is layered in by the caller, which passes the live p85
straight through to compute_leave_time when it has one.
"""
from __future__ import annotations

import os
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from compute_leave_time import compute_leave_time, one_line_reason  # re-exported

_CFG_FILE = Path(os.path.expanduser("~/.openclaw/workspace/Skills/email-monitor/travel_config.json")).expanduser()
# Machine-written overlay (log_flight.py owns it). Kept separate so the feedback
# loop never has to rewrite the hand-formatted travel_config.json.
LEARNED_FILE = Path(os.path.expanduser("~/.openclaw/workspace/Skills/email-monitor/flight_learned.json")).expanduser()

DEFAULT_WALK_MIN = 12
DEFAULT_SECURITY_FALLBACK = {"default": {"p85": 18}, "early_am": {"p85": 25}}


def load_cfg() -> dict:
    try:
        return json.loads(_CFG_FILE.read_text())
    except Exception:
        return {}


def load_learned() -> dict:
    try:
        return json.loads(LEARNED_FILE.read_text())
    except Exception:
        return {}


def airline_code(flight_number: str) -> str:
    fn = (flight_number or "").strip()
    return fn.split()[0] if " " in fn else fn[:2]


def resolve_terminal(cfg: dict, airline: str, orig: str) -> str:
    """Terminal id for walk-time lookup. Mapped by airline (we do not parse
    gate/terminal out of confirmation emails). Falls back to the airport's
    sole terminal when it has exactly one, else ""."""
    mapped = cfg.get("airline_terminal", {}).get(airline, {}).get(orig)
    if mapped:
        return mapped
    terminals = cfg.get("airports", {}).get(orig, {}).get("terminals", {})
    if len(terminals) == 1:
        return next(iter(terminals))
    return ""


def resolve_walk(cfg: dict, orig: str, terminal: str, learned: Optional[dict] = None) -> tuple[int, bool]:
    """Returns (walk_minutes, estimated). `estimated` is True unless the value
    is a learned rolling median from flight_learned.json."""
    learned = load_learned() if learned is None else learned
    lw = learned.get("walk_minutes", {}).get(orig, {}).get(terminal)
    if lw is not None:
        return int(lw), False
    apt = cfg.get("airports", {}).get(orig, {})
    tinfo = apt.get("terminals", {}).get(terminal, {})
    if "walk_minutes" in tinfo:
        return int(tinfo["walk_minutes"]), True
    if "default_walk_minutes" in apt:
        return int(apt["default_walk_minutes"]), True
    return int(cfg.get("default_walk_minutes", DEFAULT_WALK_MIN)), True


def _security_bucket(dep_dt: datetime) -> str:
    return "early_am" if 6 <= dep_dt.hour < 9 else "default"


def resolve_security(cfg: dict, orig: str, dep_dt: datetime, learned: Optional[dict] = None) -> tuple[int, str]:
    """Static p85 security fallback for the departure's time-of-day bucket.
    Prefers a learned value (flight_learned.json) over the config seed.
    Returns (p85_minutes, "static"). Live data is layered on by the caller."""
    learned = load_learned() if learned is None else learned
    bucket = _security_bucket(dep_dt)

    lp = learned.get("security_p85", {}).get(orig, {})
    if bucket in lp:
        return int(lp[bucket]), "static"
    if "default" in lp:
        return int(lp["default"]), "static"

    apt_fb = cfg.get("airports", {}).get(orig, {}).get("security_fallback")
    top_fb = cfg.get("default_security_fallback", DEFAULT_SECURITY_FALLBACK)
    table = apt_fb or top_fb
    entry = table.get(bucket) or table.get("default") or {"p85": 18}
    return int(entry.get("p85", 18)), "static"


def resolve_gate_lead(cfg: dict, airline: str) -> int:
    per_airline = cfg.get("airline_gate_close_lead", {}).get(airline)
    if per_airline is not None:
        return int(per_airline)
    return int(cfg.get("gate_close_lead_minutes", 15))


def is_international(cfg: dict, dest: str, spec: Optional[dict] = None) -> bool:
    if spec and spec.get("_international") is not None:
        return bool(spec["_international"])
    return dest in set(cfg.get("international_airports", []))


def build_inputs(
    *,
    cfg: dict,
    flight_number: str,
    orig: str,
    dest: str,
    dep_dt: datetime,
    drive_minutes: Optional[int],
    drive_source: str,
    parking_minutes: Optional[int] = None,
    security_p85: Optional[int] = None,
    security_source: Optional[str] = None,
    effective_departure: Optional[datetime] = None,
    inbound_delay_minutes: int = 0,
    checked_bag: bool = False,
    precip_or_freezing: bool = False,
    weather_source: Optional[str] = None,
    stage: str = "final",
    spec: Optional[dict] = None,
) -> dict:
    """Assemble the compute_leave_time() input dict from config + live values."""
    learned = load_learned()
    al = airline_code(flight_number)
    terminal = resolve_terminal(cfg, al, orig)
    walk_min, walk_est = resolve_walk(cfg, orig, terminal, learned)

    if security_p85 is None:
        security_p85, security_source = resolve_security(cfg, orig, dep_dt, learned)
    elif security_source is None:
        security_source = "live"

    apt = cfg.get("airports", {}).get(orig, {})
    airport_known = orig in cfg.get("airports", {})
    if parking_minutes is None:
        parking_minutes = int(apt.get("parking_minutes", 0))

    return {
        "scheduled_departure": dep_dt.isoformat(),
        "effective_departure": effective_departure.isoformat() if effective_departure else None,
        "inbound_delay_minutes": inbound_delay_minutes,
        "airport": orig,
        "airport_known": airport_known,
        "terminal": terminal,
        "buffer_minutes": int(learned.get("buffer_minutes", cfg.get("buffer_minutes", 20))),
        "gate_close_lead_minutes": resolve_gate_lead(cfg, al),
        "walk_minutes": walk_min,
        "walk_estimated": walk_est,
        "security_p85": security_p85,
        "security_source": security_source,
        "drive_minutes": drive_minutes,
        "drive_source": drive_source,
        "parking_minutes": parking_minutes,
        "checked_bag": checked_bag,
        "international": is_international(cfg, dest, spec),
        "precip_or_freezing": precip_or_freezing,
        "weather_source": weather_source,
        "stage": stage,
    }


def leave_time_for_flight(**kwargs) -> dict:
    """build_inputs(...) -> compute_leave_time(...) in one call."""
    return compute_leave_time(build_inputs(**kwargs))


if __name__ == "__main__":
    cfg = load_cfg()
    dep = datetime.fromisoformat("2026-09-15T13:40:00-05:00")
    r = leave_time_for_flight(
        cfg=cfg, flight_number="WN 100", orig="DAL", dest="ORD",
        dep_dt=dep, drive_minutes=22, drive_source="live",
    )
    print(json.dumps(r, indent=2))
    print(f"\nLeave by {r['leave_home_display']} — {one_line_reason(r)}")
