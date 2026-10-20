#!/usr/bin/env python3
"""Decide which calendar events deserve an outfit. Deterministic, silent by default.

The user, 2026-09-20: no outfits for working out — only golf, in-person meetings with
business contacts / dates / family, flight days, hosted events, nicer restaurant
dinners, and anything with a stated dress code. Friends never. The old trigger
(any of ~15 phrases in the text) fired on every simulator session and never on a
work meeting or a flight.

An event gets an outfit only if it POSITIVELY matches a kind below; an event type
this has never seen gets nothing. People are identified by invite email first
(stable), by a name parsed from the title only as a fallback.

Decisions:
  propose      build a brief and a board
  ask-person   first time this person appears in person — one tap to categorise them
  venue-check  a restaurant with nobody named — depends on how dressy the place is
  skip         everything else, with the reason

PRINT-ONLY today: nothing here sends anything. `replay` lists verdicts for review.

Usage:
  python3 event_triage.py replay --from 2026-07-26 --to 2026-10-04 [--all]
  python3 event_triage.py replay --events-json <file from fetch_calendar --json>
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
FETCH = ROOT.parent / "shared" / "fetch_calendar.py"
VOCAB = ROOT / "vocab.yaml"
PEOPLE = ROOT / "profile" / "people.json"
VENUES = ROOT / "profile" / "venues.json"
PREFS = ROOT / "profile" / "triage_preferences.json"     # what he switched off with a 🚫 tap
OUTFIT_CATEGORIES = {"business", "date", "family"}       # "friend" is remembered, and never proposed for

sys.path.insert(0, str(SCRIPTS))
from activity_policy import venue_aliases  # noqa: E402

FLIGHT = re.compile(r"\b[A-Z0-9]{2}\s?\d{1,4}\b.*\b[A-Z]{3}\s*(?:→|->|–|—|\bto\b)\s*[A-Z]{3}\b")
NAME = r"[A-Z][\w.'’-]+(?: [A-Z][\w.'’-]+){1,2}"
TITLE_PERSON = [re.compile(rf"\bw(?:ith|\.|/)\s+(?P<name>{NAME})"), re.compile(rf"^the user and (?P<name>{NAME})"),
                re.compile(rf"^(?P<name>{NAME}) <> the user")]


def load(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def has(text, phrase):
    return re.search(rf"(?<![\w]){re.escape(phrase)}(?![\w])", text, re.IGNORECASE) is not None


def longest(text, phrases):
    hits = [p for p in phrases if has(text, p)]
    return max(hits, key=len) if hits else None


class Triage:
    def __init__(self, vocab=None, people=None, venues=None, prefs=None):
        self.vocab = vocab or yaml.safe_load(VOCAB.read_text())
        self.people = people if people is not None else load(PEOPLE, {})
        self.venues = venues if venues is not None else load(VENUES, {})
        self.prefs = prefs if prefs is not None else load(PREFS, {})
        self.activity_aliases = {**self.vocab.get("activity_aliases", {}), **venue_aliases()}
        self.flight_days = set()

    # ---- who ---------------------------------------------------------------
    def _is_own(self, email):
        email = (email or "").lower()
        return (not email or email in {e.lower() for e in self.people.get("self_emails", [])}
                or email.split("@")[-1] in {d.lower() for d in self.people.get("internal_domains", [])}
                or email.endswith("calendar.google.com"))

    def _lookup(self, email=None, name=None):
        book = self.people.get("people", {})
        if email and email.lower() in book:
            return book[email.lower()]
        for row in book.values():
            if name and row.get("name", "").lower() == name.lower():
                return row
        return None

    def others(self, event):
        """External people on the event: invite emails first, the title only if there are none."""
        found, seen = [], set()
        emails = ([] if event.get("organizer_self") else [event.get("organizer_email")]) + [a.get("email") for a in event.get("attendees", [])]
        names = {a.get("email", "").lower(): a.get("name", "") for a in event.get("attendees", [])}
        for email in emails:
            if self._is_own(email) or email.lower() in seen:
                continue
            seen.add(email.lower())
            known = self._lookup(email=email)
            found.append({"email": email.lower(), "name": (known or {}).get("name") or names.get(email.lower(), ""), "category": (known or {}).get("category")})
        if not found:
            for pattern in TITLE_PERSON:
                m = pattern.search(event.get("title", ""))
                if m and m.group("name").lower() not in ("the user",):
                    known = self._lookup(name=m.group("name"))
                    found.append({"email": None, "name": m.group("name"), "category": (known or {}).get("category")})
                    break
        return found

    # ---- verdict -----------------------------------------------------------
    def verdict(self, event):
        found = self._verdict(event)
        if found["decision"] == "propose":
            if found["kind"] in self.prefs.get("never_kinds", []):
                return {"decision": "skip", "kind": None, "reason": f"he switched off {found['kind']} outfits"}
            if found.get("matched_phrase") in self.prefs.get("never_phrases", []):
                return {"decision": "skip", "kind": None, "reason": f"he switched off outfits for '{found['matched_phrase']}' events"}
        return found

    def _verdict(self, event):
        v = self.vocab
        title, location = event.get("title", ""), event.get("location", "") or ""
        place = title.split(" @ ", 1)[1] if " @ " in title else ""
        text = f"{title} {location}"
        skip = lambda reason: {"decision": "skip", "kind": None, "reason": reason}

        if event.get("status") == "cancelled" or event.get("response_status") == "declined":
            return skip("cancelled or declined")
        attire = longest(f"{text} {event.get('description', '')}", v["explicit_attire_phrases"])
        if attire:
            occasion = longest(f"{text} {event.get('description', '')}", v["occasion_aliases"])
            return {"decision": "propose", "kind": "dress-code", "reason": f"states a dress code ('{attire}')",
                    "occasion": v["occasion_aliases"].get(occasion, "smart-casual"), "activity": "general", "explicit_occasion": True}
        if FLIGHT.search(title):
            day = event.get("day")
            if day in self.flight_days:
                return skip("same travel day as an earlier flight")
            self.flight_days.add(day)
            return {"decision": "propose", "kind": "flight", "reason": "flight day (one board per travel day)", "occasion": "casual", "activity": "travel"}
        if event.get("all_day"):
            return skip("all-day block, not an occasion")
        remote = longest(f"{place} {location}", v["remote_meeting_markers"]) or ("http" in location.lower() and "remote link")
        if remote:
            return skip(f"not in person ({remote})")
        never = longest(title, v["never_propose_phrases"])
        if never:
            return skip(f"never-propose type ('{never}')")
        activity = self.activity_aliases.get(longest(text, self.activity_aliases) or "", None)
        if activity in v["workout_activities"]:
            return skip(f"workout ({activity}) — he dresses for these himself")
        if not location.strip() and not place.strip():
            return skip("no place given — cannot tell it is in person")
        if longest(f"{place} {location}".strip(), v["home_markers"]) and len(f"{place}".strip()) <= 6:
            return skip("at home")
        if activity == "golf-round":
            # "Golf @ <somewhere>" is not evidence of a round. Until 2026-10-13 it was, and a
            # TOUR of an indoor simulator club got a smart-casual board; the user: "this is not a
            # real round of golf." Silence is this triage's default, so a round must say so.
            indoor = longest(f"{place} {location} {title}", v["indoor_golf_markers"])
            if indoor:
                return skip(f"indoor golf ('{indoor}') — he dresses for these himself")
            visiting = longest(title, v["visit_phrases"])
            if visiting:
                return skip(f"a '{visiting}', not a round — silent by default")
            return {"decision": "propose", "kind": "golf-round", "reason": "a real round of golf", "occasion": "smart-casual", "activity": "golf-round", "outdoor": True}

        people = self.others(event)
        wanted = [p for p in people if p["category"] in OUTFIT_CATEGORIES]
        if wanted:
            best = sorted(wanted, key=lambda p: ["business", "date", "family"].index(p["category"]))[0]
            occasion, act = {"business": ("business-casual", "work"), "date": ("smart-casual", "dining"), "family": ("smart-casual", "general")}[best["category"]]
            return {"decision": "propose", "kind": f"meeting-{best['category']}", "reason": f"in person with {best['name'] or best['email']} ({best['category']})",
                    "person": best, "occasion": occasion, "activity": act}
        unknown = [p for p in people if not p["category"]]
        if unknown:
            return {"decision": "ask-person", "kind": "meeting-unknown", "person": unknown[0],
                    "reason": f"in person with {unknown[0]['name'] or unknown[0]['email']}, not categorised yet"}
        if people:
            return skip("in person with " + ", ".join(f"{p['name'] or p['email']} ({p['category']})" for p in people) + " — no outfits")

        hosted = longest(title, v["hosted_event_phrases"])
        if hosted:
            occasion = v["occasion_aliases"].get(longest(text, v["occasion_aliases"]) or "", "smart-casual")
            return {"decision": "propose", "kind": "hosted-event", "reason": f"hosted event ('{hosted}')", "matched_phrase": hosted, "occasion": occasion, "activity": "general"}
        dining = longest(title, v["dining_phrases"])
        if dining:
            key = re.sub(r"\s+", " ", (place or location).strip().lower())[:80]
            known = self.venues.get(key)
            if known:
                if known.get("dressy"):
                    return {"decision": "propose", "kind": "dining", "venue_key": key, "venue_name": (place or location).strip()[:40],
                            "reason": f"dinner at a dressier place ({known.get('evidence', 'remembered')})", "occasion": "smart-casual", "activity": "dining"}
                return skip(f"casual restaurant ({known.get('evidence', 'remembered')})")
            return {"decision": "venue-check", "kind": "dining", "venue_key": key, "venue_name": (place or location).strip()[:40], "reason": f"'{dining}' with nobody named — depends on how dressy the place is"}
        return skip("not a known kind of occasion — silent by default")


def fetch(args):
    if args.events_json:
        return json.loads(Path(args.events_json).read_text())
    cmd = [sys.executable, str(FETCH), "--from", args.frm, "--to", args.to, "--json"]
    done = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if done.returncode:
        sys.exit(done.stdout or done.stderr)
    return json.loads(done.stdout)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("replay")
    r.add_argument("--from", dest="frm"); r.add_argument("--to"); r.add_argument("--events-json")
    r.add_argument("--all", action="store_true", help="also list every skipped event, not just a summary")
    r.add_argument("--json", action="store_true")
    args = ap.parse_args()
    triage = Triage()
    rows = [(e, triage.verdict(e)) for e in fetch(args).get("events", [])]
    if args.json:
        print(json.dumps([{**{k: e.get(k) for k in ("event_id", "day", "start_fmt", "title", "location")}, "verdict": v} for e, v in rows], indent=2, ensure_ascii=False))
        return
    marks = {"propose": "OUTFIT ", "ask-person": "ASK WHO", "venue-check": "VENUE? ", "skip": "  —    "}
    for e, v in rows:
        if v["decision"] != "skip" or args.all:
            print(f"{marks[v['decision']]} {e.get('start_fmt', ''):24} {e.get('title', '')[:52]:52} {v['reason']}")
    if not args.all:
        reasons = {}
        for e, v in rows:
            if v["decision"] == "skip":
                reasons[re.sub(r"\(.*?\)|'.*?'", "", v["reason"]).strip()] = reasons.get(re.sub(r"\(.*?\)|'.*?'", "", v["reason"]).strip(), 0) + 1
        print(f"\nsilent for {sum(reasons.values())} events: " + "; ".join(f"{n}× {why}" for why, n in sorted(reasons.items(), key=lambda kv: -kv[1])))


if __name__ == "__main__":
    main()
