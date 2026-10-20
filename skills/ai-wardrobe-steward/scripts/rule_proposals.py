#!/usr/bin/env python3
"""Offer to turn a repeated correction into a durable rule — never do it silently.

Layer 2 of the learning loop (references/18). One "too formal" tap only nudges
scores (learned_adjustments.py). The SECOND one for the same activity, on a
different outfit, within 60 days is a pattern worth asking about: this sends one
message with a one-tap answer, in place of the usual "Recorded as too formal"
reply so he never gets two messages for one tap. Mirrors the finance skill's
"You've categorized X twice now" prompt.

Wording and trigger approved by the user 2026-09-19 (Automation rollout gate).

Usage:
  python3 rule_proposals.py check --activity golf-simulator --action too-formal --occasion smart-casual
  python3 rule_proposals.py resolve --id <12hex> --answer accept|decline
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
STORE = ROOT / "profile" / "rule_proposals.json"
SIGNALS = ROOT / "profile" / "style_signals.json"
VOCAB = ROOT / "vocab.yaml"
WINDOW_DAYS = 60
CHAT = "1000000001"

sys.path.insert(0, str(SCRIPTS))
from activity_policy import activity_preference  # noqa: E402

# One step along the formality scale. Nothing sensible sits below "casual" unless
# the activity is physical, where the step down is workout clothes.
MORE_CASUAL = {"black-tie": "business-formal", "business-formal": "business-casual",
               "business-casual": "casual", "smart-casual": "casual"}
MORE_FORMAL = {"loungewear": "casual", "athletic": "casual", "casual": "smart-casual",
               "smart-casual": "business-formal", "business-casual": "business-formal", "business-formal": "black-tie"}
DIRECTION = {"too-formal": ("too formal", "more casual"), "too-casual": ("too casual", "more formal")}


def load(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def label(activity):
    return activity.replace("-", " ")


def short(day):
    parsed = datetime.strptime(day[:10], "%Y-%m-%d")
    return f"{parsed.strftime('%b')} {parsed.day}"


def next_tier(activity, action, current):
    if action == "too-formal":
        if current == "casual":
            with open(VOCAB) as f:
                physical = yaml.safe_load(f).get("physical_activities", [])
            return "athletic" if activity in physical else None
        return MORE_CASUAL.get(current)
    return MORE_FORMAL.get(current)


def check(activity, action, occasion, today=None):
    """Return the proposal to send, or None. Pure: reads evidence, writes nothing."""
    today = today or date.today()
    if action not in DIRECTION or not activity or activity == "general":
        return None
    recent = {}
    for s in load(SIGNALS, []):
        if s.get("source") != "outfit-proposal-response" or s.get("activity") != activity or s.get("action") != action:
            continue
        try:
            age = (today - datetime.strptime(str(s.get("date", ""))[:10], "%Y-%m-%d").date()).days
        except ValueError:
            continue
        if 0 <= age <= WINDOW_DAYS:
            recent[s.get("proposal_id") or s["date"]] = s["date"]      # two taps on ONE outfit are one correction
    if len(recent) < 2:
        return None
    for prior in load(STORE, {}).values():
        if prior.get("activity") == activity and prior.get("action") == action:
            age = (today - datetime.strptime(prior["created_at"][:10], "%Y-%m-%d").date()).days
            if 0 <= age <= WINDOW_DAYS:
                return None                                            # already asked; don't nag
    current = activity_preference(activity).get("occasion") or occasion
    target = next_tier(activity, action, current)
    if not target:
        return None
    dates = sorted(recent.values())[-2:]
    said, step = DIRECTION[action]
    text = (f"That's twice you've called my {label(activity)} outfits {said} ({short(dates[0])}, {short(dates[1])}). "
            f"Make it a rule to dress {label(activity)} one step {step}: {current} → {target}?")
    pid = hashlib.sha256(f"{activity}|{action}|{today.isoformat()}".encode()).hexdigest()[:12]
    return {"id": pid, "activity": activity, "action": action, "from": current, "to": target, "dates": dates, "text": text,
            "reply_markup": {"inline_keyboard": [[
                {"text": "✅ Make it a rule", "callback_data": f"confirm:outfitrule-{pid}"},
                {"text": "❌ Just those outfits", "callback_data": f"skip:outfitrule-{pid}"}]]}}


def send(proposal):
    """Send the question and remember it. Returns True when it went out (or would have, under WARDROBE_NO_SEND)."""
    message_id = None
    if os.environ.get("WARDROBE_NO_SEND") != "1":
        from send_outfit_proposal import api                           # needs the bot token; never imported in tests
        receipt = api("sendMessage", {"chat_id": CHAT, "text": proposal["text"], "reply_markup": proposal["reply_markup"]})
        if not receipt.get("ok"):
            return False
        message_id = receipt.get("result", {}).get("message_id")
    store = load(STORE, {})
    store[proposal["id"]] = {k: proposal[k] for k in ("activity", "action", "from", "to", "dates", "text")} | {
        "status": "asked", "source_tg_msg_id": message_id, "created_at": datetime.now().astimezone().isoformat()}
    STORE.write_text(json.dumps(store, indent=2))
    return True


def resolve(pid, answer):
    store = load(STORE, {})
    row = store.get(pid)
    if not row or row.get("status") != "asked":
        return "No open rule question found. Nothing was changed. 🎩", None
    if answer == "accept":
        evidence = (f"The user {date.today().isoformat()}: tapped 'Make it a rule' after calling {label(row['activity'])} outfits "
                    f"{DIRECTION[row['action']][0]} on {' and '.join(row['dates'])}.")
        done = subprocess.run([sys.executable, str(SCRIPTS / "activity_policy.py"), "prefer", "--activity", row["activity"],
                               "--occasion", row["to"], "--merge", "--evidence", evidence], capture_output=True, text=True)
        if done.returncode:
            return f"⚠️ The rule was NOT saved — {(done.stdout + done.stderr).strip()[:200]} 🎩", None
        row["status"] = "accepted"
        text = f"Rule saved: {label(row['activity'])} now dresses {row['to']} (was {row['from']}). 🎩"
    else:
        row["status"] = "declined"
        text = f"Understood — no rule, just those outfits. 🎩"
    row["answered_at"] = datetime.now().astimezone().isoformat()
    STORE.write_text(json.dumps(store, indent=2))
    return text, row.get("source_tg_msg_id")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--activity", required=True); c.add_argument("--action", required=True); c.add_argument("--occasion", required=True)
    r = sub.add_parser("resolve")
    r.add_argument("--id", required=True); r.add_argument("--answer", choices=["accept", "decline"], required=True)
    args = ap.parse_args()
    if args.cmd == "check":
        print(json.dumps(check(args.activity, args.action, args.occasion), indent=2, ensure_ascii=False))
        return
    text, message_id = resolve(args.id, args.answer)
    if os.environ.get("WARDROBE_NO_SEND") == "1":
        print(text)
        return
    sys.path.insert(0, str(ROOT.parent / "shared"))
    from pending_proposal import edit_or_fallback                      # edit the question in place: one message, not two
    print(edit_or_fallback(message_id, text))


if __name__ == "__main__":
    main()
