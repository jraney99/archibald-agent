#!/usr/bin/env python3
"""Apply the pending style profile update — moves the proposed diff in
profile/pending_style_update.json into profile/style_profile.yaml, bumps
the version, and logs it in change_log.

Every stored preference is:
  {"value": ..., "confidence": 0-100, "importance": 0-100, "evidence": [...]}

- confidence: how certain the agent is this is true.
- importance: how much this should weigh in recommendations, independent of
  how certain it is — a trivial-but-certain fact (always wears black socks)
  shouldn't carry the same weight as a core trait (prefers classic over
  trendy), even at equal confidence.
- evidence (optional, capped at 5 most recent): short dated observations
  that actually built the confidence, so "why do you think that?" can cite
  something real instead of the agent fabricating a plausible-sounding
  reason after the fact.

Usage:
  python3 confirm_style_update.py --show     # print the pending diff, no changes
  python3 confirm_style_update.py            # apply it

Pending file shape (a single proposed diff, not a queue — see SKILL.md):
{
  "review_summary": "plain-language description of the change",
  "add": {
    "style_categories": [{"value": "workwear", "confidence": 75, "importance": 60,
                           "evidence": [{"date": "2026-07-13", "note": "referenced JFK and Steve McQueen"}]}]
  },
  "set": {"desired_persona": {"value": "...", "confidence": 80, "importance": 70}},
  "remove": {"avoidances": ["skinny fit"]}
}

"add" on a value that already exists (case-insensitive match) merges into
it — confidence/importance updated, evidence appended (capped at 5) —
rather than creating a duplicate entry. "remove" matches by plain value.
"""
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is required: pip install pyyaml")

ROOT = Path(__file__).resolve().parent.parent
PENDING_PATH = ROOT / "profile" / "pending_style_update.json"
PROFILE_PATH = ROOT / "profile" / "style_profile.yaml"
VALIDATE_SCRIPT = Path(__file__).resolve().parent / "validate_style_profile.py"

EVIDENCE_CAP = 5

# fields "add"/"remove" are valid against — everything else in this profile
# is a scalar, set via "set" instead. Checking this up front is what stops
# a wrong diff from either crashing (add onto a string) or silently
# corrupting the field (remove iterating a string character-by-character).
LIST_FIELDS = {"style_categories", "preferred_colors", "preferred_materials", "preferred_brands", "avoidances", "inspirations"}
SCALAR_FIELDS = {"fit_notes", "desired_persona"}

# Plain date-or-null bookkeeping fields (not confidence-scored preferences) —
# each tracks when a particular offer was last made, so it's never repeated
# on a schedule. A bare string, not the {value, confidence, importance}
# shape SCALAR_FIELDS requires.
META_DATE_FIELDS = {"calibration_last_offered", "closet_catalog_last_offered"}


def load_pending():
    if not PENDING_PATH.exists():
        return {}
    with open(PENDING_PATH) as f:
        return json.load(f)


def is_valid_score(n):
    return isinstance(n, int) and not isinstance(n, bool) and 0 <= n <= 100


def is_valid_evidence(evidence):
    return isinstance(evidence, list) and all(
        isinstance(e, dict) and isinstance(e.get("date"), str) and isinstance(e.get("note"), str)
        for e in evidence
    )


def is_valid_entry(entry):
    if not isinstance(entry, dict) or "value" not in entry:
        return False
    if not isinstance(entry["value"], str) or not entry["value"].strip():
        return False
    if not is_valid_score(entry.get("confidence")):
        return False
    if not is_valid_score(entry.get("importance")):
        return False
    if "evidence" in entry and not is_valid_evidence(entry["evidence"]):
        return False
    return True


def merge_preference(existing, new):
    """Merge a partial update into what's already stored: value/confidence/
    importance are replaced when given, evidence is appended and capped —
    this is what lets a hunch become a near-certainty through reconfirmation
    without losing the trail of what built it."""
    if not isinstance(existing, dict):
        result = dict(new)
        if isinstance(result.get("evidence"), list):
            result["evidence"] = result["evidence"][-EVIDENCE_CAP:]
        return result

    result = dict(existing)
    for key in ("value", "confidence", "importance"):
        if key in new:
            result[key] = new[key]
    if "evidence" in new:
        result["evidence"] = (existing.get("evidence", []) + new["evidence"])[-EVIDENCE_CAP:]
    return result


def validate_diff(diff):
    for field, entries in diff.get("add", {}).items():
        if field not in LIST_FIELDS:
            sys.exit(f"'{field}' is not a list field — 'add' only works on: {sorted(LIST_FIELDS)}")
        if not isinstance(entries, list) or not all(is_valid_entry(e) for e in entries):
            sys.exit(f"'add.{field}' must be a list of {{'value', 'confidence' 0-100, 'importance' 0-100}} objects, got {entries!r}")

    for field in diff.get("remove", {}):
        if field not in LIST_FIELDS:
            sys.exit(f"'{field}' is not a list field — 'remove' only works on: {sorted(LIST_FIELDS)}")

    for field, value in diff.get("set", {}).items():
        if field in META_DATE_FIELDS:
            if value is not None and not isinstance(value, str):
                sys.exit(f"'{field}' must be a date string or null, got {value!r}")
            continue
        if field not in LIST_FIELDS and field not in SCALAR_FIELDS:
            sys.exit(f"'{field}' is not a known style_profile field. Valid: {sorted(LIST_FIELDS | SCALAR_FIELDS | META_DATE_FIELDS)}")
        if field in LIST_FIELDS:
            if not isinstance(value, list) or not all(is_valid_entry(e) for e in value):
                sys.exit(f"'{field}' is a list field — 'set' requires a list of valid preference objects, got {value!r}. Use 'add'/'remove' for individual entries.")
        if field in SCALAR_FIELDS:
            if value is not None and not is_valid_entry(value):
                sys.exit(f"'{field}' requires either null or {{'value', 'confidence' 0-100, 'importance' 0-100}}, got {value!r}")


def apply_diff(profile, diff):
    for field, entries in diff.get("add", {}).items():
        existing_list = profile.setdefault(field, [])
        for entry in entries:
            match = next((e for e in existing_list if isinstance(e, dict) and str(e.get("value", "")).strip().lower() == str(entry["value"]).strip().lower()), None)
            if match:
                merged = merge_preference(match, entry)
                match.clear()
                match.update(merged)
            else:
                existing_list.append(entry)

    for field, value in diff.get("set", {}).items():
        if field in META_DATE_FIELDS:
            profile[field] = value
        elif value is None:
            profile[field] = None
        elif field in SCALAR_FIELDS:
            profile[field] = merge_preference(profile.get(field), value)
        else:
            profile[field] = value

    for field, values in diff.get("remove", {}).items():
        existing = profile.get(field, [])
        profile[field] = [e for e in existing if not (isinstance(e, dict) and e.get("value") in values)]

    return profile


def main():
    pending = load_pending()
    if not pending:
        print("No pending style update.")
        return

    if len(sys.argv) == 2 and sys.argv[1] == "--show":
        print(json.dumps(pending, indent=2))
        return

    validate_diff(pending)

    with open(PROFILE_PATH) as f:
        profile = yaml.safe_load(f) or {}

    profile = apply_diff(profile, pending)
    profile["version"] = profile.get("version", 0) + 1
    profile["last_updated"] = date.today().isoformat()
    profile.setdefault("change_log", []).append({
        "date": profile["last_updated"],
        "version": profile["version"],
        "summary": pending.get("review_summary", ""),
    })

    with open(PROFILE_PATH, "w") as f:
        yaml.dump(profile, f, sort_keys=False, allow_unicode=True)

    with open(PENDING_PATH, "w") as f:
        json.dump({}, f)

    print(f"Applied — style_profile.yaml now at version {profile['version']}.")

    result = subprocess.run([sys.executable, str(VALIDATE_SCRIPT)], capture_output=True, text=True)
    print(result.stdout.strip())
    if result.returncode != 0:
        print("Validation failed after write — review style_profile.yaml.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
