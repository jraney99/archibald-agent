#!/usr/bin/env python3
"""Learn which NEW senders deserve a filter, and where their mail belongs.

`senders.json` only recognises senders someone added by hand, so an unknown
recurring sender (a new vendor, a new clinic, a new service) never earns a
filing proposal — the daemon could only ever offer to unsubscribe from it.
This module closes that gap:

  observe()   every inbound message updates a small tally per exact address
              (how often, over how long, and what kind of mail) — no API call,
              no model call, no cost
  qualifies() a sender becomes a candidate only once it is genuinely recurring
  route()     picks a destination label from the user's own taxonomy, by reading
              the subjects the sender actually sends

Nothing here proposes anything on its own. daemon._maybe_propose_sender_filter
feeds the result through the SAME gates every other proposal passes: exact
address only, a live 90-day profile, and email_policy's evidence rule (at least
two consistent records; any security history or mixture blocks a rule).

Pure module: no network or disk access at import time.
"""
from __future__ import annotations

import json
import re
import time
from collections import Counter
from pathlib import Path
from typing import Optional

import email_policy as policy

# The ledger lives in its own file, NOT the daemon state file: the daemon
# rewrites its state many times a minute, and carrying a growing dataset in it
# would rewrite hundreds of KB every time.
LEDGER_FILE = Path("~/.openclaw/sender_ledger.json")
MIN_OBSERVATIONS = 3      # a one-off, or a single burst, is not a pattern
MIN_SPAN_DAYS = 7         # …and it has to span real time, not one busy morning
MAX_ENTRIES = 400
RETIRE_AFTER_DAYS = 120   # forget senders that stopped writing
SUBJECT_MEMORY = 3        # kept only so a proposal can be eyeballed later
SUBJECT_MAX_CHARS = 90

# Signals per DESTINATION LABEL, not per area. An area is too coarse to file
# with: "travel" spans Flights, Hotels and Itineraries, and routing by an
# area's most common label filed hotel bookings under Travel/Flights.
#
# Money labels (Finance/*) are deliberately absent. A brand-new financial
# sender must be added deliberately by a human — otherwise a lookalike domain
# that sends statement-shaped subjects for a week could earn a rule that hides
# it from the inbox (policy 6: financial senders need extra protection).
LEARNABLE_LABELS = (
    ("Records/Packages", "packages",
     r"(?i)(tracking (number|info)|out for delivery|has shipped|is on the way|"
     r"(was|has been) delivered|your package|parcel|shipment)"),
    ("Records/Receipts", "shopping",
     r"(?i)(your order|order (#|confirmation|confirmed)|receipt|your purchase|refund)"),
    ("Travel/Flights", "travel",
     r"(?i)(flight|boarding pass|seat assignment|check in for|you.?re (going|flying) to)"),
    ("Travel/Hotels", "travel",
     r"(?i)(hotel|stay at|\b(suites|inn|resort|lodge)\b|room reservation|nights? stay)"),
    ("Health/Appointments", "health",
     r"(?i)(appointment|your visit|visit reminder|booked for|is booked)"),
    ("Health/Pharmacy", "health",
     r"(?i)(prescription|\brefill\b|\brx\b|ready for pickup at)"),
    ("Home/Building", "home",
     r"(?i)(work order|maintenance|\blease\b|resident|property manage|towing|"
     r"package room|community (reminder|notice))"),
    ("Home/Utilities", "home",
     r"(?i)(utility|electricity|water bill|gas bill|\boutage\b|meter read|energy (bill|report))"),
    ("Records/Documents", "documents",
     r"(?i)(docusign|signature (requested|required)|please sign|completed: |"
     r"agreement|document (is )?(ready|shared))"),
)


# ── ledger ───────────────────────────────────────────────────────────────────

def load(path: Optional[Path] = None) -> dict:
    # Resolve LEDGER_FILE at call time, not as a default argument: a default is
    # bound when the function is defined, which silently ignores any later
    # repointing of LEDGER_FILE (tests did exactly that and wrote to the real
    # ledger).
    try:
        data = json.loads((path or LEDGER_FILE).read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save(ledger: dict, path: Optional[Path] = None) -> None:
    (path or LEDGER_FILE).write_text(json.dumps(ledger, separators=(",", ":")))


def entry_for(ledger: dict, address: str) -> Optional[dict]:
    return ledger.get((address or "").lower())


def observe(ledger: dict, address: str, subject: str, labels=None,
            now: Optional[float] = None) -> dict:
    """Record one message from `address`. Returns that sender's entry.

    Label signals are scored here, once, rather than by keeping raw subjects
    around to re-scan later — that keeps an entry a few hundred bytes.
    """
    now = time.time() if now is None else now
    address = (address or "").lower()
    entry = ledger.setdefault(address, {
        "first": now, "last": now, "count": 0,
        "kinds": policy.empty_counts(), "labels": {}, "subjects": []})
    entry["last"] = now
    entry["count"] += 1
    kind = policy.classify_subject(subject, labels)
    entry["kinds"][kind] = entry["kinds"].get(kind, 0) + 1
    for label, _area, pattern in LEARNABLE_LABELS:
        if re.search(pattern, subject or ""):
            entry["labels"][label] = entry["labels"].get(label, 0) + 1
    entry["subjects"] = ([(subject or "")[:SUBJECT_MAX_CHARS]]
                         + entry["subjects"])[:SUBJECT_MEMORY]
    prune(ledger, now)
    return entry


def prune(ledger: dict, now: Optional[float] = None) -> int:
    """Drop senders that went quiet, then the least recent if still oversized."""
    now = time.time() if now is None else now
    block = ledger
    dead = [a for a, e in block.items()
            if now - e.get("last", 0) > RETIRE_AFTER_DAYS * 86400]
    for address in dead:
        block.pop(address, None)
    if len(block) > MAX_ENTRIES:
        for address, _ in sorted(block.items(), key=lambda kv: kv[1].get("last", 0)
                                 )[:len(block) - MAX_ENTRIES]:
            block.pop(address, None)
            dead.append(address)
    return len(dead)


def qualifies(entry: dict, now: Optional[float] = None) -> bool:
    """Recurring enough to be worth asking about."""
    if not entry:
        return False
    now = time.time() if now is None else now
    span_days = (now - entry.get("first", now)) / 86400
    return entry.get("count", 0) >= MIN_OBSERVATIONS and span_days >= MIN_SPAN_DAYS


def dominant_kind(entry: dict) -> str:
    kinds = entry.get("kinds", {})
    return max(policy.KINDS, key=lambda k: kinds.get(k, 0))


# ── label routing ────────────────────────────────────────────────────────────

def domain_of(address: str) -> str:
    return address.split("@", 1)[1].lower() if "@" in address else address


def score_subjects(subjects) -> dict:
    """Label hit counts for a list of subjects (used by tests and tooling;
    the daemon scores incrementally in observe())."""
    hits: dict = {}
    for subject in subjects or []:
        for label, _area, pattern in LEARNABLE_LABELS:
            if re.search(pattern, subject or ""):
                hits[label] = hits.get(label, 0) + 1
    return hits


def infer_label(subjects_or_hits) -> Optional[tuple]:
    """The one label this sender agrees on, as (label, area).

    Accepts either a list of subjects or a label→count map. Requires at least
    two messages pointing the same way and a strict winner, so an ambiguous
    sender (a restaurant booking that merely says "reservation") files nowhere
    rather than somewhere wrong.
    """
    hits = (Counter(subjects_or_hits) if isinstance(subjects_or_hits, dict)
            else Counter(score_subjects(subjects_or_hits)))
    ranked = hits.most_common()
    if not ranked or ranked[0][1] < 2:
        return None
    if len(ranked) > 1 and ranked[1][1] == ranked[0][1]:
        return None
    label = ranked[0][0]
    area = next(a for lb, a, _ in LEARNABLE_LABELS if lb == label)
    return label, area


def known_labels(config: dict) -> set:
    return {entry.get("label") for entry in config.get("senders", []) if entry.get("label")}


def route(entry: dict, config: dict) -> Optional[dict]:
    """Where this sender's mail should file, or None when it isn't clear.

    The label must already exist in the user's taxonomy — learned proposals never
    invent a new label, and a renamed label simply stops being offered.
    """
    inferred = infer_label(entry.get("labels") or entry.get("subjects", []))
    if not inferred:
        return None
    label, area = inferred
    if label not in known_labels(config):
        return None
    return {"area": area, "label": label}


def learned_verdict(ledger: dict, address: str, config: dict,
                    now: Optional[float] = None) -> Optional[dict]:
    """A senders.json-shaped verdict for a recurring unknown sender, or None.

    Only records-shaped senders are routed here. Marketing belongs to the
    unsubscribe flow, and security or unclear history must stay in the inbox.
    """
    entry = entry_for(ledger, address)
    if not qualifies(entry, now):
        return None
    kinds = entry["kinds"]
    # Records must be a strict majority, not merely the largest bucket: a
    # 50/50 records-and-marketing sender is mixed, and mixed never earns a rule.
    if kinds.get("security") or kinds.get("material", 0) <= (
            kinds.get("marketing", 0) + kinds.get("unknown", 0)):
        return None
    routed = route(entry, config)
    if not routed:
        return None
    return {"name": domain_of(address), "label": routed["label"],
            "area": routed["area"], "type": "learned", "source": "learned",
            "observations": entry["count"]}
