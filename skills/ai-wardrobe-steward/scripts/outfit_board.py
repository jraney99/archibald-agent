#!/usr/bin/env python3
"""Build a Telegram-ready JPEG flat lay from exact closet-item photos.

The script uses only local files. It writes a real JPEG and prints one MEDIA:
directive on success. If an item or photo is unavailable, it exits without
claiming that an image was created so callers can fall back to show_photos.py.
"""
import argparse
import base64
import html
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLOSET_PATH = ROOT / "profile" / "closet.json"
OUTPUT_DIR = ROOT / "profile" / "generated" / "outfit-boards"


def fail(message):
    print(f"(outfit board unavailable: {message})")
    raise SystemExit(1)


def image_uri(path):
    suffix = path.suffix.lower()
    mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}.get(suffix)
    if not mime:
        fail(f"unsupported photo format for {path.name}")
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


CATEGORY_NOUN = {"specialty_footwear": "golf shoes", "athletic_shoes": "trainers", "dress_shoes": "dress shoes",
                 "t-shirt": "tee", "full_body": "suit"}
# the order a person dresses in, so the board reads top to bottom
SLOT_ORDER = {"shirt": 0, "t-shirt": 0, "polo": 0, "sweater": 1, "vest": 2, "jacket": 2, "coat": 2, "blazer": 2,
              "suit": 0, "pants": 3, "jeans": 3, "shorts": 3, "activewear": 3,
              "sneakers": 4, "boots": 4, "loafers": 4, "dress_shoes": 4, "athletic_shoes": 4, "specialty_footwear": 4,
              "belt": 5, "watch": 6, "sunglasses": 6, "hat": 6, "tie": 6, "bag": 6, "scarf": 6}


def short_colour(value):
    """'navy with whiskey-brown dogleg stripe' -> 'navy'. The board has room for a colour, not a description."""
    text = str(value or "").split(",")[0].split(" / ")[0].split(" with ")[0].strip()
    words = [w for w in text.split() if w.lower() not in ("heather", "light", "dark", "washed", "faded")] or text.split()
    return " ".join(words[:2])


def item_label(item):
    """Line 1: who made it. Model codes are noise on a phone — 2026-10-13 a board read
    'G/FORE Gallivanter (style GMF000055, 'Snow' cut off mid-word."""
    brand = str(item.get("brand") or "").split("(")[0].strip()
    return brand or str(item.get("id", "item")).replace("-", " ").title()


def item_sublabel(item):
    """Line 2: colour and what the thing is, in plain words."""
    category = CATEGORY_NOUN.get(item.get("category"), str(item.get("category") or "").replace("_", " "))
    return " ".join(x for x in (short_colour(item.get("color")), category) if x)


def build_svg(items, title, bare=False):
    """One row per four pieces, square tiles, so the whole outfit fits a phone screen.

    2026-10-13: the old board was 1400x1550 for four items — a tall scroll on the phone,
    with each photo letterboxed into a 610px card at a different apparent scale. The user:
    "I dont really like the way it has looked."
    """
    items = sorted(items, key=lambda pair: SLOT_ORDER.get((pair[0] or {}).get("category"), 9))
    count = len(items)
    cols = count if count <= 4 else 3
    rows = (count + cols - 1) // cols
    tile, gap, margin = 300, 24, 46
    header, label_h = (18, 18) if bare else (112, 104)
    card_w = tile
    card_h = tile + label_h
    width = 2 * margin + cols * card_w + (cols - 1) * gap
    height = header + rows * card_h + (rows - 1) * gap + margin
    chunks = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">',
        # sips does not resolve percentage units here, so the page is sized absolutely
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="#f3f0e9"/>',
    ]
    if not bare:
        chunks.append(f'<text x="{margin}" y="68" font-family="Helvetica,Arial,sans-serif" font-size="38" font-weight="600" fill="#202522">{html.escape(title)}</text>')
    for index, (item, photo) in enumerate(items):
        row, col = divmod(index, cols)
        x = margin + col * (card_w + gap)
        y = header + row * (card_h + gap)
        chunks.append(f'<rect x="{x}" y="{y}" width="{card_w}" height="{card_h}" rx="18" fill="#ffffff"/>')
        if photo:
            chunks.extend([
                f'<rect x="{x + 14}" y="{y + 14}" width="{tile - 28}" height="{tile - 28}" rx="12" fill="#faf8f4"/>',
                f'<image x="{x + 14}" y="{y + 14}" width="{tile - 28}" height="{tile - 28}" href="{image_uri(photo)}" preserveAspectRatio="xMidYMid meet"/>'])
        else:
            chunks.extend([
                f'<rect x="{x + 14}" y="{y + 14}" width="{tile - 28}" height="{tile - 28}" rx="12" fill="#e9e5dc"/>',
                f'<text x="{x + card_w // 2}" y="{y + tile // 2 + 8}" text-anchor="middle" font-family="Helvetica,Arial,sans-serif" font-size="20" fill="#8a918c">no photo yet</text>'])
        if bare:
            continue
        chunks.extend([
            f'<text x="{x + 18}" y="{y + tile + 36}" font-family="Helvetica,Arial,sans-serif" font-size="22" font-weight="600" fill="#202522">{html.escape(item_label(item)[:24])}</text>',
            f'<text x="{x + 18}" y="{y + tile + 70}" font-family="Helvetica,Arial,sans-serif" font-size="19" fill="#69716c">{html.escape(item_sublabel(item)[:28])}</text>',
        ])
    chunks.append("</svg>")
    return "".join(chunks)


def main():
    parser = argparse.ArgumentParser(description="Create a JPEG flat lay from closet item photos.")
    parser.add_argument("ids", nargs="+", help="exact closet item IDs, in display order")
    parser.add_argument("--title", default="Outfit flat lay")
    parser.add_argument("--bare", action="store_true", help="photos only: no title, no per-item labels (the caption says all of that)")
    parser.add_argument("--output", help="optional JPEG output path")
    args = parser.parse_args()

    if len(args.ids) > 6:
        fail("use no more than 6 items per board")
    closet = json.loads(CLOSET_PATH.read_text()) if CLOSET_PATH.exists() else []
    by_id = {item.get("id"): item for item in closet}
    resolved = []
    for item_id in args.ids:
        item = by_id.get(item_id)
        if not item:
            fail(f"no closet item with id '{item_id}'")
        photo_ref = item.get("photo")
        photo = ROOT / photo_ref if isinstance(photo_ref, str) else None
        # 2026-09-28: a piece with no photo yet (added from an order email, or never
        # photographed) used to sink the whole board. It gets a labelled tile instead.
        resolved.append((item, photo if photo and photo.exists() else None))

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    output = Path(args.output).expanduser().resolve() if args.output else OUTPUT_DIR / f"outfit-{stamp}.jpg"
    output.parent.mkdir(parents=True, exist_ok=True)
    svg = output.with_suffix(".svg")
    svg.write_text(build_svg(resolved, args.title, args.bare))
    result = subprocess.run(
        ["/usr/bin/sips", "-s", "format", "jpeg", str(svg), "--out", str(output)],
        capture_output=True, text=True,
    )
    svg.unlink(missing_ok=True)
    if result.returncode or not output.exists() or output.stat().st_size == 0:
        output.unlink(missing_ok=True)
        fail("JPEG rendering failed")
    print(f"MEDIA:{output}")


if __name__ == "__main__":
    main()
