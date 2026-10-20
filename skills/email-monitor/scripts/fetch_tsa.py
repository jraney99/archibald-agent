#!/usr/bin/env python3
"""
Live TSA security-checkpoint wait times, with a documented static fallback.

Only useful close to departure (the T-3h and T-90m runs) — a "right now" wait
time is meaningless at booking time, so daemon.py does not call this.

Sources, in order:

  1. tsawaittimes.com API   — requires a paid subscription key in
                              travel_config.json as "tsawaittimes_key".
                              Crowd-sourced; good for ATL/LAX/ORD, thin for
                              small checkpoints like DAL. Terminal-specific
                              where the airport has multiple checkpoints.
  2. legacy MyTSA endpoint  — apps.tsa.dhs.gov. TSA 302-redirects this to a
                              "use the app" page as of 2026, so it almost
                              always fails; kept as a free long shot in case
                              it ever returns.
  3. static fallback        — per-airport p85 from travel_config.json
                              (airports.<CODE>.security_fallback), by
                              time-of-day bucket. This is what ships today.

`get_security_p85(cfg, airport, dep_dt, terminal="")` always returns a
`(p85_minutes, source)` tuple and never raises. `source` is one of
"live_tsawaittimes", "live_mytsa", "static".
"""
from __future__ import annotations

import json
import math
import statistics
import sys
import urllib.request
from datetime import datetime
from typing import Optional

sys.path.insert(0, "~/.openclaw/workspace/Skills/email-monitor/scripts")
from airport_timing import resolve_security as _resolve_security


HTTP_TIMEOUT = 8


def _http_json(url: str) -> Optional[dict]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "openclaw-airport-timing/1"})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            if r.status != 200:
                return None
            return json.loads(r.read())
    except Exception:
        return None


def _p85_from_samples(samples: list[float]) -> Optional[int]:
    vals = [float(s) for s in samples if s is not None and float(s) >= 0]
    if not vals:
        return None
    if len(vals) == 1:
        return max(1, math.ceil(vals[0]))
    vals.sort()
    # statistics.quantiles needs n>=2; take the 85th percentile.
    try:
        q = statistics.quantiles(vals, n=20, method="inclusive")  # 5-pt steps
        p85 = q[16]  # 85th percentile
    except Exception:
        p85 = vals[-1]
    return max(1, math.ceil(p85))


# ── source 1: tsawaittimes.com ────────────────────────────────────────────
def fetch_tsawaittimes(cfg: dict, airport: str, terminal: str = "") -> Optional[int]:
    key = cfg.get("tsawaittimes_key", "")
    if not key:
        return None
    data = _http_json(f"https://www.tsawaittimes.com/api/airport/{key}/{airport}/json")
    if not isinstance(data, dict):
        return None

    # The payload carries a current reading ("rightnow") plus hourly estimates
    # and recent user reports. Build a p85 over whatever samples are present so
    # a single lucky-low "rightnow" doesn't undersize the buffer.
    samples: list[float] = []
    rn = data.get("rightnow")
    if isinstance(rn, (int, float)):
        samples.append(float(rn))
    elif isinstance(rn, dict) and isinstance(rn.get("wait"), (int, float)):
        samples.append(float(rn["wait"]))

    hourly = data.get("estimated_hourly_times") or data.get("estimated") or {}
    if isinstance(hourly, dict):
        samples += [float(v) for v in hourly.values() if isinstance(v, (int, float))]
    elif isinstance(hourly, list):
        samples += [float(v) for v in hourly if isinstance(v, (int, float))]

    reported = data.get("user_reported") or []
    if isinstance(reported, list):
        for entry in reported:
            w = entry.get("wait") if isinstance(entry, dict) else entry
            if isinstance(w, (int, float)):
                samples.append(float(w))

    return _p85_from_samples(samples)


# ── source 2: legacy MyTSA (usually dead) ─────────────────────────────────
def fetch_mytsa(airport: str) -> Optional[int]:
    data = _http_json(
        f"https://apps.tsa.dhs.gov/MyTSAWebService/GetWaitTimes.ashx?ap={airport}&output=json"
    )
    if not isinstance(data, dict):
        return None
    waits = data.get("WaitTimes") or []
    samples = []
    for w in waits if isinstance(waits, list) else []:
        v = w.get("WaitTime") if isinstance(w, dict) else None
        try:
            samples.append(float(v))
        except (TypeError, ValueError):
            pass
    return _p85_from_samples(samples)


# ── source 3: static fallback ────────────────────────────────────────────
def static_p85(cfg: dict, airport: str, dep_dt: datetime) -> int:
    """Per-airport p85 seed (or learned value) from airport_timing."""
    p85, _ = _resolve_security(cfg, airport, dep_dt)
    return p85


def get_security_p85(cfg: dict, airport: str, dep_dt: datetime, terminal: str = "") -> tuple[int, str]:
    """Best available p85 security wait. Never raises."""
    try:
        live = fetch_tsawaittimes(cfg, airport, terminal)
        if live is not None:
            return live, "live_tsawaittimes"
    except Exception:
        pass
    # The legacy MyTSA endpoint is 302-dead as of 2026; only try it if the
    # config explicitly opts in, so we don't eat an 8s timeout every run.
    if cfg.get("try_mytsa"):
        try:
            live = fetch_mytsa(airport)
            if live is not None:
                return live, "live_mytsa"
        except Exception:
            pass
    return static_p85(cfg, airport, dep_dt), "static"


if __name__ == "__main__":
    import sys

    sys.path.insert(0, "~/.openclaw/workspace/Skills/email-monitor/scripts")
    from airport_timing import load_cfg

    ap = sys.argv[1] if len(sys.argv) > 1 else "DAL"
    when = datetime.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else datetime.now()
    p85, src = get_security_p85(load_cfg(), ap, when)
    print(f"{ap}: p85 security wait = {p85} min  (source: {src})")
