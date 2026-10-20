#!/usr/bin/env python3
"""Mutation testing — checks whether tests/run_eval.py and
tests/audit_vocab_coverage.py actually catch real regressions, rather than
just accumulating a large test count that happens to pass either way.

Each mutation below reverts one specific fix made during this skill's
stress-testing (or is the same class of realistic slip: an off-by-one, a
removed guard, a loosened comparison) — these are real bugs that existed in
this codebase at some point, not contrived examples. For each one: copy the
whole skill into an isolated temp directory, apply the mutation there, run
the test harness against the mutated copy, and check that something fails.

A mutation that survives (no test catches it) means the test suite has a
real blind spot at that exact line — worth adding a test for, not just
noting and moving on.

Usage: python3 mutation_check.py
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (relative path, exact text to replace, replacement, description of the bug reintroduced)
MUTATIONS = [
    ("scripts/confirm_style_update.py",
     "return isinstance(n, int) and not isinstance(n, bool) and 0 <= n <= 100",
     "return isinstance(n, int) and 0 <= n <= 100",
     "confirm_style_update: re-allow bool as a valid confidence/importance score"),

    ("scripts/confirm_style_update.py",
     'match = next((e for e in existing_list if isinstance(e, dict) and str(e.get("value", "")).strip().lower() == str(entry["value"]).strip().lower()), None)',
     'match = next((e for e in existing_list if isinstance(e, dict) and str(e.get("value", "")).lower() == str(entry["value"]).lower()), None)',
     "confirm_style_update: drop .strip(), trailing whitespace creates a near-duplicate instead of reinforcing"),

    ("scripts/confirm_style_update.py",
     'if not isinstance(entry["value"], str) or not entry["value"].strip():\n        return False\n    if not is_valid_score(entry.get("confidence")):',
     'if not is_valid_score(entry.get("confidence")):',
     "confirm_style_update: re-allow an empty-string preference value"),

    ("scripts/confirm_style_update.py",
     "EVIDENCE_CAP = 5",
     "EVIDENCE_CAP = 8",
     "confirm_style_update: evidence cap loosened from 5 to 8"),

    ("scripts/closet_report.py",
     "FORMALITY_TOLERANCE = 1",
     "FORMALITY_TOLERANCE = 3",
     "closet_report: formality tolerance loosened from 1 to 3, over-reports duplicates/redundancy"),

    ("scripts/closet_report.py",
     "def is_valid_formality(f):\n    \"\"\"A closet item's formality should be an int 1-5 per vocab.yaml, but\n    this file reads raw JSON that may not have gone through validate_closet.py\n    since it was last written — bool is explicitly excluded since Python's\n    bool is an int subclass and would otherwise silently compare as 0/1.\"\"\"\n    return f is not None and not isinstance(f, bool) and isinstance(f, (int, float))",
     "def is_valid_formality(f):\n    return f is not None",
     "closet_report: re-allow non-numeric/bool formality, crashes possible-duplicates and redundancy sections"),

    ("scripts/outfit_candidates.py",
     'if formality is None or isinstance(formality, bool) or not isinstance(formality, (int, float)):',
     "if formality is None:",
     "outfit_candidates: re-allow non-numeric formality, crashes the Outfit Engine's core candidate lookup"),

    ("scripts/care.py",
     "if wears_since >= rule.get(\"interval_wears\", float(\"inf\")):",
     "if wears_since > rule.get(\"interval_wears\", float(\"inf\")):",
     "care: off-by-one, an item exactly at its interval_wears threshold no longer flagged as due"),

    ("scripts/confirm_closet_item.py",
     "    existing_ids = {item.get(\"id\") for item in closet}\n    seen = set()\n    colliding = set()\n    for item in to_confirm:\n        item_id = item.get(\"id\")\n        if item_id in existing_ids or item_id in seen:\n            colliding.add(item_id)\n        seen.add(item_id)\n    if colliding:\n        sys.exit(f\"Refusing to confirm — id(s) already in use, would create a duplicate: {', '.join(sorted(colliding))}. Fix the pending draft first.\")\n\n    for item in to_confirm:",
     "    for item in to_confirm:",
     "confirm_closet_item: remove the duplicate-id guard, writes colliding ids into closet.json before validation catches it"),

    ("scripts/outfit_history.py",
     "    duplicates = {i for i in item_ids if i in seen or seen.add(i)}\n    if duplicates:\n        sys.exit(f\"Duplicate item id(s) in --items — an outfit can't include the same item twice: {', '.join(duplicates)}\")",
     "    duplicates = set()",
     "outfit_history: remove the duplicate-item guard, a single item could be double-counted in one outfit"),

    ("scripts/outfit_history.py",
     "    if args.occasion not in vocab[\"occasion_formality\"]:\n        sys.exit(f\"Unknown occasion '{args.occasion}'. Valid: {list(vocab['occasion_formality'].keys())}\")",
     "    pass",
     "outfit_history: remove occasion validation, any string gets logged as a real occasion tier"),

    ("scripts/shopping_watchlist.py",
     "    if args.max_price is not None and args.max_price <= 0:\n        sys.exit(f\"'--max-price {args.max_price:g}' must be a positive number.\")",
     "    pass",
     "shopping_watchlist: remove positive-max-price check, a negative/zero price silently creates an unsatisfiable watch entry"),

    ("scripts/style_reflection.py",
     "HIGH_CONFIDENCE_THRESHOLD = 80",
     "HIGH_CONFIDENCE_THRESHOLD = 95",
     "style_reflection: high-confidence threshold raised from 80 to 95, understates what the agent is actually sure of"),

    ("scripts/purchase_decisions.py",
     'if rec in ("skip", "already-own") and outcome == "bought":',
     'if rec == "skip" and outcome == "bought":',
     "purchase_decisions: override detection misses 'already-own, bought anyway' cases — the stewardship metric under-counts"),

    ("scripts/confirm_purchase_lesson.py",
     "    linked = entry.get(\"linked_closet_item_id\")\n    if linked and not any(item.get(\"id\") == linked for item in closet):\n        errors.append(f\"linked_closet_item_id '{linked}' not found in closet.json\")",
     "    pass",
     "confirm_purchase_lesson: remove linked_closet_item_id existence check, a lesson can point at a nonexistent item"),

    ("scripts/color_match.py",
     "    if rej is not None and rej_d < match_d and rej_d <= BORDERLINE_MAX:",
     "    if rej is not None and rej_d < match_d and rej_d <= 0:",
     "color_match: disable the hard-reject-swatch check, an out-of-palette bright colour reads as borderline/in-palette"),

    ("scripts/color_match.py",
     "    warm_flag = (not leather\n                 and chroma >= WARM_CHROMA_MIN\n                 and WARM_HUE_LOW <= hue <= WARM_HUE_HIGH\n                 and match_d > WARM_TIGHT_MATCH)",
     "    warm_flag = False",
     "color_match: drop the warm-undertone reject, a camel/mustard garment passes as in-palette"),

    ("scripts/confirm_color_analysis_update.py",
     "    result = subprocess.run([sys.executable, str(VALIDATE_SCRIPT)], capture_output=True, text=True)\n    if result.returncode != 0:\n        if original is None:\n            PROFILE_PATH.unlink()\n        else:\n            PROFILE_PATH.write_text(original)",
     "    result = subprocess.run([sys.executable, str(VALIDATE_SCRIPT)], capture_output=True, text=True)\n    if result.returncode != 0:\n        if False:\n            PROFILE_PATH.unlink()\n        else:\n            pass",
     "confirm_color_analysis_update: stop rolling back a rejected write, leaving a half-applied invalid file"),
]


def run_tests_against(skill_dir):
    r1 = subprocess.run([sys.executable, str(skill_dir / "tests" / "run_eval.py")], capture_output=True, text=True)
    r2 = subprocess.run([sys.executable, str(skill_dir / "tests" / "audit_vocab_coverage.py")], capture_output=True, text=True)
    return r1, r2


def main():
    killed = 0
    survived = []
    skipped = []

    for file, old, new, desc in MUTATIONS:
        with tempfile.TemporaryDirectory() as tmp:
            mutant_dir = Path(tmp) / "skill"
            shutil.copytree(ROOT, mutant_dir)
            target = mutant_dir / file

            text = target.read_text()
            if old not in text:
                skipped.append(desc)
                print(f"SKIP (pattern not found, mutation stale): {desc}")
                continue
            target.write_text(text.replace(old, new, 1))

            r1, r2 = run_tests_against(mutant_dir)
            if r1.returncode != 0 or r2.returncode != 0:
                killed += 1
                print(f"KILLED: {desc}")
            else:
                survived.append(desc)
                print(f"SURVIVED (no test caught this!): {desc}")

    total = len(MUTATIONS) - len(skipped)
    print(f"\n{killed}/{total} mutant(s) killed" + (f", {len(skipped)} skipped (stale)" if skipped else "") + ".")

    if survived:
        print("\nSurviving mutants — real test-suite blind spots:")
        for s in survived:
            print(f"  - {s}")
        sys.exit(1)

    print("No survivors — the test suite catches every mutation tried.")


if __name__ == "__main__":
    main()
