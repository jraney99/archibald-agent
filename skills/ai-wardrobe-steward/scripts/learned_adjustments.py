#!/usr/bin/env python3
"""Turn behavioural evidence (button taps, confirmed wears) into score adjustments.

Nothing computed here is stored. The evidence is the record; the adjustment is
recomputed on every request and fades with age, so one bad morning nudges the
next proposal without ever hardening into a rule. Durable rules are a separate,
explicit step (`activity_policy.py prefer`, proposed by `rule_proposals.py`).

Every adjustment carries a plain-language `because`, so "why did you stop
suggesting that?" always has a real answer (references/02).

Usage:
  python3 learned_adjustments.py --activity golf-simulator [--feels-like-f 85]
"""
import argparse
import json
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROFILE = ROOT / "profile"

FORMALITY_TAP = -8      # the dressiest (or most casual) pieces of an outfit called too formal (too casual)
COMFORT_TAP = -8        # non-footwear pieces of an outfit called too warm / too cold, in similar weather
REJECTED_COMBO = -15    # the exact combination that was skipped or swapped for another
SWAPPED_OUT = -5        # one piece he tapped out of a board (2026-09-28)
WORN_FOR_ACTIVITY = 5   # each confirmed wear for this same activity
WORN_CAP = 12
COMFORT_BAND_F = 5
HORIZON_DAYS = {"formality": 120, "comfort": 365, "combo": 60, "worn": 180, "swap": 60}


def load(name, default):
    path = PROFILE / name
    return json.loads(path.read_text()) if path.exists() else default


def fade(when, horizon, today):
    try:
        age = (today - datetime.strptime(str(when)[:10], "%Y-%m-%d").date()).days
    except ValueError:
        return 0.0
    return max(0.0, 1 - age / horizon) if age >= 0 else 0.0


def learned(activity, feels_like_f=None, today=None):
    """Return {"items": {id: [{"delta", "because"}]}, "combos": [{"items", "delta", "because"}]} for one activity."""
    today = today or date.today()
    closet = {x.get("id"): x for x in load("closet.json", [])}
    items, combos = {}, []

    def note(item_id, delta, because):
        if round(delta):
            items.setdefault(item_id, []).append({"delta": round(delta), "because": because})

    for s in load("style_signals.json", []):
        if s.get("source") != "outfit-proposal-response" or s.get("activity", "general") != activity:
            continue
        action, ids, when = s.get("action"), s.get("items", []), s.get("date", "")
        if action in ("too-formal", "too-casual"):
            levels = [closet.get(i, {}).get("formality") for i in ids]
            known = [v for v in levels if isinstance(v, (int, float)) and not isinstance(v, bool)]
            edge = (max if action == "too-formal" else min)(known) if known else None
            for i, level in zip(ids, levels):
                if edge is None or level == edge:
                    note(i, FORMALITY_TAP * fade(when, HORIZON_DAYS["formality"], today), f"called {action.replace('-', ' ')} for {activity} on {when}")
        elif action in ("too-warm", "too-cold") and feels_like_f is not None and s.get("feels_like_f") is not None:
            then = float(s["feels_like_f"])
            similar = feels_like_f >= then - COMFORT_BAND_F if action == "too-warm" else feels_like_f <= then + COMFORT_BAND_F
            if similar:
                for i in ids:
                    if closet.get(i, {}).get("category") not in ("sneakers", "boots", "loafers", "dress_shoes", "athletic_shoes", "specialty_footwear"):
                        note(i, COMFORT_TAP * fade(when, HORIZON_DAYS["comfort"], today), f"called {action.replace('-', ' ')} at {then:g}F on {when}")
        elif action == "swapped":
            for i in ids:
                note(i, SWAPPED_OUT * fade(when, HORIZON_DAYS["swap"], today), f"you swapped it out of a {activity} board on {when}")
        elif action in ("skip", "alternate"):
            delta = REJECTED_COMBO * fade(when, HORIZON_DAYS["combo"], today)
            if round(delta):
                combos.append({"items": sorted(ids), "delta": round(delta), "because": f"this exact combination was passed over for {activity} on {when}"})

    worn = {}
    for h in load("outfit_history.json", []):
        if h.get("activity") == activity:
            for i in h.get("items", []):
                worn[i] = worn.get(i, 0) + WORN_FOR_ACTIVITY * fade(h.get("date", ""), HORIZON_DAYS["worn"], today)
    for i, total in worn.items():
        note(i, min(WORN_CAP, total), f"you have actually worn it for {activity}")
    return {"items": items, "combos": combos}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--activity", default="general")
    ap.add_argument("--feels-like-f", type=float)
    args = ap.parse_args()
    print(json.dumps(learned(args.activity, args.feels_like_f), indent=2))


if __name__ == "__main__":
    main()
