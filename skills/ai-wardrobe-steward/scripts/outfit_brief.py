#!/usr/bin/env python3
"""Gather everything for an outfit BEFORE the model is involved, and hand it a finished brief.

The 2026-09-19 proposal took the agent 24 tool calls, 4 of them guessed commands
that failed; it fetched weather for a city it picked itself and never passed the
temperature to the scorer. Nothing in that run needed judgment except the last
step. This does the rest deterministically:

  calendar -> event_triage (does this event deserve an outfit?) -> event_forecast
  (its place and hour) -> is he travelling? -> outfit_score with every flag set
  -> top 3 complete outfits, the learned reasons, his guidance for the activity.

The model's remaining job: pick among the 3 with style judgment and write the
caption. No commands left to guess.

This script itself only builds and prints briefs; calendar_outfit_watch.py acts on them.

Usage:
  python3 outfit_brief.py [--days 2] [--from YYYY-MM-DD --to YYYY-MM-DD] [--events-json FILE]
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
from event_forecast import forecast  # noqa: E402
from event_triage import FETCH, Triage  # noqa: E402
from calendar_outfit_candidates import key_for  # noqa: E402


def away_from_home():
    try:
        sys.path.insert(0, str(ROOT.parent / "shared"))
        from current_location import is_away_from_home
        return bool(is_away_from_home())
    except Exception:  # noqa: BLE001
        return None


def score(verdict, weather):
    cmd = [sys.executable, str(SCRIPTS / "outfit_score.py"), "--occasion", verdict["occasion"], "--activity", verdict["activity"], "--limit", "3"]
    if weather:
        cmd += ["--feels-like-f", str(weather["feels_like_f"])]
    if verdict.get("outdoor"):
        cmd += ["--outdoor"]
    if verdict.get("explicit_occasion"):
        cmd += ["--explicit-occasion"]
    done = subprocess.run(cmd, capture_output=True, text=True)
    if done.returncode:
        return {"outfits": [], "warnings": [f"scorer failed: {(done.stdout + done.stderr).strip()[:300]}"]}, cmd
    return json.loads(done.stdout), cmd


def brief(event, verdict):
    weather, why_not = (None, "network disabled") if os.environ.get("WARDROBE_NO_NETWORK") == "1" else forecast(event.get("location", ""), event.get("start_iso"))
    ranked, cmd = score(verdict, weather)
    return {
        "event_key": key_for(event),
        "event": {k: event.get(k) for k in ("event_id", "title", "start_fmt", "start_iso", "location")},
        "extra": {k: verdict[k] for k in ("person", "venue_key", "venue_name", "matched_phrase") if k in verdict},
        "why_an_outfit": verdict["reason"], "kind": verdict["kind"],
        "occasion": ranked.get("occasion", verdict["occasion"]), "activity": verdict["activity"],
        "forecast": weather, "forecast_missing_because": None if weather else why_not,
        "away_from_home": away_from_home(),
        "top_outfits": ranked.get("outfits", []),
        "notes_for_the_stylist": ranked.get("warnings", []),
        "scorer_command": " ".join(cmd[1:]),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=2)
    ap.add_argument("--from", dest="frm"); ap.add_argument("--to"); ap.add_argument("--events-json")
    args = ap.parse_args()
    if args.events_json:
        calendar = json.loads(Path(args.events_json).read_text())
    else:
        cmd = [sys.executable, str(FETCH), "--json"] + (["--from", args.frm, "--to", args.to] if args.frm and args.to else ["--days", str(args.days)])
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if done.returncode:
            sys.exit(done.stdout or done.stderr)
        calendar = json.loads(done.stdout)
    triage = Triage()
    out = {"calendar_unavailable": calendar.get("calendar_unavailable", False), "briefs": [], "needs_answer": []}
    for event in calendar.get("events", []):
        verdict = triage.verdict(event)
        if verdict["decision"] == "propose":
            out["briefs"].append(brief(event, verdict))
        elif verdict["decision"] in ("ask-person", "venue-check"):
            out["needs_answer"].append({"event": {k: event.get(k) for k in ("event_id", "title", "start_fmt", "start_iso", "location")}, "verdict": verdict})
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
