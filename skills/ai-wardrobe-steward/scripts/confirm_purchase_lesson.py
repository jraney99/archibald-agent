#!/usr/bin/env python3
"""Confirm one or all pending purchase lessons — moves them from
profile/pending_purchase_lessons.json into profile/purchase_lessons.json.

Purchase Lessons are regret memory (PART 3): a mistake noticed after the
fact ("I never wear these pants") turned into something future
recommendations can actually avoid repeating, not just a prose note the
agent has to remember verbatim. A lesson is:

  {"id": ..., "date": ..., "lesson_type": "fit"|"brand"|"color"|"quality"|
   "lifestyle"|"other", "item_description": "...",
   "linked_closet_item_id": <id or null>, "lesson": "...",
   "confidence": 0-100, "evidence": [{"date", "note"}, ...]}

Mirrors this workspace's pending/confirm pattern — drafting is automatic,
confirmation is the one deliberate checkpoint before it becomes memory that
shapes future recommendations. A new lesson matching an existing one (same
lesson_type + a similar item_description) reinforces it — confidence
updates, evidence appends (capped at 5) — rather than creating a duplicate.

Usage:
  python3 confirm_purchase_lesson.py --list            # show the queue, no changes
  python3 confirm_purchase_lesson.py <id>               # confirm one
  python3 confirm_purchase_lesson.py --all              # confirm everything queued
"""
import json
import subprocess
import sys
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is required: pip install pyyaml")

ROOT = Path(__file__).resolve().parent.parent
PENDING_PATH = ROOT / "profile" / "pending_purchase_lessons.json"
LESSONS_PATH = ROOT / "profile" / "purchase_lessons.json"
CLOSET_PATH = ROOT / "profile" / "closet.json"
VOCAB_PATH = ROOT / "vocab.yaml"
VALIDATE_SCRIPT = Path(__file__).resolve().parent / "validate_purchase_lessons.py"

EVIDENCE_CAP = 5
DESCRIPTION_SIMILARITY_THRESHOLD = 0.8


def load(path, default):
    if not path.exists():
        return default
    with open(path) as f:
        return json.load(f)


def save(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def is_valid_score(n):
    return isinstance(n, int) and not isinstance(n, bool) and 0 <= n <= 100


def is_valid_evidence(evidence):
    return isinstance(evidence, list) and all(
        isinstance(e, dict) and isinstance(e.get("date"), str) and isinstance(e.get("note"), str)
        for e in evidence
    )


def descriptions_match(a, b):
    if not isinstance(a, str) or not isinstance(b, str) or not a or not b:
        return False
    a_l, b_l = a.strip().lower(), b.strip().lower()
    if a_l in b_l or b_l in a_l:
        return True
    return SequenceMatcher(None, a_l, b_l).ratio() > DESCRIPTION_SIMILARITY_THRESHOLD


def validate_entry(entry, vocab, closet):
    errors = []
    if not entry.get("id"):
        errors.append("missing 'id' (assign one when drafting, e.g. 'lesson-slim-chinos')")
    if not entry.get("lesson_type") in vocab["purchase_lesson_types"]:
        errors.append(f"lesson_type '{entry.get('lesson_type')}' not in vocab.yaml purchase_lesson_types")
    if not entry.get("item_description"):
        errors.append("missing 'item_description'")
    if not entry.get("lesson"):
        errors.append("missing 'lesson'")
    if not is_valid_score(entry.get("confidence")):
        errors.append(f"'confidence' must be an int 0-100, got {entry.get('confidence')!r}")
    if "evidence" in entry and not is_valid_evidence(entry["evidence"]):
        errors.append("'evidence' must be a list of {date, note} objects")
    linked = entry.get("linked_closet_item_id")
    if linked and not any(item.get("id") == linked for item in closet):
        errors.append(f"linked_closet_item_id '{linked}' not found in closet.json")
    return errors


def merge_lesson(existing, new):
    result = dict(existing)
    result["lesson"] = new.get("lesson", existing.get("lesson"))
    result["confidence"] = new.get("confidence", existing.get("confidence"))
    if new.get("linked_closet_item_id"):
        result["linked_closet_item_id"] = new["linked_closet_item_id"]
    result["evidence"] = (existing.get("evidence", []) + new.get("evidence", []))[-EVIDENCE_CAP:]
    return result


def confirm(ids):
    with open(VOCAB_PATH) as f:
        vocab = yaml.safe_load(f)
    pending = load(PENDING_PATH, [])
    lessons = load(LESSONS_PATH, [])
    closet = load(CLOSET_PATH, [])

    if not isinstance(pending, list):
        sys.exit(f"pending_purchase_lessons.json top level must be a list, got {type(pending).__name__}")
    if not all(isinstance(e, dict) for e in pending):
        sys.exit("pending_purchase_lessons.json must be a list of mappings — found a non-mapping entry")

    if ids == "__all__":
        to_confirm = pending
        remaining = []
    else:
        to_confirm = [e for e in pending if e.get("id") == ids]
        remaining = [e for e in pending if e.get("id") != ids]
        if not to_confirm:
            print(f"No pending lesson with id '{ids}'.")
            list_pending(pending)
            sys.exit(1)

    all_errors = []
    for entry in to_confirm:
        all_errors.extend(validate_entry(entry, vocab, closet))
    if all_errors:
        sys.exit("Refusing to confirm — " + "; ".join(all_errors))

    confirmed_ids = []
    for entry in to_confirm:
        match = next((
            l for l in lessons
            if l.get("lesson_type") == entry.get("lesson_type")
            and descriptions_match(l.get("item_description"), entry.get("item_description"))
        ), None)
        if match:
            merged = merge_lesson(match, entry)
            match.clear()
            match.update(merged)
            confirmed_ids.append(match["id"])
        else:
            entry.setdefault("date", date.today().isoformat())
            entry.setdefault("evidence", [])
            entry["evidence"] = entry["evidence"][-EVIDENCE_CAP:]
            lessons.append(entry)
            confirmed_ids.append(entry["id"])

    save(LESSONS_PATH, lessons)
    save(PENDING_PATH, remaining)
    print(f"Confirmed {len(to_confirm)} lesson(s): {', '.join(confirmed_ids)}")

    result = subprocess.run([sys.executable, str(VALIDATE_SCRIPT)], capture_output=True, text=True)
    print(result.stdout.strip())
    if result.returncode != 0:
        print("Validation failed after write — review purchase_lessons.json.", file=sys.stderr)
        sys.exit(1)


def list_pending(pending):
    if not pending:
        print("Pending queue is empty.")
        return
    if not isinstance(pending, list) or not all(isinstance(e, dict) for e in pending):
        sys.exit("pending_purchase_lessons.json must be a list of mappings — file may be corrupted.")
    print(f"{len(pending)} lesson(s) pending confirmation:")
    for entry in pending:
        print(f"  - {entry.get('id', '(no id yet)')}: [{entry.get('lesson_type')}] {entry.get('item_description')} — {entry.get('lesson')}")


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)

    arg = sys.argv[1]
    if arg == "--list":
        list_pending(load(PENDING_PATH, []))
    elif arg == "--all":
        confirm("__all__")
    else:
        confirm(arg)


if __name__ == "__main__":
    main()
