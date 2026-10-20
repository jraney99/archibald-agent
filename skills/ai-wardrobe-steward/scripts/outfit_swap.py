#!/usr/bin/env python3
"""Swap ONE piece of a proposed outfit for the next-best piece in that slot. Pure decision + record.

The user 2026-09-28: "tap a piece on the board to swap just that piece". The other pieces
stay; the replacement comes from the same candidate pool the board was scored from
(same occasion, activity, forecast and flags), minus anything already tried. The
board photo is re-rendered; sending the edit is the caller's job (outfit_callback.py).

Usage:
  python3 outfit_swap.py --id <proposal id> --slot top|bottoms|shoes
Prints JSON {"old": id, "new": id, "old_label": ..., "new_label": ...} or {"error": ...} (exit 1).
"""
import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
PROPOSALS = ROOT / "profile" / "outfit_proposals.json"
CLOSET = ROOT / "profile" / "closet.json"
SIGNALS = ROOT / "profile" / "style_signals.json"
SLOT = {"top": "base_top", "bottoms": "bottoms", "shoes": "footwear"}

sys.path.insert(0, str(SCRIPTS))
from learned_adjustments import learned  # noqa: E402
from outfit_score import item_score, recently_proposed  # noqa: E402
from proposal_caption import piece_label  # noqa: E402


def load(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def candidates(proposal, slot):
    meta = proposal.get("meta") or {}
    scoring = meta.get("scoring") or {}
    cmd = [sys.executable, str(SCRIPTS / "outfit_candidates.py"), "--occasion", scoring.get("occasion") or proposal["occasion"],
           "--activity", scoring.get("activity") or proposal.get("activity") or "general"]
    if scoring.get("feels_like_f") is not None:
        cmd += ["--feels-like-f", str(scoring["feels_like_f"])]
    if scoring.get("outdoor"):
        cmd += ["--outdoor"]
    if scoring.get("explicit_occasion"):
        cmd += ["--explicit-occasion"]
    done = subprocess.run(cmd, capture_output=True, text=True)
    if done.returncode:
        raise RuntimeError((done.stdout + done.stderr).strip()[:300])
    data = json.loads(done.stdout)
    return data["occasion"], data["candidates"].get(slot, [])


def choose(proposal, slot_name):
    slot = SLOT[slot_name]
    vocab = yaml.safe_load((ROOT / "vocab.yaml").read_text())
    slot_categories = set(vocab["outfit_slots"][slot])
    closet = {x.get("id"): x for x in load(CLOSET, [])}
    current = next((i for i in proposal["items"] if closet.get(i, {}).get("category") in slot_categories), None)
    if not current:
        return None, f"This board has no {slot_name} to swap."
    occasion, pool = candidates(proposal, slot)
    activity = proposal.get("activity") or "general"
    tried = set(proposal.get("swapped_out", [])) | set(proposal["items"])
    scoring_meta = (proposal.get("meta") or {}).get("scoring") or {}
    lessons = learned(activity, scoring_meta.get("feels_like_f"))["items"]
    proposed = recently_proposed()
    ranked = []
    for item in pool:
        if item["id"] in tried:
            continue
        score = item_score(item, occasion, activity)[0] - min(9, 3 * proposed.get(item["id"], 0)) + sum(w["delta"] for w in lessons.get(item["id"], []))
        ranked.append((score, item["id"]))
    if not ranked:
        return None, f"No other {slot_name} fits this event — that's the only one."
    ranked.sort(key=lambda x: (-x[0], x[1]))
    return {"old": current, "new": ranked[0][1], "old_label": piece_label(closet.get(current)), "new_label": piece_label(closet.get(ranked[0][1]))}, None


def apply(proposal_id, slot_name):
    proposals = load(PROPOSALS, [])
    proposal = next((p for p in proposals if p.get("id") == proposal_id), None)
    if not proposal:
        return None, f"No outfit proposal with id '{proposal_id}'."
    swap, problem = choose(proposal, slot_name)
    if not swap:
        return None, problem
    items = [swap["new"] if i == swap["old"] else i for i in proposal["items"]]
    # the belt answers to the bottoms and the shoes, so a swap of either re-picks it
    # (2026-10-13): swapping shorts for joggers must not leave a belt on the board.
    if slot_name in ("bottoms", "shoes"):
        try:
            from belt_pick import pick as pick_belt
            closet_items = load(CLOSET, [])
            kept = [i for i in items if next((x for x in closet_items if x.get("id") == i), {}).get("category") != "belt"]
            belt = pick_belt(kept, closet_items, proposal.get("activity") or "general", proposal.get("occasion") or "casual")
            items = kept + ([belt["belt"]] if belt.get("belt") else [])
        except Exception:  # noqa: BLE001 - a swap must never fail over an accessory
            pass
    board = subprocess.run([sys.executable, str(SCRIPTS / "outfit_board.py"), *items, "--title", proposal["event_title"], "--bare"], capture_output=True, text=True)
    if board.returncode or "MEDIA:" not in board.stdout:
        return None, "The new board could not be rendered; nothing was changed."
    proposal["items"] = items
    proposal["board_path"] = board.stdout.strip().split("MEDIA:", 1)[1]
    proposal.setdefault("swapped_out", []).append(swap["old"])
    proposal.setdefault("meta", {}).setdefault("swaps", []).append({"slot": slot_name, **swap, "at": datetime.now().astimezone().isoformat(timespec="seconds")})
    signals = load(SIGNALS, [])
    signals.append({"date": datetime.now().date().isoformat(), "source": "outfit-proposal-response", "confidence": 30, "action": "swapped",
                    "proposal_id": proposal_id, "occasion": proposal.get("occasion"), "activity": proposal.get("activity", "general"),
                    "feels_like_f": proposal.get("feels_like_f"), "items": [swap["old"]], "note": f"swapped for {swap['new']}"})
    SIGNALS.write_text(json.dumps(signals, indent=2))
    PROPOSALS.write_text(json.dumps(proposals, indent=2))
    return swap, None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--id", required=True)
    ap.add_argument("--slot", choices=list(SLOT), required=True)
    args = ap.parse_args()
    swap, problem = apply(args.id, args.slot)
    if problem:
        print(json.dumps({"error": problem}))
        sys.exit(1)
    print(json.dumps(swap))


if __name__ == "__main__":
    main()
