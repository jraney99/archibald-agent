#!/usr/bin/env python3
"""Sunday Pinterest digest: what the user pinned this week and what it says about his taste.

  python3 pinterest_digest.py [--dry-run] [--min-pins 3]

Approved by the user 2026-10-12 (text and trigger). Fires only when at least --min-pins
described pins have not been through a digest yet; otherwise prints NOTHING_TO_SEND
and exits 0. One message, with ✅ Add / ⏭ Skip buttons that edit it in place
(callback: pinterest_digest_callback.py). The taste lines are a proposal: nothing
reaches style_profile.yaml until he taps ✅.

Steps: pinterest_describe.py for any undescribed pins, then one Haiku text call that
groups the descriptions into pieces / looks / taste lines (JSON), then the message is
built from a fixed template with plain text only (model output never carries markup).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent / "shared"))
sys.path.insert(0, str(ROOT / "scripts"))
from data_paths import path as data_path  # noqa: E402
from _store import load_json, save_json  # noqa: E402
import pinterest_describe as pd  # noqa: E402

PINS_PATH = data_path("inspiration_pins.json")
DIGEST_PATH = data_path("pinterest_digest.json")
BOARDS_PATH = data_path("pinterest_boards.json")
MODEL = "claude-haiku-4-5-20251001"
PROMPT = (
    "Below are short descriptions of pins the user saved to his fashion board, each marked piece (one item he "
    "likes) or look (a whole outfit he likes). Summarise them for a message to him. JSON only:\n"
    '{"pieces": ["<item, colour>", ...], "looks": [{"text": "<pieces joined with +>", "count": <pins>}, ...], '
    '"taste": ["<one short line about a recurring preference>", ...]}\n'
    "Rules: pieces = up to 5 distinct single items from piece pins. looks = up to 4 recurring combinations "
    "from look pins, with how many pins show each. taste = 2 to 4 lines, each under 10 words, only patterns "
    "that at least two pins support, each naming a garment, colour, fit or combination; no mood words like "
    "polished, sophisticated or effortless. Plain words, no brand guesses, no markdown.\n\nPINS:\n"
)


def away_from_home() -> bool:
    """True while the user is travelling (shared/current_location.py). Prompts that ask him to act
    on something at home or in a store wait until he is back."""
    try:
        from current_location import is_away_from_home
        return bool(is_away_from_home())
    except Exception:  # noqa: BLE001 - never block a prompt on a broken helper
        return False


def pending_pins(pins: list) -> list:
    return [p for p in pins if p.get("description") and not p.get("digest_id")]


def summarise(described: list, client) -> dict:
    lines = []
    for p in described:
        d = p["description"]
        pieces = ", ".join(f"{x.get('item', '?')} ({x.get('colour', '?')})" for x in d.get("pieces", []))
        lines.append(f"- [{d['kind']}] {d.get('summary', '')} | {pieces}")
    resp = client.messages.create(model=MODEL, max_tokens=600,
                                  messages=[{"role": "user", "content": PROMPT + "\n".join(lines)}])
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    data = json.loads(text[text.find("{"): text.rfind("}") + 1])
    try:
        from usage_logger import log_usage
        log_usage("pinterest_digest.py", MODEL, resp.usage, response_id=getattr(resp, "id", None))
    except Exception:  # noqa: BLE001
        pass
    # the Pieces section is for pins that ARE a single item; with only outfit pins it would just repeat the looks
    has_piece_pins = any(p["description"].get("kind") == "piece" for p in described)
    return {"pieces": [str(x)[:60] for x in data.get("pieces", [])][:5] if has_piece_pins else [],
            "looks": [{"text": str(l.get("text", ""))[:60], "count": int(l.get("count", 1))} for l in data.get("looks", [])][:4],
            "taste": [str(x)[:60] for x in data.get("taste", [])][:4]}


def watch_items(batch: list, stores: list, max_items: int = 3) -> list:
    """PIECE pins that map to a vocab category: what a 🔍 Watch button would add."""
    from urllib.parse import urlparse
    from store_catalog import CATEGORY_WORDS
    import re
    by_domain = {}
    for st in stores:
        if st.get("domain"):
            by_domain[st["domain"].lower().replace("www.", "")] = st
    out = []
    for p in batch:
        d = p.get("description") or {}
        if d.get("kind") != "piece" or not d.get("pieces"):
            continue
        item = d["pieces"][0]
        label = f"{item.get('colour', '')} {item.get('item', '')}".strip()
        # footwear and accessories first: "oxford brogue shoes" must not read as a shirt
        order = sorted(CATEGORY_WORDS, key=lambda c: 0 if c in ("t-shirt", "sneakers", "boots", "loafers", "dress_shoes", "athletic_shoes",
                                                                 "belt", "hat", "socks", "wallet", "bag", "scarf", "tie", "sunglasses", "watch") else 1)
        cat = next((c for c in order if re.search("|".join(CATEGORY_WORDS[c]), item.get("item", ""), re.I)), None)
        if not cat:
            continue
        host = urlparse(p.get("source_url") or "").netloc.lower().replace("www.", "")
        store = by_domain.get(host)
        out.append({"pin_id": p["pin_id"], "label": label[:40], "category": cat, "colour": item.get("colour", ""),
                    "store": store["slug"] if store else None, "store_name": store["name"] if store else None,
                    "source_url": p.get("source_url") or "", "pin_link": p["link"], "status": "offered"})
        if len(out) >= max_items:
            break
    return out


def closet_line(board: str = None) -> str:
    """One line on how much of what he pins he can already wear (approved 2026-10-12)."""
    try:
        from closet_looks import analyse
        from pathlib import Path as _P
        res = analyse(load_json(PINS_PATH, []), load_json(_P(__file__).resolve().parent.parent / "profile" / "closet.json", []), board)
    except Exception:  # noqa: BLE001 - a digest must never fail on its closing line
        return ""
    can, away = len(res["can_make"]), len(res["one_piece_away"])
    if not can and not away:
        return ""
    bits = []
    if can:
        bits.append(f"You can already make {can} of these looks")
    if away:
        bits.append(("and " if can else "") + f"{away} more are one piece away")
    return "👔 " + ", ".join(bits) + "."


def build_message(board_name: str, n_pins: int, s: dict, watch: list = (), closet: str = "") -> str:
    out = [f"📌 Pinterest · {board_name}: {n_pins} new pins", ""]
    if watch:
        out += ["👕 Pieces"] + [f"• {w['label']}" + (f" ({w['store_name']})" if w.get("store_name") else "") for w in watch] + [""]
    elif s["pieces"]:
        out += ["👕 Pieces"] + [f"• {x}" for x in s["pieces"]] + [""]
    if s["looks"]:
        out += ["🧥 Looks"] + [f"• {l['text']}" + (f" ×{l['count']}" if l["count"] > 1 else "") for l in s["looks"]] + [""]
    if s["taste"]:
        out += ["🎯 Your taste"] + [f"• {x}" for x in s["taste"]] + [""]
    if closet:
        out += [closet, ""]
    out.append("Add to your style profile?")
    return "\n".join(out)


def buttons(digest_id: str, watch: list = ()) -> dict:
    rows = [[{"text": "✅ Add", "callback_data": f"confirm:pindigest-{digest_id}"},
             {"text": "⏭ Skip", "callback_data": f"skip:pindigest-{digest_id}"}]]
    row = []
    for n, w in enumerate(watch):
        text = "👀 Watching" if w.get("status") == "watching" else f"🔍 Watch: {w['label'].split(' ', 1)[-1][:18]}"
        row.append({"text": text, "callback_data": f"confirm:pinwatch-{digest_id}-{n}"})
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    return {"inline_keyboard": rows}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="describe + summarise, print the message, send nothing, mark nothing")
    ap.add_argument("--min-pins", type=int, default=3)
    a = ap.parse_args(argv)

    if away_from_home() and not a.dry_run:
        print("HELD: The user is away from home; digest waits until he is back"); return 0
    pins = load_json(PINS_PATH, [])
    client = pd.make_client()
    rep = pd.describe_pending(pins, client)
    save_json(PINS_PATH, pins)
    batch = pending_pins(pins)
    if len(batch) < a.min_pins:
        print(f"NOTHING_TO_SEND ({len(batch)} pins waiting, min {a.min_pins}; described {rep['described']})")
        return 0
    boards = {b["id"]: b for b in load_json(BOARDS_PATH, {}).get("boards", [])}
    from _stores import load_stores
    boards_list = lambda _b: load_stores()  # noqa: E731
    board_name = boards.get(batch[0]["board"], {}).get("name", "Fashion Ideas")
    summary = summarise(batch, client)
    watch = watch_items(batch, list(boards_list(boards)))
    digest_id = hashlib.sha256(("|".join(p["pin_id"] for p in batch) + datetime.now().isoformat()).encode()).hexdigest()[:12]
    text = build_message(board_name, len(batch), summary, watch, closet_line(batch[0]["board"]))
    if a.dry_run:
        print(text); print(json.dumps({"digest_id": digest_id, "pins": len(batch), "summary": summary, "watch": watch,
                                       "buttons": buttons(digest_id, watch)}, indent=2))
        return 0
    digests = load_json(DIGEST_PATH, {})
    digests[digest_id] = {"status": "asked", "created_at": datetime.now(timezone.utc).isoformat(), "board": batch[0]["board"],
                          "pin_ids": [p["pin_id"] for p in batch], "summary": summary, "text": text, "watch": watch, "source_tg_msg_id": None}
    save_json(DIGEST_PATH, digests)
    from send_telegram import send_chunk
    receipt = send_chunk(text, reply_markup=buttons(digest_id, watch))
    msg_id = (receipt or {}).get("result", {}).get("message_id")
    digests[digest_id]["source_tg_msg_id"] = msg_id
    save_json(DIGEST_PATH, digests)
    for p in batch:
        p["digest_id"] = digest_id
    save_json(PINS_PATH, pins)
    print(f"sent digest {digest_id} ({len(batch)} pins, message {msg_id})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
