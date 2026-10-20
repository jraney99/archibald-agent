#!/usr/bin/env python3
"""Audit photo references — DESIGN.md calls a closet item's photo "never
disposable," but nothing enforces that the file actually exists. This finds:
  - closet items whose 'photo' field is set but the file is missing (broken reference)
  - active closet items with no photo at all (never got one — a nudge, not an error)
  - files in profile/photos/closet/ that no item actually references (orphans)

Usage: python3 check_photos.py
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLOSET_PATH = ROOT / "profile" / "closet.json"
CLOSET_PHOTOS_DIR = ROOT / "profile" / "photos" / "closet"


def load(path):
    if not path.exists():
        return []
    with open(path) as f:
        return json.load(f)


def main():
    closet = load(CLOSET_PATH)

    broken = []
    missing = []
    malformed = []
    referenced_files = set()

    for item in closet:
        photo = item.get("photo")
        item_id = item.get("id", "?")
        if not photo:
            if item.get("lifecycle_state") == "active":
                missing.append(item_id)
            continue
        if not isinstance(photo, str):
            malformed.append(item_id)
            continue
        photo_path = ROOT / photo
        referenced_files.add(photo_path.resolve())
        if not photo_path.exists():
            broken.append((item_id, photo))

    orphans = []
    if CLOSET_PHOTOS_DIR.exists():
        for f in CLOSET_PHOTOS_DIR.iterdir():
            if f.is_file() and f.name != ".gitkeep" and f.resolve() not in referenced_files:
                orphans.append(f.name)

    if broken:
        print(f"Broken references ({len(broken)}) — 'photo' field set but file missing:")
        for item_id, photo in broken:
            print(f"  - {item_id}: {photo}")

    if missing:
        print(f"Active item(s) with no photo yet ({len(missing)}): {', '.join(missing)}")

    if orphans:
        print(f"Orphaned file(s) in profile/photos/closet/ not referenced by any item ({len(orphans)}): {', '.join(orphans)}")

    if malformed:
        print(f"Item(s) with a malformed 'photo' field, not a string ({len(malformed)}): {', '.join(malformed)}")

    if not broken and not missing and not orphans and not malformed:
        print("All photo references are consistent.")


if __name__ == "__main__":
    main()
