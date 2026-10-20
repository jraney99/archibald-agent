#!/usr/bin/env python3
"""Return upcoming attire-bearing calendar events that need an outfit proposal."""
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is required")

ROOT = Path(__file__).resolve().parent.parent
FETCH = ROOT.parent / "shared" / "fetch_calendar.py"
VOCAB = ROOT / "vocab.yaml"
PROPOSALS = ROOT / "profile" / "outfit_proposals.json"
EXPLICIT_ATTIRE = ("black tie", "formal attire", "business formal", "business casual", "smart casual", "casual attire")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from activity_policy import activity_preference, venue_aliases  # noqa: E402


def key_for(event):
    raw = "|".join(str(event.get(k, "")) for k in ("event_id", "calendar", "start_iso", "title", "description", "location"))
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def infer(text, aliases):
    lower = text.lower()
    matches = [(phrase, occasion) for phrase, occasion in aliases.items() if phrase in lower]
    return max(matches, key=lambda pair: len(pair[0])) if matches else (None, None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=2)
    args = ap.parse_args()
    result = subprocess.run([sys.executable, str(FETCH), "--days", str(args.days), "--json"], capture_output=True, text=True, timeout=60)
    if result.returncode:
        sys.exit(result.stdout or result.stderr)
    calendar = json.loads(result.stdout)
    with open(VOCAB) as f:
        vocab = yaml.safe_load(f)
    existing = json.loads(PROPOSALS.read_text()) if PROPOSALS.exists() else []
    known = {p.get("event_key") for p in existing if p.get("status") not in ("superseded", "cancelled")}
    activity_aliases = {**vocab.get("activity_aliases", {}), **venue_aliases()}
    candidates = []
    for event in calendar.get("events", []):
        if event.get("status") == "cancelled" or event.get("response_status") == "declined":
            continue
        text = " ".join(str(event.get(k, "")) for k in ("title", "description", "location"))
        phrase, occasion = infer(text, vocab.get("occasion_aliases", {}))
        if not occasion:
            continue
        event_key = key_for(event)
        if event_key in known:
            continue
        activity_phrase, activity = infer(text, activity_aliases)
        activity = activity or "general"
        occasion_inferred = phrase not in EXPLICIT_ATTIRE
        # The user 2026-09-20: no outfits for working out (gym, runs, and the simulator/range,
        # which he dresses for as workouts). A dress code written on the invite still counts.
        if occasion_inferred and activity in vocab.get("workout_activities", []):
            continue
        # A stated dress code stands; an inferred one yields to how the wearer
        # actually dresses for this activity.
        preferred = activity_preference(activity).get("occasion")
        occasion_source = "attire-text" if not occasion_inferred else "alias"
        if occasion_inferred and preferred:
            occasion, occasion_source = preferred, "activity-preference"
        event.update({"event_key": event_key, "occasion": occasion, "attire_phrase": phrase,
                      "occasion_source": occasion_source,
                      "activity": activity, "activity_phrase": activity_phrase,
                      "tentative": event.get("response_status") == "tentative",
                      "occasion_inferred": occasion_inferred})
        candidates.append(event)
    print(json.dumps({"calendar_unavailable": calendar.get("calendar_unavailable", False), "events": candidates}, indent=2))


if __name__ == "__main__":
    main()
