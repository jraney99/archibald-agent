#!/usr/bin/env python3
"""Validate profile/purchase_lessons.json against vocab.yaml's purchase_lesson_types.

Usage: python3 validate_purchase_lessons.py
"""
import json
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is required: pip install pyyaml")

ROOT = Path(__file__).resolve().parent.parent
LESSONS_PATH = ROOT / "profile" / "purchase_lessons.json"
CLOSET_PATH = ROOT / "profile" / "closet.json"
VOCAB_PATH = ROOT / "vocab.yaml"


def is_valid_score(n):
    return isinstance(n, int) and not isinstance(n, bool) and 0 <= n <= 100


def is_valid_evidence(evidence):
    return isinstance(evidence, list) and all(
        isinstance(e, dict) and isinstance(e.get("date"), str) and isinstance(e.get("note"), str)
        for e in evidence
    )


def validate_entry(entry, index, vocab, closet_ids):
    errors = []
    prefix = f"lesson[{index}] (id={entry.get('id', '?')})"

    for field in ("id", "date", "lesson_type", "item_description", "lesson"):
        if not entry.get(field):
            errors.append(f"{prefix}: missing required field '{field}'")

    if entry.get("lesson_type") and entry["lesson_type"] not in vocab["purchase_lesson_types"]:
        errors.append(f"{prefix}: lesson_type '{entry['lesson_type']}' not in vocab.yaml purchase_lesson_types")

    if not is_valid_score(entry.get("confidence")):
        errors.append(f"{prefix}: 'confidence' must be an int 0-100, got {entry.get('confidence')!r}")

    if "evidence" in entry and not is_valid_evidence(entry["evidence"]):
        errors.append(f"{prefix}: 'evidence' must be a list of {{date, note}} objects")

    linked = entry.get("linked_closet_item_id")
    if linked and linked not in closet_ids:
        errors.append(f"{prefix}: linked_closet_item_id '{linked}' not found in closet.json")

    return errors


def main():
    if not VOCAB_PATH.exists():
        sys.exit(f"vocab.yaml not found at {VOCAB_PATH}")
    with open(VOCAB_PATH) as f:
        vocab = yaml.safe_load(f)

    if not LESSONS_PATH.exists():
        sys.exit(f"purchase_lessons.json not found at {LESSONS_PATH}")
    with open(LESSONS_PATH) as f:
        lessons = json.load(f)

    if not isinstance(lessons, list):
        sys.exit(f"FAIL — purchase_lessons.json top level must be a list, got {type(lessons).__name__}")

    closet_ids = set()
    if CLOSET_PATH.exists():
        with open(CLOSET_PATH) as f:
            closet = json.load(f)
        closet_ids = {item.get("id") for item in closet if isinstance(item, dict)}

    ids_seen = {}
    all_errors = []
    for i, entry in enumerate(lessons):
        if not isinstance(entry, dict):
            all_errors.append(f"lesson[{i}] must be a mapping, got {type(entry).__name__}: {entry!r}")
            continue
        all_errors.extend(validate_entry(entry, i, vocab, closet_ids))
        entry_id = entry.get("id")
        if entry_id:
            ids_seen.setdefault(entry_id, []).append(i)

    for entry_id, indexes in ids_seen.items():
        if len(indexes) > 1:
            all_errors.append(f"duplicate id '{entry_id}' used at indexes {indexes}")

    if all_errors:
        print(f"FAIL — {len(all_errors)} issue(s):")
        for e in all_errors:
            print(f"  - {e}")
        sys.exit(1)

    print(f"OK — {len(lessons)} lesson(s) validated against vocab.yaml")


if __name__ == "__main__":
    main()
