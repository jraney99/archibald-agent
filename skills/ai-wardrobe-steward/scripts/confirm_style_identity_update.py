#!/usr/bin/env python3
"""Apply the pending style identity update — moves the proposed diff in
profile/pending_style_identity_update.json into profile/style_identity.yaml,
bumps the version, and logs it in change_log.

The Style Identity / Genome layer is the synthesis one level above individual
preferences (see style_profile.yaml) — not "likes navy," but "classic
American, coastal, rugged, refined." It emerges from the same ordinary
conversation as everything else; it is never a questionnaire, and it never
overwrites itself wholesale — new signal merges into what's there, same as
every other preference in this skill.

Every stored element is:
  {"value": ..., "confidence": 0-100, "importance": 0-100, "evidence": [...]}

- archetype: a single scalar (there's normally one dominant archetype at a
  time, though it can evolve — a "set" replaces it, but the merge still
  folds in the new confidence/importance/evidence rather than discarding
  history, so an archetype update reads as refinement, not amnesia).
- pillars / desired_impression / anti_identity: list fields — several can
  coexist (e.g. "Ivy League refinement" + "coastal freedom"), each with its
  own confidence/importance so a dominant pillar can be told apart from a
  minor one even before either is fully certain.

Usage:
  python3 confirm_style_identity_update.py --show     # print the pending diff, no changes
  python3 confirm_style_identity_update.py            # apply it

Pending file shape (a single proposed diff, not a queue):
{
  "review_summary": "plain-language description of the change",
  "set": {"archetype": {"value": "American Gentleman Adventurer", "confidence": 70, "importance": 90,
                         "evidence": [{"date": "2026-07-14", "note": "referenced JFK and Steve McQueen as influences"}]}},
  "add": {"pillars": [{"value": "Rugged heritage", "confidence": 75, "importance": 70,
                        "evidence": [{"date": "2026-07-14", "note": "..."}]}]},
  "remove": {"anti_identity": ["flashy luxury"]}
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
PENDING_PATH = ROOT / "profile" / "pending_style_identity_update.json"
PROFILE_PATH = ROOT / "profile" / "style_identity.yaml"
VALIDATE_SCRIPT = Path(__file__).resolve().parent / "validate_style_identity.py"

EVIDENCE_CAP = 5

LIST_FIELDS = {"pillars", "desired_impression", "anti_identity"}
SCALAR_FIELDS = {"archetype"}


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
        if field not in LIST_FIELDS and field not in SCALAR_FIELDS:
            sys.exit(f"'{field}' is not a known style_identity field. Valid: {sorted(LIST_FIELDS | SCALAR_FIELDS)}")
        if field in LIST_FIELDS:
            if not isinstance(value, list) or not all(is_valid_entry(e) for e in value):
                sys.exit(f"'{field}' is a list field — 'set' requires a list of valid entries, got {value!r}. Use 'add'/'remove' for individual entries.")
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
        if value is None:
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
        print("No pending style identity update.")
        return

    if len(sys.argv) == 2 and sys.argv[1] == "--show":
        print(json.dumps(pending, indent=2))
        return

    if not isinstance(pending, dict):
        sys.exit(f"pending_style_identity_update.json top level must be a mapping (a single diff, not a queue), got {type(pending).__name__}")

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

    print(f"Applied — style_identity.yaml now at version {profile['version']}.")

    result = subprocess.run([sys.executable, str(VALIDATE_SCRIPT)], capture_output=True, text=True)
    print(result.stdout.strip())
    if result.returncode != 0:
        print("Validation failed after write — review style_identity.yaml.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
