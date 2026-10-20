#!/usr/bin/env python3
"""Apply the pending colour-analysis update — moves the proposed diff in
profile/pending_color_analysis_update.json into profile/color_analysis.yaml,
bumps the version, and logs it in change_log.

color_analysis.yaml is expert-stated reference data (a professional seasonal
analysis), not a passively-accumulated taste preference — so this isn't the
add/remove/merge-by-value machinery the style files use. A "set" replaces a
whole top-level field. The first import sets everything at once; a later
tweak ("drop terracotta from the exclude examples", "add mint to the
palette") sets just the one field it touches.

Usage:
  python3 confirm_color_analysis_update.py --show     # print the pending diff
  python3 confirm_color_analysis_update.py            # apply it

Pending file shape (a single proposed diff, not a queue):
{
  "review_summary": "plain-language description of the change",
  "set": {
    "palette": [ {"hex": "#4C98A2", "name": "Blued Moss", "family": "blue-greens"}, ... ],
    "exclude": { "hard_reject": [...], "undertone_examples": [...] }
  }
}
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
PENDING_PATH = ROOT / "profile" / "pending_color_analysis_update.json"
PROFILE_PATH = ROOT / "profile" / "color_analysis.yaml"
VALIDATE_SCRIPT = Path(__file__).resolve().parent / "validate_color_analysis.py"

# Kept in sync with validate_color_analysis.KNOWN_FIELDS by audit_vocab_coverage.py.
KNOWN_FIELDS = {
    "season", "undertone", "value", "intensity", "contrast_level", "descriptors",
    "priority_families", "priority_note", "core_colors", "palette", "leather_palette",
    "exclude", "metals", "fabric_finish", "combination_styles", "footwear", "eyewear",
    "follow_ups", "source", "analysis_date",
}


def load_pending():
    if not PENDING_PATH.exists():
        return {}
    with open(PENDING_PATH) as f:
        return json.load(f)


def validate_diff(diff):
    if not isinstance(diff, dict):
        sys.exit(f"pending_color_analysis_update.json top level must be a mapping (a single diff), got {type(diff).__name__}")
    changes = diff.get("set", {})
    if not isinstance(changes, dict):
        sys.exit(f"'set' must be a mapping of field -> new value, got {type(changes).__name__}")
    if not changes:
        sys.exit("'set' is empty — nothing to apply.")
    unknown = set(changes) - KNOWN_FIELDS
    if unknown:
        sys.exit(f"unknown field(s) in 'set': {sorted(unknown)}. Valid: {sorted(KNOWN_FIELDS)}")


def main():
    pending = load_pending()
    if not pending:
        print("No pending colour-analysis update.")
        return

    if len(sys.argv) == 2 and sys.argv[1] == "--show":
        print(json.dumps(pending, indent=2))
        return

    validate_diff(pending)

    original = PROFILE_PATH.read_text() if PROFILE_PATH.exists() else None

    profile = {}
    if original is not None:
        profile = yaml.safe_load(original) or {}

    for field, value in pending["set"].items():
        profile[field] = value

    profile["version"] = profile.get("version", 0) + 1
    profile["last_updated"] = date.today().isoformat()
    profile.setdefault("change_log", []).append({
        "date": profile["last_updated"],
        "version": profile["version"],
        "summary": pending.get("review_summary", ""),
    })

    with open(PROFILE_PATH, "w") as f:
        yaml.dump(profile, f, sort_keys=False, allow_unicode=True)

    # Validate before we clear the pending diff. If the resulting file is
    # invalid, roll the write back so a rejected update never leaves a
    # half-applied color_analysis.yaml behind (the pre-write validate_diff
    # only checks field names — the structural checks live in the validator).
    result = subprocess.run([sys.executable, str(VALIDATE_SCRIPT)], capture_output=True, text=True)
    if result.returncode != 0:
        if original is None:
            PROFILE_PATH.unlink()
        else:
            PROFILE_PATH.write_text(original)
        print(result.stdout.strip())
        print("Update rejected — it would make color_analysis.yaml invalid; nothing was written. "
              "Fix the pending diff and try again.", file=sys.stderr)
        sys.exit(1)

    with open(PENDING_PATH, "w") as f:
        json.dump({}, f)

    print(f"Applied — color_analysis.yaml now at version {profile['version']}.")
    print(result.stdout.strip())


if __name__ == "__main__":
    main()
