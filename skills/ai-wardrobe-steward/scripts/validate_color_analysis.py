#!/usr/bin/env python3
"""Validate profile/color_analysis.yaml's structure against vocab.yaml.

Deliberately YAML for human-editability, same as the other profile files —
this is the independent safety net for hand-edits, run any time, not just
after a scripted write.

Unlike the taste-preference files, this isn't the {value, confidence,
importance, evidence} shape — it's expert-stated reference data (a
professional seasonal analysis) with a source and a versioned change_log.

Usage: python3 validate_color_analysis.py
"""
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is required: pip install pyyaml")

ROOT = Path(__file__).resolve().parent.parent
ANALYSIS_PATH = ROOT / "profile" / "color_analysis.yaml"
VOCAB_PATH = ROOT / "vocab.yaml"

HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

# Every top-level key the confirm script's "set" is allowed to write. Kept in
# sync with confirm_color_analysis_update.KNOWN_FIELDS by audit_vocab_coverage.py.
KNOWN_FIELDS = {
    "season", "undertone", "value", "intensity", "contrast_level", "descriptors",
    "priority_families", "priority_note", "core_colors", "palette", "leather_palette",
    "exclude", "metals", "fabric_finish", "combination_styles", "footwear", "eyewear",
    "follow_ups", "source", "analysis_date",
}


def check_swatch(entry, label, errors, require_family=False, valid_families=None):
    if not isinstance(entry, dict):
        errors.append(f"{label} must be a mapping with 'hex'/'name', got {type(entry).__name__}: {entry!r}")
        return
    if not isinstance(entry.get("hex"), str) or not HEX_RE.match(entry["hex"]):
        errors.append(f"{label} 'hex' must be '#RRGGBB', got {entry.get('hex')!r}")
    if not isinstance(entry.get("name"), str) or not entry["name"].strip():
        errors.append(f"{label} 'name' must be a non-empty string, got {entry.get('name')!r}")
    if require_family:
        fam = entry.get("family")
        if fam not in (valid_families or set()):
            errors.append(f"{label} 'family' must be one of {sorted(valid_families or [])}, got {fam!r}")


def main():
    if not ANALYSIS_PATH.exists():
        print("OK — no color_analysis.yaml on file (a colour analysis hasn't been captured yet)")
        return

    with open(VOCAB_PATH) as f:
        vocab = yaml.safe_load(f)
    with open(ANALYSIS_PATH) as f:
        data = yaml.safe_load(f)

    errors = []

    if not isinstance(data, dict):
        sys.exit(f"FAIL — color_analysis.yaml top level must be a mapping, got {type(data).__name__}")

    if not isinstance(data.get("version"), int):
        errors.append(f"'version' must be an int, got {data.get('version')!r}")

    for extra in set(data) - KNOWN_FIELDS - {"version", "last_updated", "change_log"}:
        errors.append(f"unknown top-level field '{extra}' — not something the confirm script writes")

    valid_families = set(vocab.get("palette_families", []))

    if "season" in data and data["season"] not in vocab.get("color_seasons", []):
        errors.append(f"season '{data['season']}' not in vocab.yaml color_seasons")
    if "undertone" in data and data["undertone"] not in vocab.get("undertones", []):
        errors.append(f"undertone '{data['undertone']}' not in vocab.yaml undertones")
    if "contrast_level" in data and data["contrast_level"] not in vocab.get("contrast_levels", []):
        errors.append(f"contrast_level '{data['contrast_level']}' not in vocab.yaml contrast_levels")

    if "priority_families" in data:
        if not isinstance(data["priority_families"], list):
            errors.append("'priority_families' must be a list")
        else:
            for fam in data["priority_families"]:
                if fam not in valid_families:
                    errors.append(f"priority_families entry '{fam}' not in vocab.yaml palette_families")

    palette = data.get("palette")
    if palette is None:
        errors.append("'palette' is required (a colour analysis with no palette is unusable)")
    elif not isinstance(palette, list) or not palette:
        errors.append(f"'palette' must be a non-empty list, got {palette!r}")
    else:
        seen = set()
        for i, entry in enumerate(palette):
            check_swatch(entry, f"palette[{i}]", errors, require_family=True, valid_families=valid_families)
            if isinstance(entry, dict) and isinstance(entry.get("hex"), str):
                key = entry["hex"].upper()
                if key in seen:
                    errors.append(f"palette[{i}] duplicate hex {entry['hex']}")
                seen.add(key)

    for i, entry in enumerate(data.get("leather_palette", []) or []):
        check_swatch(entry, f"leather_palette[{i}]", errors)

    exclude = data.get("exclude")
    if exclude is not None:
        if not isinstance(exclude, dict):
            errors.append("'exclude' must be a mapping")
        else:
            for i, entry in enumerate(exclude.get("hard_reject", []) or []):
                check_swatch(entry, f"exclude.hard_reject[{i}]", errors)
            if "undertone_examples" in exclude and not isinstance(exclude["undertone_examples"], list):
                errors.append("'exclude.undertone_examples' must be a list")

    # core_colors must name actual palette swatches
    if isinstance(palette, list):
        palette_names = {e["name"] for e in palette if isinstance(e, dict) and isinstance(e.get("name"), str)}
        for name in data.get("core_colors", []) or []:
            if name not in palette_names:
                errors.append(f"core_colors entry '{name}' isn't a name in `palette`")

    change_log = data.get("change_log", [])
    if not isinstance(change_log, list):
        errors.append(f"'change_log' must be a list, got {type(change_log).__name__}")
    else:
        for i, entry in enumerate(change_log):
            if not isinstance(entry, dict) or not all(k in entry for k in ("date", "version", "summary")):
                errors.append(f"change_log[{i}] must be a mapping with 'date'/'version'/'summary'")

    if errors:
        print(f"FAIL — {len(errors)} issue(s):")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)

    n = len(palette) if isinstance(palette, list) else 0
    print(f"OK — color_analysis.yaml valid (version {data.get('version')}, {n} palette swatches, season {data.get('season')})")


if __name__ == "__main__":
    main()
