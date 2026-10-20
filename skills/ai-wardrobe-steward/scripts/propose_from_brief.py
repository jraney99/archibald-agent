#!/usr/bin/env python3
"""The ONE command the stylist runs per event: choose an outfit from a finished brief and send it.

Everything else (which event, forecast, scoring, flags) was decided before the model
was involved (outfit_brief.py). The model contributes exactly two things — which of
the three ranked outfits, and a sentence or two on why — so there is nothing left to
guess: no flags, no ids, no weather, no second script.

Usage:
  python3 propose_from_brief.py --briefs <file> --index 0 --choice 1 --reason "Crisp white shirt with grey trousers; add the navy blazer."
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))
from proposal_caption import forecast_line  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--briefs", required=True)
    ap.add_argument("--index", type=int, required=True)
    ap.add_argument("--choice", type=int, choices=[1, 2, 3], required=True)
    ap.add_argument("--reason", required=True)
    args = ap.parse_args()
    briefs = json.loads(Path(args.briefs).read_text())
    if not 0 <= args.index < len(briefs):
        sys.exit(f"No brief at index {args.index}; the file has {len(briefs)}.")
    brief = briefs[args.index]
    outfits = brief.get("top_outfits", [])
    if args.choice > len(outfits):
        sys.exit(f"Brief {args.index} has only {len(outfits)} outfit(s); choose 1-{len(outfits)}.")
    reason = " ".join(args.reason.split())
    # 2026-10-13: was 10-400, and the model used all of it — a 50-word paragraph that the
    # new caption could only truncate. The board shows the clothes; this says the one thing
    # the photos cannot ("shorts today, it's 94F").
    if not 10 <= len(reason) <= 140:
        sys.exit("--reason must be ONE short plain sentence, 10-140 characters — say the one thing "
                 "the photos cannot. Not a description of the clothes.")
    event, weather = brief["event"], brief.get("forecast")
    meta = {"why": brief["why_an_outfit"], "kind": brief["kind"], "forecast": weather, **brief.get("extra", {}),
            "scoring": {"occasion": brief["occasion"], "activity": brief["activity"], "feels_like_f": (weather or {}).get("feels_like_f"),
                        "outdoor": "--outdoor" in brief.get("scorer_command", ""), "explicit_occasion": "--explicit-occasion" in brief.get("scorer_command", "")}}
    # 2026-10-13: the engine fills top/bottoms/shoes only, so no board ever named a belt
    # ("should i not be wearing a belt?" — the user). Chosen after the outfit, from the shoes.
    items = list(outfits[args.choice - 1]["items"])
    try:
        from belt_pick import pick as pick_belt
        from _store import load_json
        belt = pick_belt(items, load_json(ROOT / "profile" / "closet.json", []), brief["activity"], brief["occasion"])
        if belt.get("belt"):
            items.append(belt["belt"])
            meta["belt"] = {"id": belt["belt"], "why": belt["why"]}
    except Exception as exc:  # noqa: BLE001 - a board must never fail over an accessory
        meta["belt_error"] = str(exc)[:120]
    create = [sys.executable, str(SCRIPTS / "outfit_proposals.py"), "create", "--event-key", brief["event_key"], "--event-id", event.get("event_id") or "",
              "--event-title", event["title"], "--event-start", event["start_iso"], "--occasion", brief["occasion"], "--activity", brief["activity"],
              "--items", ",".join(items), "--reason", reason, "--weather", forecast_line(meta), "--meta", json.dumps(meta)]
    if weather:
        create += ["--feels-like-f", str(weather["feels_like_f"])]
    made = subprocess.run(create, capture_output=True, text=True)
    if made.returncode:
        sys.exit(f"Proposal not created: {(made.stdout + made.stderr).strip()[:400]}")
    proposal_id = json.loads(made.stdout)["id"]
    if os.environ.get("WARDROBE_NO_SEND") == "1":
        print(json.dumps({"created": proposal_id, "sent": False}))
        return
    sent = subprocess.run([sys.executable, str(SCRIPTS / "send_outfit_proposal.py"), "--id", proposal_id], capture_output=True, text=True)
    if sent.returncode:
        sys.exit(f"Proposal {proposal_id} was created but NOT sent: {(sent.stdout + sent.stderr).strip()[:300]}")
    print(json.dumps({"created": proposal_id, "sent": True, **json.loads(sent.stdout)}))


if __name__ == "__main__":
    main()
