#!/usr/bin/env python3
"""Stylist self-reflection (PART 5) — read-only aggregation across
style_profile.yaml, style_identity.yaml, and body_fit_profile.yaml so the
agent can answer "what do you currently believe about this person?" from
real computed groupings instead of composing an answer from memory on the
fly. Everything here is derived, not stored (DESIGN.md universal rule 4) —
nothing is written, this is pure read/report.

Reports three things:
  - High-confidence beliefs (>=80): what the agent is genuinely sure of.
  - Uncertain beliefs (<50): what's still a hunch.
  - Possible contradictions: a "like" and a "dislike" entry whose text is a
    close match (e.g. a preferred_colors entry and an avoidances entry that
    both say "black") — literal textual overlap only. This script does not
    invent semantic contradictions (e.g. inferring "minimalist" conflicts
    with "maximalist") because that requires judgment the agent should
    apply in conversation, not a hardcoded antonym list baked into the
    engine.
  - Stale entries: high-importance beliefs whose most recent evidence is
    older than STALE_DAYS — a candidate for on-demand style-evolution
    noticing (SKILL.md), never surfaced proactively by this script itself.

Usage: python3 style_reflection.py
"""
import sys
import json
from collections import Counter
from datetime import date, datetime
from difflib import SequenceMatcher
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is required: pip install pyyaml")

ROOT = Path(__file__).resolve().parent.parent
STYLE_PATH = ROOT / "profile" / "style_profile.yaml"
IDENTITY_PATH = ROOT / "profile" / "style_identity.yaml"
BODY_FIT_PATH = ROOT / "profile" / "body_fit_profile.yaml"
OUTFIT_HISTORY_PATH = ROOT / "profile" / "outfit_history.json"
STYLE_SIGNALS_PATH = ROOT / "profile" / "style_signals.json"

HIGH_CONFIDENCE_THRESHOLD = 80
LOW_CONFIDENCE_THRESHOLD = 50
STALE_DAYS = 180
CONTRADICTION_SIMILARITY_THRESHOLD = 0.8

POSITIVE_FIELDS = ["style_categories", "preferred_colors", "preferred_materials", "preferred_brands", "inspirations"]
NEGATIVE_FIELDS = ["avoidances"]
IDENTITY_POSITIVE_FIELDS = ["pillars", "desired_impression"]
IDENTITY_NEGATIVE_FIELDS = ["anti_identity"]


def load_yaml(path):
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def collect_entries(profile, fields, source):
    entries = []
    for field in fields:
        for entry in profile.get(field, []) or []:
            if isinstance(entry, dict) and "value" in entry:
                entries.append((source, field, entry))
    return entries


def collect_fit_preferences(body_fit):
    entries = []
    fit = body_fit.get("fit_preferences", {}) or {}
    for key in ("tops", "outerwear"):
        entry = fit.get(key)
        if isinstance(entry, dict) and "value" in entry:
            entries.append(("body_fit_profile", f"fit_preferences.{key}", entry))
    bottoms = fit.get("bottoms", {}) or {}
    for key in ("rise", "leg_opening", "break"):
        entry = bottoms.get(key)
        if isinstance(entry, dict) and "value" in entry:
            entries.append(("body_fit_profile", f"fit_preferences.bottoms.{key}", entry))
    return entries


def most_recent_evidence_date(entry):
    evidence = entry.get("evidence") or []
    dates = []
    for e in evidence:
        try:
            dates.append(datetime.strptime(e["date"], "%Y-%m-%d").date())
        except (ValueError, TypeError, KeyError):
            continue
    return max(dates) if dates else None


def texts_match(a, b):
    if not isinstance(a, str) or not isinstance(b, str) or not a or not b:
        return False, 0.0
    a_l, b_l = a.strip().lower(), b.strip().lower()
    if a_l in b_l or b_l in a_l:
        return True, 1.0
    sim = SequenceMatcher(None, a_l, b_l).ratio()
    return sim > CONTRADICTION_SIMILARITY_THRESHOLD, sim


def report_confidence_buckets(all_entries):
    high = [(s, f, e) for s, f, e in all_entries if isinstance(e.get("confidence"), int) and e["confidence"] >= HIGH_CONFIDENCE_THRESHOLD]
    low = [(s, f, e) for s, f, e in all_entries if isinstance(e.get("confidence"), int) and e["confidence"] < LOW_CONFIDENCE_THRESHOLD]

    print("## High-confidence beliefs\n")
    if high:
        high.sort(key=lambda t: t[2].get("importance", 0), reverse=True)
        for source, field, entry in high:
            print(f"  - [{source}/{field}] \"{entry['value']}\" (confidence {entry['confidence']}, importance {entry.get('importance', '?')})")
    else:
        print("  None yet.")
    print()

    print("## Uncertain beliefs (still hunches)\n")
    if low:
        for source, field, entry in low:
            print(f"  - [{source}/{field}] \"{entry['value']}\" (confidence {entry['confidence']}, importance {entry.get('importance', '?')})")
    else:
        print("  None — nothing currently below the uncertainty threshold.")
    print()


def report_contradictions(all_entries, positive_fields, negative_fields):
    positives = [(s, f, e) for s, f, e in all_entries if f in positive_fields]
    negatives = [(s, f, e) for s, f, e in all_entries if f in negative_fields]

    found = []
    for ps, pf, pe in positives:
        for ns, nf, ne in negatives:
            matched, sim = texts_match(pe.get("value"), ne.get("value"))
            if matched:
                found.append((pe, ne, sim))
    return found


def report_stale(all_entries):
    print("## Possibly stale (no reconfirmation in a while)\n")
    today = date.today()
    stale = []
    for source, field, entry in all_entries:
        most_recent = most_recent_evidence_date(entry)
        if most_recent is None:
            continue
        age_days = (today - most_recent).days
        if age_days >= STALE_DAYS and entry.get("importance", 0) >= 50:
            stale.append((source, field, entry, age_days))

    if stale:
        stale.sort(key=lambda t: t[3], reverse=True)
        for source, field, entry, age_days in stale:
            print(f"  - [{source}/{field}] \"{entry['value']}\" — last reconfirmed {age_days} day(s) ago")
    else:
        print("  Nothing flagged — either no evidence-dated entries yet, or all recently reconfirmed.")
    print()


def main():
    style = load_yaml(STYLE_PATH)
    identity = load_yaml(IDENTITY_PATH)
    body_fit = load_yaml(BODY_FIT_PATH)

    all_entries = []
    all_entries += collect_entries(style, POSITIVE_FIELDS + NEGATIVE_FIELDS, "style_profile")
    if isinstance(style.get("desired_persona"), dict) and "value" in style["desired_persona"]:
        all_entries.append(("style_profile", "desired_persona", style["desired_persona"]))

    all_entries += collect_entries(identity, IDENTITY_POSITIVE_FIELDS + IDENTITY_NEGATIVE_FIELDS, "style_identity")
    if isinstance(identity.get("archetype"), dict) and "value" in identity["archetype"]:
        all_entries.append(("style_identity", "archetype", identity["archetype"]))

    all_entries += collect_fit_preferences(body_fit)

    if not all_entries:
        print("Nothing learned yet — style_profile.yaml, style_identity.yaml, and body_fit_profile.yaml are all empty.")
        return

    print(f"# Stylist self-reflection — {date.today().isoformat()}\n")
    history = json.loads(OUTFIT_HISTORY_PATH.read_text()) if OUTFIT_HISTORY_PATH.exists() else []
    signals = json.loads(STYLE_SIGNALS_PATH.read_text()) if STYLE_SIGNALS_PATH.exists() else []
    print("## Evidence calibration\n")
    print(f"  - Confirmed wear events: {len(history)}")
    by_source = Counter(s.get("source", "unknown") for s in signals)
    print(f"  - Proposal-response signals: {by_source.get('outfit-proposal-response', 0)}")
    for src, n in sorted(by_source.items()):
        if src != "outfit-proposal-response":
            print(f"  - {src} signals: {n}")
    if len(history) < 5:
        print("  - Warning: behavioral evidence is thin; high confidence currently reflects stated preferences/inspiration more than observed wear.")
    print()
    report_confidence_buckets(all_entries)

    print("## Possible contradictions\n")
    # Checked across style_profile AND style_identity together, not each file
    # in isolation — a preferred_colors entry that overlaps with an
    # anti_identity entry is just as real a tension as one within a single
    # file, and a belief system split across two files shouldn't get a
    # narrower contradiction check than a single-file one would.
    all_positive_fields = POSITIVE_FIELDS + IDENTITY_POSITIVE_FIELDS
    all_negative_fields = NEGATIVE_FIELDS + IDENTITY_NEGATIVE_FIELDS
    contradictions = report_contradictions(all_entries, all_positive_fields, all_negative_fields)
    if contradictions:
        for pe, ne, sim in contradictions:
            print(f"  - \"{pe['value']}\" (liked, confidence {pe.get('confidence')}) vs. \"{ne['value']}\" (avoided, confidence {ne.get('confidence')}) — similar text, worth reconciling in conversation")
    else:
        print("  None found by direct text overlap. (This only catches literal overlap — a real stylistic tension the agent notices in conversation may not show up here.)")
    print()

    report_stale(all_entries)


if __name__ == "__main__":
    main()
