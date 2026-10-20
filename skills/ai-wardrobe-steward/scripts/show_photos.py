#!/usr/bin/env python3
"""Print ready-to-send MEDIA: directives for one or more closet items.

Exists because hand-typing "MEDIA:<absolute path>" lines for several items
in one reply is unreliable in practice (2026-09-16: the live agent said
"let me send the individual photos" twice in a row and never actually
emitted a single MEDIA: line either time, then falsely claimed it already
had). This turns "show me photos of X, Y, Z" into one deterministic call
instead of several hand-formatted lines the model has to remember to type.

Prints one MEDIA:<path> line per item found with a real photo on disk, in
the order given. For anything unresolvable, prints a plain warning line
instead (no fabricated path) — relay this script's stdout as-is, don't
retype or reformat it.

Usage:
    python3 show_photos.py <item-id> [<item-id> ...]
    python3 show_photos.py --search "polo"   # case-insensitive id/brand/model/color match
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLOSET_PATH = ROOT / "profile" / "closet.json"


def load_closet():
    if not CLOSET_PATH.exists():
        return []
    return json.loads(CLOSET_PATH.read_text())


def resolve_photo_line(item):
    item_id = item.get("id", "?")
    photo = item.get("photo")
    if not photo or not isinstance(photo, str):
        return f"(no photo on file for {item_id})"
    photo_path = ROOT / photo
    if not photo_path.exists():
        return f"(photo file missing for {item_id}: {photo})"
    return f"MEDIA:{photo_path}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("ids", nargs="*", help="exact closet item id(s)")
    parser.add_argument("--search", help="case-insensitive substring match against id/brand/model/color")
    args = parser.parse_args()

    closet = load_closet()
    by_id = {item.get("id"): item for item in closet}

    matched = []
    if args.search:
        needle = args.search.lower()
        matched = [
            item for item in closet
            if needle in " ".join(str(item.get(k, "")) for k in ("id", "brand", "model", "color")).lower()
        ]
        if not matched:
            print(f"No closet items matched '{args.search}'.", file=sys.stderr)
            sys.exit(1)
    else:
        if not args.ids:
            print("Provide item id(s) or --search.", file=sys.stderr)
            sys.exit(1)
        for item_id in args.ids:
            item = by_id.get(item_id)
            if not item:
                print(f"(no closet item with id '{item_id}')")
                continue
            matched.append(item)

    for item in matched:
        print(resolve_photo_line(item))


if __name__ == "__main__":
    main()
