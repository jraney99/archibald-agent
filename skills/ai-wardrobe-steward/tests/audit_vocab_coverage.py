#!/usr/bin/env python3
"""Vocab/schema coverage audit — a different kind of test from run_eval.py.

run_eval.py checks scripts' *behavior* against synthetic data. This checks
the *code itself* for the specific bug pattern that kept showing up during
manual stress-testing: a field the schema/vocab says should be a controlled
vocabulary, wired into some but not all of the three places that matter
(vocab.yaml's enum, the confirm script's validation, the read-side validator)
— `fit_preferences.bottoms.length` and `outfit_history.py log --occasion`
were both exactly this: present in one or two of the three, silently
unvalidated in the others, until someone happened to test that exact field
by hand. This audit makes that class of gap show up automatically instead
of depending on manual discovery.

Two kinds of checks:
  1. Structural cross-checks — import the actual confirm/validate modules
     and compare their real constants against each other and against
     vocab.yaml, so drift between them is caught even if nobody remembers
     to update all three when adding a field.
  2. A CLI-arg-to-vocab lint — for write-path commands (add/log/set, not
     read-only list/report filters) that accept a value from a known
     controlled vocabulary, confirm the script's source actually references
     the matching vocab.yaml key somewhere. Coarser than #1 (text search,
     not AST), but it's exactly the grep that would have caught the
     outfit_history.py gap on day one.

Usage: python3 audit_vocab_coverage.py
"""
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is required: pip install pyyaml")

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
VOCAB_PATH = ROOT / "vocab.yaml"

sys.path.insert(0, str(SCRIPTS_DIR))


def load_module(name):
    """Import a script as a module without running its __main__ block."""
    import importlib
    return importlib.import_module(name)


def main():
    errors = []

    with open(VOCAB_PATH) as f:
        vocab = yaml.safe_load(f)

    # ---------- 1. structural cross-checks ----------

    confirm_body_fit = load_module("confirm_body_fit_update")
    validate_body_fit = load_module("validate_body_fit_profile")
    confirm_style = load_module("confirm_style_update")
    validate_style = load_module("validate_style_profile")
    confirm_identity = load_module("confirm_style_identity_update")
    validate_identity = load_module("validate_style_identity")

    # Every fit_preference_scales key in vocab.yaml must be wired into BOTH
    # the confirm script's enum-constrained paths and the validator's
    # checked-field lists — this is the exact shape of the `length` gap.
    vocab_scale_keys = set(vocab.get("fit_preference_scales", {}).keys())
    confirm_scale_keys = set(confirm_body_fit.CONSTRAINED_PATHS.values())
    validate_scale_keys = set(validate_body_fit.TOP_LEVEL_FIT_FIELDS) | set(validate_body_fit.BOTTOMS_FIT_FIELDS)

    if vocab_scale_keys != confirm_scale_keys:
        errors.append(
            f"fit_preference_scales mismatch — vocab.yaml has {sorted(vocab_scale_keys)}, "
            f"confirm_body_fit_update.CONSTRAINED_PATHS covers {sorted(confirm_scale_keys)} "
            f"(diff: {sorted(vocab_scale_keys ^ confirm_scale_keys)})"
        )
    if vocab_scale_keys != validate_scale_keys:
        errors.append(
            f"fit_preference_scales mismatch — vocab.yaml has {sorted(vocab_scale_keys)}, "
            f"validate_body_fit_profile's checked fields cover {sorted(validate_scale_keys)} "
            f"(diff: {sorted(vocab_scale_keys ^ validate_scale_keys)})"
        )

    # Measurement fields: confirm's MEASUREMENT_PATHS are "measurements.<field>"
    # dot-paths; validate's MEASUREMENT_FIELDS are the bare names.
    confirm_measurement_fields = {p.split(".", 1)[1] for p in confirm_body_fit.MEASUREMENT_PATHS}
    if confirm_measurement_fields != validate_body_fit.MEASUREMENT_FIELDS:
        errors.append(
            f"measurement fields mismatch — confirm_body_fit_update covers {sorted(confirm_measurement_fields)}, "
            f"validate_body_fit_profile covers {sorted(validate_body_fit.MEASUREMENT_FIELDS)} "
            f"(diff: {sorted(confirm_measurement_fields ^ validate_body_fit.MEASUREMENT_FIELDS)})"
        )

    # style_profile.yaml: confirm and validate must agree on which fields
    # are list-valued vs. scalar, or a field could be writable in a shape
    # the validator doesn't actually check.
    if confirm_style.LIST_FIELDS != validate_style.LIST_FIELDS:
        errors.append(f"style_profile LIST_FIELDS mismatch — confirm has {sorted(confirm_style.LIST_FIELDS)}, validate has {sorted(validate_style.LIST_FIELDS)}")
    if confirm_style.SCALAR_FIELDS != validate_style.SCALAR_FIELDS:
        errors.append(f"style_profile SCALAR_FIELDS mismatch — confirm has {sorted(confirm_style.SCALAR_FIELDS)}, validate has {sorted(validate_style.SCALAR_FIELDS)}")

    # style_identity.yaml: same check, one layer up the abstraction.
    if confirm_identity.LIST_FIELDS != validate_identity.LIST_FIELDS:
        errors.append(f"style_identity LIST_FIELDS mismatch — confirm has {sorted(confirm_identity.LIST_FIELDS)}, validate has {sorted(validate_identity.LIST_FIELDS)}")
    if confirm_identity.SCALAR_FIELDS != validate_identity.SCALAR_FIELDS:
        errors.append(f"style_identity SCALAR_FIELDS mismatch — confirm has {sorted(confirm_identity.SCALAR_FIELDS)}, validate has {sorted(validate_identity.SCALAR_FIELDS)}")

    # color_analysis.yaml: the confirm script's writable top-level fields and
    # the validator's known-fields set must agree, or a field could be
    # written in a shape the validator never checks (the `length` gap again).
    confirm_color = load_module("confirm_color_analysis_update")
    validate_color = load_module("validate_color_analysis")
    if confirm_color.KNOWN_FIELDS != validate_color.KNOWN_FIELDS:
        errors.append(
            f"color_analysis KNOWN_FIELDS mismatch — confirm_color_analysis_update has "
            f"{sorted(confirm_color.KNOWN_FIELDS)}, validate_color_analysis has "
            f"{sorted(validate_color.KNOWN_FIELDS)} "
            f"(diff: {sorted(confirm_color.KNOWN_FIELDS ^ validate_color.KNOWN_FIELDS)})"
        )
    # validate_color_analysis checks season/undertone/contrast against vocab —
    # confirm the vocab keys it names actually exist.
    for key in ("color_seasons", "undertones", "contrast_levels", "palette_families"):
        if key not in vocab:
            errors.append(f"vocab.yaml is missing '{key}', which validate_color_analysis.py checks against")
    validate_color_src = (SCRIPTS_DIR / "validate_color_analysis.py").read_text()
    for key in ("color_seasons", "undertones", "contrast_levels", "palette_families"):
        if key not in validate_color_src:
            errors.append(f"validate_color_analysis.py never references vocab key '{key}' — a value from it may go unvalidated")
    # color_match.py's verdicts must be exactly vocab.yaml palette_verdicts
    # (validate_closet.py checks a stored palette_match.verdict against that key).
    color_match_src = (SCRIPTS_DIR / "color_match.py").read_text()
    for verdict in vocab.get("palette_verdicts", []):
        if f'"{verdict}"' not in color_match_src:
            errors.append(f"color_match.py never emits the palette verdict '{verdict}' that vocab.yaml/validate_closet.py expect")

    # ---------- 2. CLI-arg-to-vocab lint (write-path commands only) ----------
    # (script, human label, required substrings — any one satisfies it)
    LINT_RULES = [
        ("outfit_candidates.py", "--occasion", ["occasion_formality", "occasion_tiers"]),
        ("outfit_history.py", "--occasion (log)", ["occasion_formality", "occasion_tiers"]),
        ("travel_packing.py", "--occasions", ["occasion_formality", "occasion_tiers"]),
        ("shopping_watchlist.py", "--season (add)", ["seasons"]),
        ("outfit_candidates.py", "--season", ["seasons"]),
        ("travel_packing.py", "--season", ["seasons"]),
        ("seasonal_review.py", "--season", ["seasons"]),
        ("shopping_watchlist.py", "--formality (add)", ["formality_scale"]),
        ("closet_bulk_state.py", "--set-state/--current-state", ["lifecycle_states"]),
        ("closet_bulk_state.py", "--disposition", ["disposition_types"]),
        ("shopping_watchlist.py", "--source (add)", ["watchlist_sources"]),
        ("shopping_watchlist.py", "--category (add)", ["category_groups"]),
        ("confirm_purchase_lesson.py", "lesson_type", ["purchase_lesson_types"]),
        ("confirm_body_fit_update.py", "units", ["unit_systems"]),
        ("purchase_decisions.py", "--recommendation", ["purchase_recommendations"]),
    ]

    for script, label, required_any in LINT_RULES:
        source = (SCRIPTS_DIR / script).read_text()
        if not any(req in source for req in required_any):
            errors.append(
                f"{script} ({label}) — expected a reference to one of {required_any} "
                f"in the source, found none. A value from this dimension may be accepted without validation."
            )

    # ---------- report ----------
    if errors:
        print(f"FAIL — {len(errors)} coverage gap(s):")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)

    print("OK — vocab/schema coverage is consistent across confirm scripts, validators, and vocab.yaml.")


if __name__ == "__main__":
    main()
