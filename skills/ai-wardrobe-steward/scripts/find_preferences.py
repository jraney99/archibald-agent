#!/usr/bin/env python3
"""What his 👍/👎 on scan finds add up to — the guidance the next scan reads.

  python3 find_preferences.py [--json] [--days 365]

WHY (2026-10-13): the thumbs were wired to WRITE a style signal and nothing read it back.
`learned_adjustments.py` only consumes signals whose source is "outfit-proposal-response",
so shopping feedback accumulated and changed nothing — he could have tapped fifty times and
seen the same recommendations. This reads them.

A tap is weighed by how recent it is (a year-old opinion counts for little) and aggregated
by brand, material and colour FAMILY rather than by exact shade, because "he liked a navy
merino H&B thing" generalises and "#1F2449" does not. A pull needs at least MIN_TAPS taps
before it is reported: one tap is a mood, not a preference.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from _store import load_json  # noqa: E402

SIGNALS = ROOT / "profile" / "style_signals.json"
SOURCE = "shopping-find-feedback"
HALF_LIFE_DAYS = 120      # an opinion is worth half as much four months on
MIN_TAPS = 2              # one tap is a mood
MIN_WEIGHT = 1.2          # and the weighted pull has to be worth saying


def _age_weight(when: str, today: date) -> float:
    try:
        d = datetime.strptime(when, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return 0.0
    days = max(0, (today - d).days)
    return 0.5 ** (days / HALF_LIFE_DAYS)


def summarise(signals: list, today: date = None, days: int = 365) -> dict:
    today = today or date.today()
    buckets = {k: defaultdict(lambda: {"liked": 0.0, "disliked": 0.0, "taps": 0})
               for k in ("brand", "material", "colour_family", "category")}
    taps = 0
    for s in signals:
        if s.get("source") != SOURCE or s.get("action") not in ("liked", "disliked"):
            continue
        w = _age_weight(s.get("date", ""), today)
        if w <= 0:
            continue
        taps += 1
        for field in buckets:
            value = (s.get(field) or "").strip()
            if value:
                b = buckets[field][value.lower() if field != "brand" else value]
                b[s["action"]] += w
                b["taps"] += 1
    out = {"taps": taps, "prefer": {}, "avoid": {}}
    for field, items in buckets.items():
        for value, b in items.items():
            if b["taps"] < MIN_TAPS:
                continue
            pull = b["liked"] - b["disliked"]
            if pull >= MIN_WEIGHT:
                out["prefer"].setdefault(field, []).append({"value": value, "weight": round(pull, 2), "taps": b["taps"]})
            elif pull <= -MIN_WEIGHT:
                out["avoid"].setdefault(field, []).append({"value": value, "weight": round(-pull, 2), "taps": b["taps"]})
    for side in ("prefer", "avoid"):
        for field in out[side]:
            out[side][field].sort(key=lambda x: -x["weight"])
    return out


def sentence(summary: dict) -> str:
    """One line the scan can act on, or a plain statement that there is nothing yet."""
    if not summary["taps"]:
        return "No 👍/👎 on finds yet — nothing learned from them, so judge on the profile alone."
    bits = []
    for side, verb in (("prefer", "He has liked"), ("avoid", "He has passed on")):
        parts = []
        for field in ("brand", "material", "colour_family", "category"):
            vals = [x["value"] for x in summary[side].get(field, [])][:3]
            if vals:
                parts.append(f"{field.replace('_', ' ')}: {', '.join(vals)}")
        if parts:
            bits.append(f"{verb} — " + "; ".join(parts))
    if not bits:
        return f"{summary['taps']} tap(s) so far, none repeated enough to be a preference yet."
    return " || ".join(bits)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--days", type=int, default=365)
    a = ap.parse_args(argv)
    summary = summarise(load_json(SIGNALS, []), days=a.days)
    print(json.dumps({**summary, "sentence": sentence(summary)}, indent=2) if a.json else sentence(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
