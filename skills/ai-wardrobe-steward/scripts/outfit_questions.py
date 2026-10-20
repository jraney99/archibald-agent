#!/usr/bin/env python3
"""The two one-tap questions the triage cannot answer for itself. Deterministic; no model.

  who    "First time I've seen {Name} on your calendar in person … Should meetings
          with {Name} get an outfit?"   [Business] [Date] [Family] [Friend, no outfits]
  venue  "Dinner at {Venue} ({day}). Dressy enough for an outfit?"   [Yes] [No, casual]

Asked once per person/venue per event; an unanswered question means no outfit for
that event and is not repeated until they next show up. The answer is remembered
(profile/people.json, profile/venues.json), so the same person or place is never
asked about twice. A tap edits the question in place — one message, not two.

Wording and trigger approved by the user 2026-09-20 (Automation rollout gate).

Usage:
  python3 outfit_questions.py answer --id <12hex> --value biz|date|fam|friend|yes|no
"""
import argparse
import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
STORE = ROOT / "profile" / "outfit_questions.json"
PEOPLE = ROOT / "profile" / "people.json"
VENUES = ROOT / "profile" / "venues.json"
CHAT = "1000000001"
CATEGORY = {"biz": "business", "date": "date", "fam": "family", "friend": "friend"}

sys.path.insert(0, str(SCRIPTS))
from proposal_caption import event_name, when  # noqa: E402


def load(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def person_key(person):
    return (person.get("email") or person.get("name") or "").lower()


def build(event, verdict):
    """The question for one ask-person / venue-check verdict: {id, type, key, text, reply_markup, ...}."""
    day = when(event.get("start_iso")).rsplit(",", 1)[0]
    if verdict["decision"] == "ask-person":
        person = verdict["person"]
        name = person.get("name") or person.get("email")
        key = person_key(person)
        qid = hashlib.sha256(f"who|{key}|{event.get('event_id')}".encode()).hexdigest()[:12]
        text = (f"First time I've seen {name} on your calendar in person ({event_name(event.get('title'))}, {day}). "
                f"Should meetings with {name} get an outfit?")
        buttons = [[{"text": "💼 Business", "callback_data": f"confirm:outfitwho-{qid}-biz"},
                    {"text": "❤️ Date", "callback_data": f"confirm:outfitwho-{qid}-date"}],
                   [{"text": "👪 Family", "callback_data": f"confirm:outfitwho-{qid}-fam"},
                    {"text": "🙅 Friend, no outfits", "callback_data": f"confirm:outfitwho-{qid}-friend"}]]
        return {"id": qid, "type": "who", "key": key, "name": name, "email": person.get("email"), "text": text, "reply_markup": {"inline_keyboard": buttons}}
    venue = verdict.get("venue_name") or "this place"
    qid = hashlib.sha256(f"venue|{verdict['venue_key']}|{event.get('event_id')}".encode()).hexdigest()[:12]
    text = f"Dinner at {venue} ({day}). Dressy enough for an outfit?"
    buttons = [[{"text": "👔 Yes", "callback_data": f"confirm:outfitvenue-{qid}-yes"},
                {"text": "👕 No, casual", "callback_data": f"confirm:outfitvenue-{qid}-no"}]]
    return {"id": qid, "type": "venue", "key": verdict["venue_key"], "name": venue, "text": text, "reply_markup": {"inline_keyboard": buttons}}


def ask(event, verdict):
    """Send the question unless this exact one was already asked. Returns the question if it went out."""
    question = build(event, verdict)
    store = load(STORE, {})
    if question["id"] in store:
        return None
    message_id = None
    if os.environ.get("WARDROBE_NO_SEND") != "1":
        from send_outfit_proposal import api
        receipt = api("sendMessage", {"chat_id": CHAT, "text": question["text"], "reply_markup": question["reply_markup"]})
        if not receipt.get("ok"):
            return None
        message_id = receipt.get("result", {}).get("message_id")
    store[question["id"]] = {k: question[k] for k in ("type", "key", "name", "text")} | {
        "email": question.get("email"), "event_id": event.get("event_id"), "status": "asked",
        "source_tg_msg_id": message_id, "asked_at": datetime.now().astimezone().isoformat(timespec="seconds")}
    STORE.write_text(json.dumps(store, indent=2, ensure_ascii=False))
    return question


def answer(qid, value):
    store = load(STORE, {})
    row = store.get(qid)
    if not row or row.get("status") != "asked":
        return "No open question found. Nothing was changed. 🎩", None
    today = datetime.now().date().isoformat()
    if row["type"] == "who":
        if value not in CATEGORY:
            return "That answer does not fit this question. Nothing was changed. 🎩", None
        people = load(PEOPLE, {"self_emails": [], "internal_domains": [], "people": {}})
        people.setdefault("people", {})[row["key"]] = {"name": row["name"], "category": CATEGORY[value],
                                                        "evidence": f"The user {today}: tapped '{CATEGORY[value]}' when asked about {row['name']}."}
        PEOPLE.write_text(json.dumps(people, indent=2, ensure_ascii=False))
        text = (f"Got it — {row['name']} is a friend, no outfits. 🎩" if value == "friend"
                else f"Got it — {row['name']} is {CATEGORY[value]}. 🎩")
    else:
        if value not in ("yes", "no"):
            return "That answer does not fit this question. Nothing was changed. 🎩", None
        venues = load(VENUES, {})
        venues[row["key"]] = {"dressy": value == "yes", "name": row["name"], "evidence": f"The user {today}: tapped '{'dressy' if value == 'yes' else 'casual'}'"}
        VENUES.write_text(json.dumps(venues, indent=2, ensure_ascii=False))
        text = f"Got it — {row['name']} is dressy, outfits on. 🎩" if value == "yes" else f"Got it — {row['name']} is casual, no outfits. 🎩"
    row.update({"status": "answered", "answer": value, "answered_at": datetime.now().astimezone().isoformat(timespec="seconds")})
    STORE.write_text(json.dumps(store, indent=2, ensure_ascii=False))
    return text, row.get("source_tg_msg_id")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("answer")
    a.add_argument("--id", required=True); a.add_argument("--value", required=True)
    args = ap.parse_args()
    text, message_id = answer(args.id, args.value)
    if os.environ.get("WARDROBE_NO_SEND") == "1":
        print(text)
        return
    sys.path.insert(0, str(ROOT.parent / "shared"))
    from pending_proposal import edit_or_fallback
    print(edit_or_fallback(message_id, text))


if __name__ == "__main__":
    main()
