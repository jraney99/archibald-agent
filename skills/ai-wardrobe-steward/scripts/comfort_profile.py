#!/usr/bin/env python3
"""Learn and predict personal layering comfort from real conditions."""
import argparse
import json
from datetime import date
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent.parent
PATH = ROOT / "profile" / "comfort_profile.yaml"
CHOICES = ["shirt-only", "light-layer", "jacket", "sweater-and-jacket"]


def load():
    return yaml.safe_load(PATH.read_text()) if PATH.exists() else {"version": 1, "runs_temperature": "unknown", "observations": []}


def save(x):
    x["last_updated"] = date.today().isoformat(); PATH.write_text(yaml.safe_dump(x, sort_keys=False))


def distance(o, args):
    score = abs(float(o["feels_like_f"]) - args.feels_like_f)
    score += min(abs(float(o.get("wind_mph", 0)) - args.wind_mph), 20) * .35
    score += min(abs(int(o.get("outdoor_minutes", 0)) - args.outdoor_minutes), 120) * .05
    if bool(o.get("rain")) != args.rain: score += 8
    if o.get("activity", "light") != args.activity: score += 4
    return score


def main():
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="command", required=True)
    obs = sub.add_parser("observe")
    pred = sub.add_parser("predict")
    for p in (obs, pred):
        p.add_argument("--feels-like-f", type=float, required=True); p.add_argument("--wind-mph", type=float, default=0)
        p.add_argument("--outdoor-minutes", type=int, default=15); p.add_argument("--activity", choices=["sedentary","light","active"], default="light")
        p.add_argument("--rain", action="store_true"); p.add_argument("--context", default="general")
    obs.add_argument("--preference", choices=CHOICES, required=True); obs.add_argument("--note", default="")
    args = ap.parse_args(); profile = load(); observations = profile.setdefault("observations", [])
    if args.command == "observe":
        observations.append({"date": date.today().isoformat(), "feels_like_f": args.feels_like_f, "wind_mph": args.wind_mph,
                             "outdoor_minutes": args.outdoor_minutes, "activity": args.activity, "rain": args.rain,
                             "context": args.context, "preference": args.preference, "note": args.note, "source": "explicit"})
        profile["observations"] = observations[-60:]; save(profile); print("Comfort preference recorded."); return
    ranked = sorted(observations, key=lambda o: distance(o, args))[:5]
    if not ranked:
        print(json.dumps({"prediction": None, "confidence": 0, "question": f"It will feel like {args.feels_like_f:g}°F with {args.outdoor_minutes} minutes outside. Would you prefer a shirt only, light layer, jacket, or sweater and jacket?"}, indent=2)); return
    weights = {}
    for o in ranked:
        w = 1 / (1 + distance(o, args)); weights[o["preference"]] = weights.get(o["preference"], 0) + w
    winner = max(weights, key=weights.get); confidence = round(100 * weights[winner] / sum(weights.values()))
    print(json.dumps({"prediction": winner, "confidence": confidence, "nearest_evidence": ranked}, indent=2))


if __name__ == "__main__": main()
