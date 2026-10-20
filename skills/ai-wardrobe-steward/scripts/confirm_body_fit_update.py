#!/usr/bin/env python3
"""Apply the pending body/fit profile update (DESIGN.md Module 2) — moves the
proposed diff in profile/pending_body_fit_update.json into
profile/body_fit_profile.yaml, bumps version, logs the change.

Measurements and fit preferences are stored as:
  {"value": ..., "confidence": 0-100, "importance": 0-100, "evidence": [...]}
"units" and measurements.measured_date stay plain values (a unit system
choice and a timestamp aren't taste preferences with uncertainty attached).

Fit preferences additionally support per-context overrides — someone can
prefer relaxed fits by default but structured ones for business-formal
occasions. Context keys are vocab.yaml's occasion_tiers, reusing the same
vocabulary the Outfit Engine already filters on rather than inventing a
parallel concept:

fit_preferences.tops:
  value: relaxed
  confidence: 85
  importance: 60
  contexts:
    business-formal: {value: structured, confidence: 70, importance: 80}

Usage:
  python3 confirm_body_fit_update.py --show     # print the pending diff, no changes
  python3 confirm_body_fit_update.py            # apply it

Pending file shape (a single proposed diff, not a queue):
{
  "review_summary": "plain-language description of the change",
  "set": {
    "units": "imperial",
    "measurements.chest": {"value": 40, "confidence": 85, "importance": 50},
    "fit_preferences.tops": {"value": "classic", "confidence": 70, "importance": 60,
                              "contexts": {"business-formal": {"value": "structured", "confidence": 70, "importance": 80}}}
  },
  "add": {"brand_sizing": [{"brand": "J.Crew", "category": "shirt", "size": "M", "notes": "true to size", "confidence": 90}]}
}

"set" on a measurement/fit-preference path MERGES into what's already
there — value/confidence/importance are replaced when given, evidence is
appended and capped, and a "contexts" update merges context-by-context
rather than replacing the whole contexts dict. This is what lets one
context get corrected without silently discarding the others. "add" on a
brand_sizing entry that already matches an existing (brand, category) pair
merges into it (confidence/evidence) rather than duplicating.
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
PENDING_PATH = ROOT / "profile" / "pending_body_fit_update.json"
PROFILE_PATH = ROOT / "profile" / "body_fit_profile.yaml"
VOCAB_PATH = ROOT / "vocab.yaml"
VALIDATE_SCRIPT = Path(__file__).resolve().parent / "validate_body_fit_profile.py"

EVIDENCE_CAP = 5

# dot-path -> vocab.yaml fit_preference_scales key, for enum validation.
# Also the set of paths eligible for per-context overrides.
CONSTRAINED_PATHS = {
    "fit_preferences.tops": "tops",
    "fit_preferences.outerwear": "outerwear",
    "fit_preferences.bottoms.rise": "rise",
    "fit_preferences.bottoms.leg_opening": "leg_opening",
    "fit_preferences.bottoms.length": "length",
    "fit_preferences.bottoms.break": "break",
}

MEASUREMENT_PATHS = {
    f"measurements.{f}" for f in
    ("height", "weight", "chest", "waist", "inseam", "shoe_size", "shoulder_width", "sleeve_length", "neck", "hip", "thigh")
}

# paths that are plain values, not {value, confidence} — a unit system and a
# timestamp aren't preferences with uncertainty attached
PLAIN_PATHS = {"units", "measurements.measured_date"}

# the only field "add" is meant for — everything else in this profile is a
# scalar or nested-scalar structure set via dot-path, not a list to append to
KNOWN_ADD_FIELDS = {"brand_sizing"}


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


def is_valid_entry(entry, allow_contexts=False):
    if not isinstance(entry, dict) or "value" not in entry:
        return False
    # 'value' is a string for fit preferences but numeric for measurements —
    # only reject the empty/whitespace-only string case here; numeric
    # validity (including rejecting bool) is checked separately by the
    # MEASUREMENT_PATHS check in validate_diff.
    if isinstance(entry["value"], str) and not entry["value"].strip():
        return False
    if not is_valid_score(entry.get("confidence")):
        return False
    if not is_valid_score(entry.get("importance")):
        return False
    if "evidence" in entry and not is_valid_evidence(entry["evidence"]):
        return False
    if "contexts" in entry:
        if not allow_contexts or not isinstance(entry["contexts"], dict):
            return False
        for ctx_value in entry["contexts"].values():
            if not is_valid_entry(ctx_value, allow_contexts=False):
                return False
    return True


def merge_preference(existing, new):
    """Merge a partial update into what's already stored — see module
    docstring. Recurses into 'contexts' so updating one context never wipes
    out the others or the default."""
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
    if "contexts" in new:
        merged_contexts = dict(existing.get("contexts", {}))
        for ctx_key, ctx_value in new["contexts"].items():
            merged_contexts[ctx_key] = merge_preference(merged_contexts.get(ctx_key), ctx_value)
        result["contexts"] = merged_contexts
    return result


def merge_brand_sizing(existing, new):
    result = dict(existing)
    for key, value in new.items():
        if key != "evidence":
            result[key] = value
    if "evidence" in new or "evidence" in existing:
        result["evidence"] = (existing.get("evidence", []) + new.get("evidence", []))[-EVIDENCE_CAP:]
    return result


def set_path(d, path, value):
    keys = path.split(".")
    cur = d
    for k in keys[:-1]:
        cur = cur.setdefault(k, {})
    cur[keys[-1]] = value


def get_path(d, path):
    keys = path.split(".")
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


def path_exists(d, path):
    """A 'set' path is only valid if it already resolves to a real key in the
    profile's structure — this is what stops a typo (measurements.chset)
    from silently creating a new bogus field instead of erroring."""
    keys = path.split(".")
    cur = d
    for k in keys[:-1]:
        if not isinstance(cur, dict) or k not in cur:
            return False
        cur = cur[k]
    return isinstance(cur, dict) and keys[-1] in cur


def validate_diff(diff, vocab, profile):
    """Validates by previewing the ACTUAL merge (via merge_preference — the
    same function apply_diff uses) and checking the result, rather than
    validating the raw diff value in isolation. This is what lets a partial
    update ('just bump this context's confidence') pass validation without
    needing to duplicate every existing field — the preview always comes
    out complete, because merge_preference fills in whatever the diff
    didn't specify from what's already stored."""
    scales = vocab.get("fit_preference_scales", {})
    occasion_tiers = vocab.get("occasion_tiers", [])

    for path, value in diff.get("set", {}).items():
        if not path_exists(profile, path):
            sys.exit(f"'{path}' is not a known field in body_fit_profile.yaml — check for a typo.")

        if path == "units":
            if value is not None and value not in vocab.get("unit_systems", []):
                sys.exit(f"'{value}' is not a valid units value. Valid: {vocab.get('unit_systems', [])}")
            continue
        if path in PLAIN_PATHS:
            continue

        if path in KNOWN_ADD_FIELDS:
            if not isinstance(value, list):
                sys.exit(f"'{path}' is a list field — 'set' requires a list value, got {type(value).__name__}: {value!r}. Use 'add' for individual entries.")
            continue

        if value is None:
            continue  # clearing a field is always fine

        allow_contexts = path in CONSTRAINED_PATHS
        existing = get_path(profile, path)
        preview = merge_preference(existing, value)

        if not is_valid_entry(preview, allow_contexts=allow_contexts):
            sys.exit(f"'{path}' would be invalid after merging with the existing value: {preview!r}")

        if path in MEASUREMENT_PATHS and (isinstance(preview["value"], bool) or not isinstance(preview["value"], (int, float))):
            sys.exit(f"'{path}' value must be numeric, got {preview['value']!r}")

        scale_key = CONSTRAINED_PATHS.get(path)
        if scale_key:
            if preview["value"] not in scales.get(scale_key, []):
                sys.exit(f"'{preview['value']}' is not valid for {path}. Valid: {scales.get(scale_key, [])}")
            for ctx_key, ctx_entry in preview.get("contexts", {}).items():
                if ctx_key not in occasion_tiers:
                    sys.exit(f"'{ctx_key}' is not a valid context for {path} — contexts must be one of vocab.yaml's occasion_tiers: {occasion_tiers}")
                if ctx_entry["value"] not in scales.get(scale_key, []):
                    sys.exit(f"'{ctx_entry['value']}' is not valid for {path}.contexts.{ctx_key}. Valid: {scales.get(scale_key, [])}")

    for field, entries in diff.get("add", {}).items():
        if field not in KNOWN_ADD_FIELDS:
            sys.exit(f"'{field}' is not a valid 'add' target. Valid: {sorted(KNOWN_ADD_FIELDS)}")
        for entry in entries:
            if not isinstance(entry, dict) or not is_valid_score(entry.get("confidence")):
                sys.exit(f"'{field}' entries must include an int 'confidence' 0-100, got {entry!r}")
            if "evidence" in entry and not is_valid_evidence(entry["evidence"]):
                sys.exit(f"'{field}' entry has malformed 'evidence', got {entry.get('evidence')!r}")


def apply_diff(profile, diff):
    for path, value in diff.get("set", {}).items():
        if path == "units" or path in PLAIN_PATHS or path in KNOWN_ADD_FIELDS:
            set_path(profile, path, value)
        elif value is None:
            set_path(profile, path, None)
        else:
            set_path(profile, path, merge_preference(get_path(profile, path), value))

    for field, entries in diff.get("add", {}).items():
        existing = profile.setdefault(field, [])
        for entry in entries:
            match = next((
                e for e in existing
                if isinstance(e, dict)
                and str(e.get("brand", "")).strip().lower() == str(entry.get("brand", "")).strip().lower()
                and str(e.get("category", "")).strip().lower() == str(entry.get("category", "")).strip().lower()
            ), None)
            if match:
                merged = merge_brand_sizing(match, entry)
                match.clear()
                match.update(merged)
            else:
                existing.append(entry)

    return profile


def main():
    pending = load_pending()
    if not pending:
        print("No pending body/fit update.")
        return

    if len(sys.argv) == 2 and sys.argv[1] == "--show":
        print(json.dumps(pending, indent=2))
        return

    with open(VOCAB_PATH) as f:
        vocab = yaml.safe_load(f)
    with open(PROFILE_PATH) as f:
        profile = yaml.safe_load(f) or {}

    validate_diff(pending, vocab, profile)

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

    print(f"Applied — body_fit_profile.yaml now at version {profile['version']}.")

    result = subprocess.run([sys.executable, str(VALIDATE_SCRIPT)], capture_output=True, text=True)
    print(result.stdout.strip())
    if result.returncode != 0:
        print("Validation failed after write — review body_fit_profile.yaml.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
