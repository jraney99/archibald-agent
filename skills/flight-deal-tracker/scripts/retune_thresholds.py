#!/usr/bin/env python3
"""
Retune opportunistic-route round-trip thresholds from observed price history.

The seed thresholds (LA $320 / NYC $360 / SF $330 / Austin $200, set
2026-08-29) were each ~10-15% below a SINGLE first sample. Once
price_history.json has a few weeks of round-trip observations per route,
this recomputes each threshold as "meaningfully cheaper than typical" —
the PCTL-th percentile of recent round-trip prices — so an alert means a
genuinely good fare rather than firing constantly or never.

Guardrails (a bad computation must not wreck alerting):
  - needs >= MIN_SAMPLES distinct observations in the last LOOKBACK_DAYS
  - never moves a threshold more than MAX_STEP_PCT in one run
  - never sets it below the cheapest observed price (that fare exists — you
    want to hear about it again)

Runs monthly on cron with --apply; also useful by hand:
    python3 retune_thresholds.py            # report + Telegram, no writes
    python3 retune_thresholds.py --apply    # write new thresholds + Telegram
    python3 retune_thresholds.py --dry-run  # print only, no Telegram, no writes
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _state_lock import locked_state  # noqa: E402

SKILL_DIR = Path(__file__).parent.parent
WATCHLIST_FILE = SKILL_DIR / "config" / "watchlist.json"
HISTORY_FILE = SKILL_DIR / "config" / "price_history.json"
SEND_TELEGRAM = SKILL_DIR.parent / "shared" / "send_telegram.py"

LOOKBACK_DAYS = 45
MIN_DISTINCT_DAYS = 10   # need observations across ~2 weeks, not just N snapshots
PCTL = 25               # target: a fare in the cheapest ~quarter of what's typical
MAX_STEP_PCT = 0.35     # cap one run's move so a noisy window can't swing it wildly
ROUND_TO = 5


def percentile(values: list[float], pct: float) -> float:
    s = sorted(values)
    if not s:
        return 0.0
    if len(s) == 1:
        return float(s[0])
    idx = pct / 100.0 * (len(s) - 1)
    lo = int(idx)
    frac = idx - lo
    if lo + 1 >= len(s):
        return float(s[-1])
    return s[lo] * (1 - frac) + s[lo + 1] * frac


def round_to(x: float, step: int) -> int:
    return int(round(x / step) * step)


def suggest_for_route(route: dict, entries: list[dict], today: date) -> dict:
    """Return a dict describing the suggested threshold + why (or why not)."""
    rid = route["id"]
    current = route.get("max_price_roundtrip")
    if current is None and route.get("max_price_oneway") is not None:
        current = int(route["max_price_oneway"]) * 2
    cutoff = (today - timedelta(days=LOOKBACK_DAYS)).isoformat()

    # NONSTOP rows only, and only rows with a real fare. A threshold must be
    # computed from the same universe of fares that can trigger an alert --
    # check_deals.py ignores connections, so pricing off them would set a
    # target below the nonstop floor and the route would just go silent.
    rows = [
        e for e in entries
        if e.get("kind") == "opportunistic" and e.get("route_id") == rid
        and e.get("trip_type") == "roundtrip" and e.get("date_checked", "") >= cutoff
        and isinstance(e.get("price"), (int, float)) and e["price"] > 0
        and (e.get("stops") or 0) == 0
    ]
    prices = [e["price"] for e in rows]
    distinct_days = len({e["date_checked"] for e in rows})

    out = {"route_id": rid, "label": route.get("label", rid), "current": current,
           "samples": len(prices), "days": distinct_days, "suggested": None, "reason": ""}

    if distinct_days < MIN_DISTINCT_DAYS:
        d = "day" if distinct_days == 1 else "days"
        out["reason"] = f"only {distinct_days} {d} of data (need {MIN_DISTINCT_DAYS}) — leaving as is"
        return out

    raw = percentile(prices, PCTL)
    floor = min(prices) * 0.85          # never below a fare that actually exists
    target = max(raw, floor)

    if current:
        lo, hi = current * (1 - MAX_STEP_PCT), current * (1 + MAX_STEP_PCT)
        target = min(max(target, lo), hi)

    suggested = round_to(target, ROUND_TO)
    out["suggested"] = suggested
    out["stats"] = {"min": round(min(prices)), "p25": round(percentile(prices, 25)),
                    "median": round(percentile(prices, 50)), "max": round(max(prices))}
    if current == suggested:
        out["reason"] = "already well-tuned"
    else:
        out["reason"] = (f"p{PCTL} of {len(prices)} obs = ${round(raw)} "
                         f"(range ${round(min(prices))}-${round(max(prices))})")
    return out


def build_message(results: list[dict], applied: bool) -> str:
    changed = [r for r in results if r["suggested"] and r["suggested"] != r["current"]]
    lines = ["✈️ Flight-deal threshold retune" + (" — applied" if applied else " — proposal")]
    for r in results:
        if r in changed:
            lines.append(f"• {r['label']}: ${r['current']} → ${r['suggested']}  ({r['reason']})")
        else:
            lines.append(f"• {r['label']}: ${r['current']} unchanged ({r['reason']})")
    if not changed:
        lines.append("\nNothing to change.")
    elif not applied:
        # 2026-10-07: this told the user to run a python3 invocation. A message to him asks
        # or tells; it never hands him a command.
        lines.append("\nTell me and I'll apply these, or name a route to set it yourself.")
    lines.append("🎩")
    return "\n".join(lines)


def send_telegram(text: str) -> None:
    subprocess.run(["python3", str(SEND_TELEGRAM), "--text", text],
                   capture_output=True, text=True, timeout=20)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Write new thresholds to watchlist.json")
    ap.add_argument("--dry-run", action="store_true", help="Print only — no Telegram, no writes")
    args = ap.parse_args()

    watchlist = json.loads(WATCHLIST_FILE.read_text())
    history = json.loads(HISTORY_FILE.read_text()).get("entries", [])
    today = date.today()

    results = [suggest_for_route(r, history, today)
               for r in watchlist.get("opportunistic_routes", [])]

    changed = [r for r in results if r["suggested"] and r["suggested"] != r["current"]]

    if args.apply and changed and not args.dry_run:
        with locked_state(WATCHLIST_FILE, default=watchlist) as wl:
            by_id = {r["route_id"]: r for r in changed}
            for route in wl.get("opportunistic_routes", []):
                if route["id"] in by_id:
                    route["max_price_roundtrip"] = by_id[route["id"]]["suggested"]
                    route.pop("max_price_oneway", None)

    applied = bool(args.apply and changed)
    msg = build_message(results, applied=applied)
    print(json.dumps({"results": results, "applied": applied}, indent=2), file=sys.stderr)

    if args.dry_run:
        print("--- would send ---\n" + msg)
    elif changed:
        # Silent when there's nothing to change — matches the skill's
        # "only speak when there's something to say" contract.
        send_telegram(msg)


if __name__ == "__main__":
    main()
