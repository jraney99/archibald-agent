#!/usr/bin/env python3
"""Validate profile/style_identity.yaml's structure.

Deliberately YAML for human-editability, same as style_profile.yaml — this
is the independent safety net for hand-edits, run any time, not just after
a scripted write.

Every stored element is {"value": ..., "confidence": 0-100, "importance":
0-100, "evidence": [...]} — same shape as style_profile.yaml, one level up
the abstraction (archetype/pillars/desired impression/anti-identity instead
of individual colors/materials/brands).

Usage: python3 validate_style_identity.py
"""
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is required: pip install pyyaml")

PROFILE_PATH = Path(__file__).resolve().parent.parent / "profile" / "style_identity.yaml"

LIST_FIELDS = {"pillars", "desired_impression", "anti_identity"}
SCALAR_FIELDS = {"archetype"}


def check_entry(entry, label, errors):
    if not isinstance(entry, dict):
        errors.append(f"{label} must be a mapping with 'value'/'confidence'/'importance', got {type(entry).__name__}: {entry!r}")
        return
    if "value" not in entry:
        errors.append(f"{label} missing 'value'")
    elif not isinstance(entry["value"], str) or not entry["value"].strip():
        errors.append(f"{label} 'value' must be a non-empty string, got {entry['value']!r}")
    for score_field in ("confidence", "importance"):
        score = entry.get(score_field)
        if not isinstance(score, int) or isinstance(score, bool) or not (0 <= score <= 100):
            errors.append(f"{label} '{score_field}' must be an int 0-100, got {score!r}")
    if "evidence" in entry:
        evidence = entry["evidence"]
        if not isinstance(evidence, list):
            errors.append(f"{label} 'evidence' must be a list, got {type(evidence).__name__}")
        else:
            for j, ev in enumerate(evidence):
                if not isinstance(ev, dict) or not isinstance(ev.get("date"), str) or not isinstance(ev.get("note"), str):
                    errors.append(f"{label} evidence[{j}] must be a mapping with string 'date'/'note', got {ev!r}")


def main():
    if not PROFILE_PATH.exists():
        sys.exit(f"style_identity.yaml not found at {PROFILE_PATH}")

    with open(PROFILE_PATH) as f:
        profile = yaml.safe_load(f)

    errors = []

    if not isinstance(profile, dict):
        sys.exit(f"FAIL — style_identity.yaml top level must be a mapping, got {type(profile).__name__}")

    if not isinstance(profile.get("version"), int):
        errors.append(f"'version' must be an int, got {profile.get('version')!r}")

    for field in LIST_FIELDS:
        if field not in profile:
            continue
        if not isinstance(profile[field], list):
            errors.append(f"'{field}' must be a list, got {type(profile[field]).__name__}: {profile[field]!r}")
            continue
        for i, entry in enumerate(profile[field]):
            check_entry(entry, f"{field}[{i}]", errors)

    for field in SCALAR_FIELDS:
        if field in profile and profile[field] is not None:
            check_entry(profile[field], f"'{field}'", errors)

    change_log = profile.get("change_log", [])
    if not isinstance(change_log, list):
        errors.append(f"'change_log' must be a list, got {type(change_log).__name__}")
    else:
        for i, entry in enumerate(change_log):
            if not isinstance(entry, dict):
                errors.append(f"change_log[{i}] must be a mapping, got {type(entry).__name__}")
                continue
            for key in ("date", "version", "summary"):
                if key not in entry:
                    errors.append(f"change_log[{i}] missing '{key}'")

    if errors:
        print(f"FAIL — {len(errors)} issue(s):")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)

    print(f"OK — style_identity.yaml valid (version {profile.get('version')})")


if __name__ == "__main__":
    main()
