#!/usr/bin/env python3
"""Send the Mon/Thu scan's finds with a 👍 / 👎 under each one.

  python3 send_find.py --text "<the summary you wrote>" \
      --find "<decision-id>|<short label>[|brand|material|#hex|<image url>|<caption>]" [--find "..."] [--dry-run]

WHY (2026-10-13): the scan had surfaced 18 recommendations and learned nothing from any of
them — every outcome was "unknown". The user will not forward inspiration to Telegram ("im just
not going to remember"), but he will tap a button on a message already on his screen, so the
finds themselves become the taste data. Approved by him on 2026-10-13.

The prose is the model's; the buttons are deterministic. Each find carries its
purchase_decisions id so the tap lands on the right recommendation, and the optional
brand / material / measured colour so the tap is attributable — a 👎 on "bright red
polyester" is only a lesson if those words are stored with it. See find_preferences.py,
which turns the accumulated taps into the guidance the next scan reads.

PHOTOS (2026-10-16, approved by the user: "it doesnt show me the image of it and the link
takes me to the checkout"). A find may carry a 6th field, the product image URL, and a
7th, its own caption. When ANY find has an image the finds are sent as ONE MESSAGE EACH —
photo plus caption plus that find's own 👍 / 👎 — because Telegram cannot hang one
keyboard under two photos. Two finds therefore arrive as two messages; that cost was
put to him and accepted. With no image anywhere the old single combined message is sent
unchanged, so the Mon/Thu cron keeps working untouched.

The caption is the model's prose and should carry the product page link FIRST and the
cart link second, if at all: he asked to look before he buys. Anything after the 6th
pipe is caption, pipes included, so prose is never truncated by its own punctuation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent / "shared"))
sys.path.insert(0, str(ROOT / "scripts"))
from data_paths import path as data_path  # noqa: E402
from _store import load_json, save_json  # noqa: E402

STORE = data_path("find_feedback.json")
MAX_FINDS = 3
# A find may not be sent without a colour that was MEASURED against his palette. Before
# 2026-10-13 the cron said to check colour "when colour matters", which left it to judgment —
# so the palette could be skipped silently on any given run. The user asked for a guarantee, and
# an instruction is not one: this is the gate. "n/a" is the only escape and it must be typed
# deliberately, for something with no single colour (a pattern, a multi-colour weave).
HEX_RE = re.compile(r"^#?[0-9A-Fa-f]{6}$")
NO_COLOUR = "n/a"
IMAGE_RE = re.compile(r"^https?://", re.I)


def tidy(text: str) -> str:
    """Normalise spacing WITHOUT destroying the line structure.

    WHY (2026-10-16): this was `" ".join(text.split())`, which flattened every blank
    line, so every find the user ever received arrived as one unbroken block — the opposite
    of the digestibility he keeps asking for. Runs of spaces inside a line still
    collapse (model output is untidy), consecutive blank lines collapse to one, but a
    deliberate paragraph break survives.
    """
    lines = [" ".join(line.split()) for line in (text or "").splitlines()]
    out: list = []
    for line in lines:
        if not line and (not out or not out[-1]):
            continue
        out.append(line)
    return "\n".join(out).strip()


def token_for(decision_id: str, label: str) -> str:
    return hashlib.sha256(f"{decision_id}|{label}".encode()).hexdigest()[:12]


def row_for(token: str, label: str) -> list:
    return [{"text": f"👍 {label[:18]}", "callback_data": f"confirm:find-{token}"},
            {"text": "👎", "callback_data": f"skip:find-{token}"}]


def build(finds: list) -> tuple:
    """[(decision_id, label, brand, material, hex, image, caption)] -> ({token: record}, keyboard).

    The keyboard holds every find's row, which is what the single combined message wants.
    Per-find messages use row_for directly. The record shape is unchanged: find_feedback.py
    reads it and must keep working against rows written before photos existed.
    """
    rows, records = [], {}
    for find in finds[:MAX_FINDS]:
        decision_id, label = find[0], find[1]
        brand, material, colour = (list(find) + ["", "", ""])[2:5]
        token = token_for(decision_id, label)
        records[token] = {"decision_id": decision_id, "label": label[:60], "status": "asked",
                          "brand": brand[:40], "material": material[:40], "colour_hex": colour[:8],
                          "asked_at": datetime.now(timezone.utc).isoformat()}
        rows.append(row_for(token, label))
    return records, {"inline_keyboard": rows}


def parse_find(raw: str) -> tuple:
    """One --find string -> (decision_id, label, brand, material, hex, image, caption)."""
    parts = [x.strip() for x in raw.split("|")]
    if len(parts) < 2 or not parts[0] or not parts[1]:
        sys.exit(f"--find must be '<decision-id>|<short label>|<brand>|<material>|<#hex>', got {raw!r}")
    padded = (parts + ["", "", "", "", ""])[:6]
    colour, image = padded[4], padded[5]
    # Caption is everything past the 6th pipe, rejoined, so prose keeps its own punctuation.
    caption = "|".join(parts[6:]).strip() if len(parts) > 6 else ""
    if not colour:
        sys.exit(f"{parts[1]!r} has no colour. Run color_from_image.py --id <product id> --url <image> --match "
                 f"first and pass its hex as the 5th field — his palette is checked on EVERY find, not when it "
                 f"seems to matter. For something with no single colour pass '{NO_COLOUR}'.")
    if colour.lower() != NO_COLOUR and not HEX_RE.match(colour):
        sys.exit(f"{parts[1]!r}: {colour!r} is not a measured hex. Pass what color_from_image.py returned, "
                 f"or '{NO_COLOUR}' for something with no single colour — never a colour name.")
    if image and not IMAGE_RE.match(image):
        sys.exit(f"{parts[1]!r}: {image!r} is not an image URL. Pass the catalog row's `image` value "
                 f"(the same one color_from_image.py measured), or leave the 6th field empty.")
    return tuple(padded[:5]) + (image, caption)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--text", default="", help="the summary, in your own words (used when a find has no caption)")
    ap.add_argument("--find", action="append", default=[], metavar="ID|LABEL",
                    help="repeatable: the purchase_decisions id and a short label for the button")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    finds = [parse_find(raw) for raw in a.find]
    if not finds:
        sys.exit("at least one --find is required; with nothing to surface, stay silent instead")
    finds = finds[:MAX_FINDS]
    summary = tidy(a.text)
    per_find = any(f[5] for f in finds)
    records, keyboard = build(finds)

    if not per_find:
        # Legacy path: one message, every find's buttons on it. What the Mon/Thu cron sends today.
        if not summary:
            sys.exit("--text is required when no find carries an image URL")
        if a.dry_run or os.environ.get("WARDROBE_NO_SEND") == "1":
            print(summary + "\n" + json.dumps(keyboard, ensure_ascii=False, indent=1)); return 0
        from send_telegram import send_chunk
        receipt = send_chunk(summary, reply_markup=keyboard)
        msg_id = (receipt or {}).get("result", {}).get("message_id")
        store = load_json(STORE, {})
        for token, rec in records.items():
            rec["source_tg_msg_id"] = msg_id
            store[token] = rec
        save_json(STORE, store)
        print(json.dumps({"sent": msg_id, "finds": list(records)}))
        return 0

    # Photo path: one message per find, each with its own caption and its own buttons.
    messages = []
    for find in finds:
        label, image, caption = find[1], find[5], tidy(find[6])
        if not caption:
            if len(finds) == 1 and summary:
                caption = summary
            else:
                sys.exit(f"{label!r} has an image but no caption. With more than one find each needs its "
                         f"own caption (the 7th field) — a shared summary cannot sit under two photos.")
        messages.append((token_for(find[0], label), label, image, caption))

    if a.dry_run or os.environ.get("WARDROBE_NO_SEND") == "1":
        for token, label, image, caption in messages:
            print(json.dumps({"photo": image or None, "caption": caption,
                              "reply_markup": {"inline_keyboard": [row_for(token, label)]}},
                             ensure_ascii=False, indent=1))
        return 0

    from send_telegram import send_chunk, send_photo
    store = load_json(STORE, {})
    sent = []
    for token, label, image, caption in messages:
        markup = {"inline_keyboard": [row_for(token, label)]}
        if image:
            receipt = send_photo(image, caption, reply_markup=markup)
        else:
            receipt = send_chunk(caption, reply_markup=markup)
        msg_id = (receipt or {}).get("result", {}).get("message_id")
        rec = records[token]
        rec["source_tg_msg_id"] = msg_id
        store[token] = rec
        sent.append({"find": token, "sent": msg_id})
        # Persist after each send: a failure on the second photo must not lose the first
        # message's token, or his tap on it would land on nothing.
        save_json(STORE, store)
    print(json.dumps({"sent": sent}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
