#!/usr/bin/env python3
"""Validate profile/body_fit_profile.yaml's structure against vocab.yaml.

Unlike closet.json, this file is deliberately YAML for human-editability —
which means it can be hand-edited outside confirm_body_fit_update.py's
type/path-checks entirely. This is the independent safety net for that path:
run any time, not just after a scripted write.

Measurements and fit preferences are {"value": ..., "confidence": 0-100,
"importance": 0-100, "evidence": [...]}, not bare values — this validates
that shape. Fit preferences may additionally carry a "contexts" dict keyed
by vocab.yaml's occasion_tiers, each holding its own valid preference
object. "units" and measured_date are plain values, not preferences.

Usage: python3 validate_body_fit_profile.py
"""
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is required: pip install pyyaml")

ROOT = Path(__file__).resolve().parent.parent
PROFILE_PATH = ROOT / "profile" / "body_fit_profile.yaml"
VOCAB_PATH = ROOT / "vocab.yaml"

MEASUREMENT_FIELDS = {
    "height", "weight", "chest", "waist", "inseam", "shoe_size",
    "shoulder_width", "sleeve_length", "neck", "hip", "thigh",
}

# Module-level (not inline in the function) so tests/audit_vocab_coverage.py
# can import and cross-check these against vocab.yaml's fit_preference_scales
# and confirm_body_fit_update.py's CONSTRAINED_PATHS directly, instead of
# three places drifting out of sync silently (see DESIGN.md's note on the
# fit_preferences.bottoms.length gap this was built to catch).
TOP_LEVEL_FIT_FIELDS = ("tops", "outerwear")
BOTTOMS_FIT_FIELDS = ("rise", "leg_opening", "length", "break")


def check_evidence(evidence, label, errors):
    if not isinstance(evidence, list):
        errors.append(f"{label} 'evidence' must be a list, got {type(evidence).__name__}")
        return
    for j, ev in enumerate(evidence):
        if not isinstance(ev, dict) or not isinstance(ev.get("date"), str) or not isinstance(ev.get("note"), str):
            errors.append(f"{label} evidence[{j}] must be a mapping with string 'date'/'note', got {ev!r}")


def check_entry(entry, label, errors, allow_contexts=False, scale=None, occasion_tiers=None):
    if not isinstance(entry, dict):
        errors.append(f"{label} must be a mapping with 'value'/'confidence'/'importance', got {type(entry).__name__}: {entry!r}")
        return None
    if "value" not in entry:
        errors.append(f"{label} missing 'value'")
    elif isinstance(entry["value"], str) and not entry["value"].strip():
        # Only the empty/whitespace-only string case is rejected here —
        # 'value' is numeric for measurements, string for fit preferences,
        # and numeric validity is already checked separately by the caller.
        errors.append(f"{label} 'value' must be a non-empty string, got {entry['value']!r}")
    for score_field in ("confidence", "importance"):
        score = entry.get(score_field)
        if not isinstance(score, int) or isinstance(score, bool) or not (0 <= score <= 100):
            errors.append(f"{label} '{score_field}' must be an int 0-100, got {score!r}")
    if "evidence" in entry:
        check_evidence(entry["evidence"], label, errors)

    value = entry.get("value")

    if "contexts" in entry:
        if not allow_contexts:
            errors.append(f"{label} has 'contexts' but this field doesn't support per-context overrides")
        elif not isinstance(entry["contexts"], dict):
            errors.append(f"{label}.contexts must be a mapping, got {type(entry['contexts']).__name__}")
        else:
            for ctx_key, ctx_entry in entry["contexts"].items():
                if occasion_tiers is not None and ctx_key not in occasion_tiers:
                    errors.append(f"{label}.contexts has key '{ctx_key}', not one of vocab.yaml's occasion_tiers: {occasion_tiers}")
                ctx_value = check_entry(ctx_entry, f"{label}.contexts.{ctx_key}", errors)
                if scale is not None and ctx_value is not None and ctx_value not in scale:
                    errors.append(f"{label}.contexts.{ctx_key}.value is '{ctx_value}', not one of {scale}")

    return value


def main():
    if not PROFILE_PATH.exists():
        sys.exit(f"body_fit_profile.yaml not found at {PROFILE_PATH}")
    if not VOCAB_PATH.exists():
        sys.exit(f"vocab.yaml not found at {VOCAB_PATH}")

    with open(PROFILE_PATH) as f:
        profile = yaml.safe_load(f)
    with open(VOCAB_PATH) as f:
        vocab = yaml.safe_load(f)

    errors = []

    if not isinstance(profile, dict):
        sys.exit(f"FAIL — body_fit_profile.yaml top level must be a mapping, got {type(profile).__name__}")

    if not isinstance(profile.get("version"), int):
        errors.append(f"'version' must be an int, got {profile.get('version')!r}")

    units = profile.get("units")
    if units is not None and units not in vocab.get("unit_systems", []):
        errors.append(f"'units' is '{units}', not one of {vocab.get('unit_systems', [])}")

    # (calibration_last_offered lives only in style_profile.yaml — see SKILL.md
    # "Deep calibration". It was previously mirrored here and never written;
    # removed to keep one authoritative home for the offer-tracking date.)

    occasion_tiers = vocab.get("occasion_tiers", [])

    measurements = profile.get("measurements", {})
    if not isinstance(measurements, dict):
        errors.append(f"'measurements' must be a mapping, got {type(measurements).__name__}")
    else:
        for field in MEASUREMENT_FIELDS:
            entry = measurements.get(field)
            if entry is None:
                continue
            value = check_entry(entry, f"measurements.{field}", errors)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
                errors.append(f"measurements.{field}.value must be numeric, got {type(value).__name__}: {value!r}")
        measured_date = measurements.get("measured_date")
        if measured_date is not None and not isinstance(measured_date, str):
            errors.append(f"measurements.measured_date must be a date string or null, got {measured_date!r}")

    fit_prefs = profile.get("fit_preferences", {})
    scales = vocab.get("fit_preference_scales", {})
    if not isinstance(fit_prefs, dict):
        errors.append(f"'fit_preferences' must be a mapping, got {type(fit_prefs).__name__}")
    else:
        for field in TOP_LEVEL_FIT_FIELDS:
            entry = fit_prefs.get(field)
            if entry is None:
                continue
            value = check_entry(entry, f"fit_preferences.{field}", errors, allow_contexts=True, scale=scales.get(field, []), occasion_tiers=occasion_tiers)
            if value is not None and value not in scales.get(field, []):
                errors.append(f"fit_preferences.{field}.value is '{value}', not one of {scales.get(field, [])}")

        bottoms = fit_prefs.get("bottoms", {})
        if not isinstance(bottoms, dict):
            errors.append(f"'fit_preferences.bottoms' must be a mapping, got {type(bottoms).__name__}")
        else:
            for field in BOTTOMS_FIT_FIELDS:
                entry = bottoms.get(field)
                if entry is None:
                    continue
                value = check_entry(entry, f"fit_preferences.bottoms.{field}", errors, allow_contexts=True, scale=scales.get(field, []), occasion_tiers=occasion_tiers)
                if value is not None and value not in scales.get(field, []):
                    errors.append(f"fit_preferences.bottoms.{field}.value is '{value}', not one of {scales.get(field, [])}")

    brand_sizing = profile.get("brand_sizing", [])
    if not isinstance(brand_sizing, list):
        errors.append(f"'brand_sizing' must be a list, got {type(brand_sizing).__name__}")
    else:
        for i, entry in enumerate(brand_sizing):
            if not isinstance(entry, dict):
                errors.append(f"brand_sizing[{i}] must be a mapping, got {type(entry).__name__}")
                continue
            confidence = entry.get("confidence")
            if not isinstance(confidence, int) or isinstance(confidence, bool) or not (0 <= confidence <= 100):
                errors.append(f"brand_sizing[{i}] 'confidence' must be an int 0-100, got {confidence!r}")
            if "evidence" in entry:
                check_evidence(entry["evidence"], f"brand_sizing[{i}]", errors)

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

    print(f"OK — body_fit_profile.yaml valid (version {profile.get('version')})")


if __name__ == "__main__":
    main()
