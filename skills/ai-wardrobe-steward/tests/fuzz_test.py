#!/usr/bin/env python3
"""Property-based / fuzz testing — a third, genuinely different testing
methodology from run_eval.py (hand-picked examples) and mutation_check.py
(deliberately broken code). This generates randomized, adversarial-shaped
diffs — random confidence/importance types and ranges, random strings
(empty, whitespace, unicode, very long), random evidence shapes — and checks
INVARIANTS that must hold no matter what input arrives, rather than
asserting one specific expected output per example:

  1. Never crash. A confirm script may reject bad input, but it should
     never do so via an uncaught traceback.
  2. Never partially write. If a confirm script rejects a diff (nonzero
     exit), the profile file it would have written to must be byte-identical
     to what it was before — no silent partial mutation.
  3. Never write something invalid. If a confirm script accepts a diff
     (exit 0), the resulting profile file must pass its own validate_*.py
     script — "succeeded" and "produced valid data" must never diverge.

Uses only the stdlib `random` module (no hypothesis) — this skill's scripts
are stdlib + PyYAML only, and the test tooling matches that constraint.
Fixed seed for reproducibility: a failure here should be re-runnable, not a
one-time fluke that vanishes on the next run.

Usage: python3 fuzz_test.py [--iterations N] [--seed N]
"""
import argparse
import json
import random
import shutil
import string
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is required: pip install pyyaml")

ROOT = Path(__file__).resolve().parent.parent
PROFILE_DIR = ROOT / "profile"
SCRIPTS_DIR = ROOT / "scripts"

ALPHABET = string.ascii_letters + string.digits + " -_'\"!@#.,()éü中文🎉\t\n"


def random_string(rng, min_len=0, max_len=40):
    length = rng.randint(min_len, max_len)
    return "".join(rng.choice(ALPHABET) for _ in range(length))


def random_value(rng):
    choice = rng.random()
    if choice < 0.1:
        return ""
    if choice < 0.2:
        return "   "
    if choice < 0.25:
        return random_string(rng, 1, 200)  # long
    return random_string(rng, 1, 20)


def random_score(rng):
    choice = rng.random()
    if choice < 0.4:
        return rng.randint(0, 100)  # valid
    if choice < 0.5:
        return rng.randint(-1000, -1)
    if choice < 0.6:
        return rng.randint(101, 1000)
    if choice < 0.7:
        return rng.choice([True, False])
    if choice < 0.8:
        return round(rng.uniform(0, 100), 2)
    if choice < 0.9:
        return str(rng.randint(0, 100))
    return rng.choice([None, [], {}, random_string(rng, 1, 10)])


def random_evidence(rng):
    if rng.random() < 0.3:
        return None
    n = rng.randint(0, 6)
    out = []
    for _ in range(n):
        if rng.random() < 0.15:
            out.append(rng.choice(["not-a-dict", 42, None]))
        else:
            out.append({
                "date": f"2026-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
                "note": random_string(rng, 0, 300),
            })
    return out


def random_entry(rng, include_evidence=True):
    entry = {"value": random_value(rng)}
    if rng.random() < 0.95:
        entry["confidence"] = random_score(rng)
    if rng.random() < 0.95:
        entry["importance"] = random_score(rng)
    if include_evidence:
        ev = random_evidence(rng)
        if ev is not None:
            entry["evidence"] = ev
    return entry


def run(script, *args):
    return subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / script), *args],
        capture_output=True, text=True,
    )


def snapshot(relpath):
    path = PROFILE_DIR / relpath
    return path.read_bytes() if path.exists() else None


def restore(relpath, content):
    path = PROFILE_DIR / relpath
    if content is None:
        if path.exists():
            path.unlink()
    else:
        path.write_bytes(content)


def check_invariants(label, before, relpath, confirm_result, validate_script):
    """Returns a list of violation strings (empty if none)."""
    violations = []
    after = snapshot(relpath)

    if "Traceback" in confirm_result.stderr:
        violations.append(f"{label}: uncaught traceback — {confirm_result.stderr.strip().splitlines()[-1]}")
        return violations  # no point checking further invariants after a crash

    if confirm_result.returncode != 0:
        if after != before:
            violations.append(f"{label}: rejected (exit {confirm_result.returncode}) but {relpath} was still modified — partial write on a rejected diff")
    else:
        v = run(validate_script)
        if v.returncode != 0:
            violations.append(f"{label}: confirmed successfully (exit 0) but {validate_script} then reports invalid data: {v.stdout.strip()[:200]}")

    return violations


def fuzz_style_update(rng, iterations):
    violations = []
    LIST_FIELDS = ["style_categories", "preferred_colors", "preferred_materials", "preferred_brands", "avoidances", "inspirations"]
    for _ in range(iterations):
        before = snapshot("style_profile.yaml")
        field = rng.choice(LIST_FIELDS)
        diff = {"review_summary": random_string(rng, 0, 50), "add": {field: [random_entry(rng) for _ in range(rng.randint(1, 3))]}}
        (PROFILE_DIR / "pending_style_update.json").write_text(json.dumps(diff))
        r = run("confirm_style_update.py")
        violations.extend(check_invariants("confirm_style_update", before, "style_profile.yaml", r, "validate_style_profile.py"))
        restore("style_profile.yaml", before)
        restore("pending_style_update.json", b"{}")
    return violations


def fuzz_style_identity(rng, iterations):
    violations = []
    LIST_FIELDS = ["pillars", "desired_impression", "anti_identity"]
    for _ in range(iterations):
        before = snapshot("style_identity.yaml")
        field = rng.choice(LIST_FIELDS)
        diff = {"review_summary": random_string(rng, 0, 50), "add": {field: [random_entry(rng) for _ in range(rng.randint(1, 3))]}}
        (PROFILE_DIR / "pending_style_identity_update.json").write_text(json.dumps(diff))
        r = run("confirm_style_identity_update.py")
        violations.extend(check_invariants("confirm_style_identity_update", before, "style_identity.yaml", r, "validate_style_identity.py"))
        restore("style_identity.yaml", before)
        restore("pending_style_identity_update.json", b"{}")
    return violations


def fuzz_body_fit(rng, iterations):
    violations = []
    PATHS = ["fit_preferences.tops", "fit_preferences.outerwear", "fit_preferences.bottoms.rise",
             "fit_preferences.bottoms.leg_opening", "fit_preferences.bottoms.length", "fit_preferences.bottoms.break",
             "measurements.chest", "measurements.waist", "measurements.height"]
    for _ in range(iterations):
        before = snapshot("body_fit_profile.yaml")
        path = rng.choice(PATHS)
        entry = random_entry(rng)
        if rng.random() < 0.3 and "measurements" not in path:
            entry["contexts"] = {rng.choice(["business-formal", "casual", "not-a-real-tier"]): random_entry(rng, include_evidence=False)}
        diff = {"review_summary": random_string(rng, 0, 50), "set": {path: entry}}
        (PROFILE_DIR / "pending_body_fit_update.json").write_text(json.dumps(diff))
        r = run("confirm_body_fit_update.py")
        violations.extend(check_invariants("confirm_body_fit_update", before, "body_fit_profile.yaml", r, "validate_body_fit_profile.py"))
        restore("body_fit_profile.yaml", before)
        restore("pending_body_fit_update.json", b"{}")
    return violations


def fuzz_purchase_lesson(rng, iterations):
    violations = []
    LESSON_TYPES = ["fit", "brand", "color", "quality", "lifestyle", "other", "not-a-real-type"]
    for _ in range(iterations):
        before = snapshot("purchase_lessons.json")
        entry = {
            "id": random_string(rng, 1, 20) or "fallback-id",
            "lesson_type": rng.choice(LESSON_TYPES),
            "item_description": random_value(rng),
            "lesson": random_value(rng),
            "confidence": random_score(rng),
        }
        if rng.random() < 0.5:
            entry["evidence"] = random_evidence(rng)
        if rng.random() < 0.3:
            entry["linked_closet_item_id"] = random_string(rng, 1, 15)
        (PROFILE_DIR / "pending_purchase_lessons.json").write_text(json.dumps([entry]))
        r = run("confirm_purchase_lesson.py", "--all")
        violations.extend(check_invariants("confirm_purchase_lesson", before, "purchase_lessons.json", r, "validate_purchase_lessons.py"))
        restore("purchase_lessons.json", before)
        restore("pending_purchase_lessons.json", b"[]")
    return violations


def fuzz_color_analysis(rng, iterations):
    violations = []
    FIELDS = ["season", "undertone", "contrast_level", "priority_families", "core_colors",
              "palette", "leather_palette", "exclude", "not_a_real_field"]
    SEASONS = ["Soft Summer", "True Winter", "made up season", "", 42]
    FAMILIES = ["blue-greens", "blues", "neutrals", "chartreuse", ""]

    def random_swatch():
        h = rng.choice([
            "#" + "".join(rng.choice("0123456789abcdefABCDEF") for _ in range(6)),
            "#" + "".join(rng.choice("0123456789abcdef") for _ in range(rng.randint(0, 8))),
            random_string(rng, 0, 10),
            42,
        ])
        entry = {"hex": h, "name": random_value(rng)}
        if rng.random() < 0.7:
            entry["family"] = rng.choice(FAMILIES)
        return entry

    for _ in range(iterations):
        before = snapshot("color_analysis.yaml")
        field = rng.choice(FIELDS)
        if field in ("palette", "leather_palette"):
            value = [random_swatch() for _ in range(rng.randint(0, 4))]
        elif field == "season":
            value = rng.choice(SEASONS)
        elif field == "undertone":
            value = rng.choice(["neutral", "cool", "chilly", 7])
        elif field == "contrast_level":
            value = rng.choice(["low", "medium", "extreme", None])
        elif field in ("priority_families", "core_colors"):
            value = rng.choice([[rng.choice(FAMILIES)], "not-a-list", []])
        elif field == "exclude":
            value = rng.choice([
                {"hard_reject": [random_swatch()]},
                {"undertone_examples": "not-a-list"},
                "not-a-mapping",
            ])
        else:
            value = random_value(rng)
        diff = {"review_summary": random_string(rng, 0, 40), "set": {field: value}}
        (PROFILE_DIR / "pending_color_analysis_update.json").write_text(json.dumps(diff))
        r = run("confirm_color_analysis_update.py")
        violations.extend(check_invariants("confirm_color_analysis_update", before, "color_analysis.yaml", r, "validate_color_analysis.py"))
        restore("color_analysis.yaml", before)
        restore("pending_color_analysis_update.json", b"{}")
    return violations


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=150, help="iterations per fuzzed script")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    backup_dir = Path(tempfile.mkdtemp()) / "profile_backup"
    shutil.copytree(PROFILE_DIR, backup_dir)

    rng = random.Random(args.seed)
    all_violations = []
    try:
        all_violations += fuzz_style_update(rng, args.iterations)
        all_violations += fuzz_style_identity(rng, args.iterations)
        all_violations += fuzz_body_fit(rng, args.iterations)
        all_violations += fuzz_purchase_lesson(rng, args.iterations)
        all_violations += fuzz_color_analysis(rng, args.iterations)
    finally:
        shutil.rmtree(PROFILE_DIR)
        shutil.copytree(backup_dir, PROFILE_DIR)
        shutil.rmtree(backup_dir.parent)

    total_runs = args.iterations * 5
    if all_violations:
        print(f"FAIL — {len(all_violations)} invariant violation(s) out of {total_runs} fuzzed run(s) (seed={args.seed}):")
        for v in all_violations[:30]:
            print(f"  - {v}")
        if len(all_violations) > 30:
            print(f"  ... and {len(all_violations) - 30} more")
        sys.exit(1)

    print(f"OK — {total_runs} fuzzed run(s) (seed={args.seed}), no invariant violations (no crashes, no partial writes, no invalid-but-accepted data).")


if __name__ == "__main__":
    main()
