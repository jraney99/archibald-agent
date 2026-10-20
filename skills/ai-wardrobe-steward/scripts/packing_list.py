#!/usr/bin/env python3
"""One packing message, two evenings before an outbound flight. Deterministic; no model.

The user 2026-09-28. The old travel watcher looked for the words "flight/trip/travel" and
his flight events read like "WN 1234 DAL → BOS", so it never fired once. This reads the
flights themselves, pairs an outbound with its return, looks up the destination's
day-by-day forecast, sees what the calendar holds while he is there, and picks real
closet pieces — dirty ones included, flagged, because laundry day is before the trip.

Message (approved 2026-09-28):
  🧳 Packing for New York · Sep 1–8 (7 nights)
  Forecast there: 58–74°F, rain Thursday
  On the plane: …
  Pack: … (grouped)
  In the hamper now: …. Wash before you go.

Usage:
  python3 packing_list.py [--dry-run] [--today YYYY-MM-DD] [--events-json FILE] [--force]
"""
import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
CLOSET = ROOT / "profile" / "closet.json"
VOCAB = ROOT / "vocab.yaml"
sys.path.insert(0, str(ROOT.parent / "shared"))
sys.path.insert(0, str(SCRIPTS))
from data_paths import path as data_path  # noqa: E402
from event_forecast import daily_forecast  # noqa: E402
from event_triage import FETCH, FLIGHT, Triage  # noqa: E402
from outfit_brief import brief as build_brief  # noqa: E402
from proposal_caption import piece_label  # noqa: E402

CONFIG = data_path("wardrobe_travel.json")
STATE = Path(os.environ["WARDROBE_STATE_DIR"]) / "packing_state.json" if os.environ.get("WARDROBE_STATE_DIR") else data_path("packing_state.json")   # tests point this at a scratch dir
ROUTE = re.compile(r"\b([A-Z]{3})\s*(?:→|->|–|—|\bto\b)\s*([A-Z]{3})\b")
FOOTWEAR = {"sneakers", "boots", "loafers", "dress_shoes", "athletic_shoes", "specialty_footwear"}
TOPS, BOTTOMS, LAYERS = {"shirt", "t-shirt", "polo"}, {"pants", "jeans", "shorts"}, {"sweater", "vest", "jacket", "blazer"}


def load(p, default):
    return json.loads(Path(p).read_text()) if Path(p).exists() else default


def trips(events, home_airports):
    """Pair each outbound flight (leaving a home airport) with the first flight back."""
    flights = []
    for e in events:
        m = ROUTE.search(e.get("title", "")) if FLIGHT.search(e.get("title", "")) else None
        if m:
            flights.append((e.get("day"), m.group(1), m.group(2), e))
    flights.sort(key=lambda f: f[0])
    found = []
    for i, (day, origin, dest, event) in enumerate(flights):
        if origin not in home_airports:
            continue
        back = next(((d, ev) for d, o, de, ev in flights[i + 1:] if de in home_airports and d >= day), None)
        found.append({"key": f"{day}-{dest}", "out_day": day, "dest": dest, "outbound": event,
                      "back_day": back[0] if back else None, "return_event": back[1] if back else None})
    return found


def destination(trip, vocab):
    city = vocab.get("airport_cities", {}).get(trip["dest"])
    if city:
        return city
    loc = (trip.get("return_event") or {}).get("location") or ""
    return loc or trip["dest"]


def forecast_line(fc):
    if not fc:
        return "Forecast there: not available yet"
    days = fc["days"]
    lo, hi = min(d["min_f"] for d in days), max(d["max_f"] for d in days)
    rainy = [datetime.fromisoformat(d["date"]).strftime("%A") for d in days if d["rain_pct"] >= 50]
    rain = "dry" if not rainy else "rain " + (" & ".join(rainy[:2]) + ("…" if len(rainy) > 2 else ""))
    tail = f" (first {len(days)} days)" if fc.get("partial") else ""
    return f"Forecast there: {lo}–{hi}°F, {rain}{tail}"


def occasions_during(trip, events, triage, vocab):
    needed = {"casual"}
    for e in events:
        day = e.get("day", "")
        if not day or day < trip["out_day"] or (trip["back_day"] and day > trip["back_day"]) or FLIGHT.search(e.get("title", "")):
            continue
        v = triage.verdict(e)
        if v["decision"] == "propose":
            needed.add(v.get("occasion") or "smart-casual")
        elif v["decision"] in ("ask-person", "venue-check"):
            needed.add("smart-casual")
    return sorted(needed)


def choose(closet, needed, fc, nights, vocab):
    """Real pieces for the trip. Dirty ones count (laundry day comes first) and are flagged."""
    targets = {vocab["occasion_formality"][o] for o in needed if o in vocab["occasion_formality"]}
    business = any(o in ("business-casual", "business-formal") for o in needed)
    hi = max(d["max_f"] for d in fc["days"]) if fc else None
    lo = min(d["min_f"] for d in fc["days"]) if fc else None
    n = nights if nights is not None else 3
    today = date.today()

    def fits(item):
        f = item.get("formality")
        if item.get("lifecycle_state") not in ("active", "dirty") or not isinstance(f, (int, float)) or isinstance(f, bool):
            return False
        if not any(abs(f - t) <= 1 for t in targets):
            return False
        cat = item.get("category")
        if cat == "shorts" and hi is not None and hi < 78:
            return False
        if cat in TOPS and item.get("sleeve") == "long" and hi is not None and hi >= 85 and f < 3:
            return False
        if cat == "coat" and (lo is None or lo >= 45):
            return False
        return True

    def score(item):
        s = sum(2 for o in needed if o in (item.get("occasion_tags") or []))
        s += sum(1 for t in targets if abs(item.get("formality", 0) - t) <= 1)
        s += 1 if (item.get("palette_match") or {}).get("verdict") == "in-palette" else 0
        s += 1 if item.get("lifecycle_state") == "active" else 0
        try:
            s += min(3, (today - datetime.strptime(item.get("last_worn"), "%Y-%m-%d").date()).days // 30)
        except (TypeError, ValueError):
            s += 2
        return s

    pool = sorted((x for x in closet if fits(x)), key=lambda x: (-score(x), x.get("id", "")))
    picks = {"Tops": [], "Bottoms": [], "Shoes": [], "Layers": [], "Also": []}
    want = {"Tops": min(n + 1, 6), "Bottoms": min(2 + n // 3, 4), "Shoes": 2, "Layers": (2 if n >= 5 else 1) if lo is not None and lo < 62 else 0}
    for item in pool:
        cat = item.get("category")
        group = "Tops" if cat in TOPS else "Bottoms" if cat in BOTTOMS else "Shoes" if cat in FOOTWEAR else "Layers" if cat in LAYERS or cat == "coat" else None
        if group and len(picks[group]) < want[group]:
            if group == "Shoes" and picks["Shoes"] and business:       # one dressy pair, one easy pair
                dressy = any(closet_by(picks["Shoes"][0]).get("formality", 0) >= 3 for _ in [0])
                if (item.get("formality", 0) >= 3) == dressy:
                    continue
            picks[group].append(item["id"])
    if business:
        belt = next((x["id"] for x in pool if x.get("category") == "belt"), None)
        if belt:
            picks["Also"].append(belt)
    return {k: v for k, v in picks.items() if v}


def closet_by(item_id):
    return next((x for x in load(CLOSET, []) if x.get("id") == item_id), {})


def message(trip, events, vocab, triage, today):
    closet = load(CLOSET, [])
    by_id = {x.get("id"): x for x in closet}
    dest = destination(trip, vocab)
    nights = (date.fromisoformat(trip["back_day"]) - date.fromisoformat(trip["out_day"])).days if trip["back_day"] else None
    fc, _ = (None, None) if os.environ.get("WARDROBE_NO_NETWORK") == "1" else daily_forecast(dest, trip["out_day"], trip["back_day"] or (date.fromisoformat(trip["out_day"]) + timedelta(days=3)).isoformat())
    needed = occasions_during(trip, events, triage, vocab)
    picks = choose(closet, needed, fc, nights, vocab)
    # The outbound is a flight by construction; brief it as one without asking the triage
    # (which would count it as "already seen" on a second look at the same day).
    plane = build_brief(trip["outbound"], {"reason": "flight day (one board per travel day)", "kind": "flight", "occasion": "casual", "activity": "travel"})
    top = (plane.get("top_outfits") or [{}])[0].get("items", [])
    out, back = date.fromisoformat(trip["out_day"]), date.fromisoformat(trip["back_day"]) if trip["back_day"] else None
    span = f"{out.strftime('%b')} {out.day}" + (f"–{back.day if back.month == out.month else back.strftime('%b ') + str(back.day)}" if back else "")
    city = dest.split(",")[0]
    lines = [f"🧳 Packing for {city} · {span}" + (f" ({nights} night{'s' if nights != 1 else ''})" if nights else " (return not on the calendar)"),
             forecast_line(fc),
             "On the plane: " + (", ".join(piece_label(by_id.get(i)) for i in top) if top else "no complete flight-day outfit in the closet"),
             "Pack:"]
    for group, ids in picks.items():
        lines.append(f"• {group}: " + ", ".join(piece_label(by_id.get(i)) for i in ids))
    dirty = [piece_label(by_id.get(i)) for ids in picks.values() for i in ids if by_id.get(i, {}).get("lifecycle_state") == "dirty"]
    if dirty:
        lines.append("In the hamper now: " + ", ".join(dirty) + ". Wash before you go.")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--today")
    ap.add_argument("--events-json")
    ap.add_argument("--force", action="store_true", help="build for every trip found, regardless of timing or state")
    args = ap.parse_args()
    today = date.fromisoformat(args.today) if args.today else date.today()
    config = {"home_airports": ["DAL", "DFW"], "lead_days": 2, **load(CONFIG, {})}
    vocab = yaml.safe_load(VOCAB.read_text())
    if args.events_json:
        events = json.loads(Path(args.events_json).read_text()).get("events", [])
    else:
        done = subprocess.run([sys.executable, str(FETCH), "--from", today.isoformat(), "--to", (today + timedelta(days=21)).isoformat(), "--json"], capture_output=True, text=True, timeout=120)
        if done.returncode:
            sys.exit(done.stdout or done.stderr)
        events = json.loads(done.stdout).get("events", [])
    state = load(STATE, {"sent": {}})
    triage = Triage()
    out = []
    for trip in trips(events, config["home_airports"]):
        lead = (date.fromisoformat(trip["out_day"]) - today).days
        due = args.force or (trip["key"] not in state["sent"] and 0 <= lead <= config["lead_days"])
        if not due:
            continue
        text = message(trip, events, vocab, triage, today)
        out.append({"trip": trip["key"], "text": text})
        if args.dry_run:
            continue
        message_id = None
        if os.environ.get("WARDROBE_NO_SEND") != "1":     # 2026-09-28: a test once sent the user a fake trip. Never again.
            from send_telegram import send_chunk
            message_id = send_chunk(text).get("result", {}).get("message_id")
        state["sent"][trip["key"]] = {"date": today.isoformat(), "message_id": message_id}
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(state, indent=2))
    print(json.dumps({"dry_run": args.dry_run, "packing": out}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
