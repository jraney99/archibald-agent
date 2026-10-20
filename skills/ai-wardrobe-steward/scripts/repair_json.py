#!/usr/bin/env python3
"""Repair a JSON file that holds a valid document followed by trailing junk.

  python3 repair_json.py <file> [--dry-run]

The corrupt original is copied to state/corrupt/<name>.<timestamp> before the
file is rewritten (atomically), so nothing is lost. Exits 1 when the file is
already valid (nothing to do) or when no valid prefix exists (needs a human).
"""
import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _store import recover_prefix, save_json  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def repair(path: Path, dry_run: bool = False, backup_dir=None) -> dict:
    raw = path.read_text()
    try:
        json.loads(raw)
        return {"file": str(path), "status": "already-valid"}
    except json.JSONDecodeError:
        pass
    try:
        data, leftover = recover_prefix(raw)
    except json.JSONDecodeError as exc:
        sys.exit(f"{path}: no valid JSON prefix ({exc}); needs a human.")
    result = {"file": str(path), "status": "repaired", "recovered_type": type(data).__name__,
              "recovered_entries": len(data), "dropped_bytes": len(leftover)}
    if dry_run:
        result["status"] = "would-repair"
        return result
    backup_dir = backup_dir or ROOT / "state" / "corrupt"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"{path.name}.{datetime.now().strftime('%Y%m%dT%H%M%S')}"
    shutil.copy2(path, backup)
    save_json(path, data)
    result["backup"] = str(backup)
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("file", type=Path)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    print(json.dumps(repair(a.file, a.dry_run), indent=2))


if __name__ == "__main__":
    main()
