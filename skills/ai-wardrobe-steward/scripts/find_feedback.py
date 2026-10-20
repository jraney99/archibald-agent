#!/usr/bin/env python3
"""👍 / 👎 on something the scan found. Edits only that find's buttons.

  python3 find_feedback.py --id <12hex> --answer like|dislike

Records the verdict on the purchase decision (`taste`), and appends a style signal so the
shopping advisor can weigh it later. A 👍 is NOT a purchase: `user_action` stays untouched,
because what he likes and what he buys are different facts and the purchase watcher owns
the second one.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent / "shared"))
sys.path.insert(0, str(ROOT / "scripts"))
from data_paths import path as data_path  # noqa: E402
from _store import load_json, save_json  # noqa: E402

STORE = data_path("find_feedback.json")
DECISIONS = ROOT / "profile" / "purchase_decisions.json"
SIGNALS = ROOT / "profile" / "style_signals.json"


def colour_family(hex_value: str) -> str:
    """The palette family a measured colour belongs to, so taps aggregate by family not by shade."""
    if not hex_value:
        return ""
    import subprocess
    r = subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "color_match.py"),
                        "--hex", hex_value.lstrip("#")], capture_output=True, text=True)
    try:
        return (json.loads(r.stdout).get("nearest_palette") or {}).get("family", "")
    except Exception:  # noqa: BLE001 - a missing family must never lose the tap
        return ""


def record(token: str, answer: str):
    store = load_json(STORE, {})
    row = store.get(token)
    if not row or row.get("status") != "asked":
        return "No open find with that button. Nothing was changed. 🎩", None, None
    taste = "liked" if answer == "like" else "disliked"
    decisions = load_json(DECISIONS, [])
    hit = next((d for d in decisions if d.get("id") == row["decision_id"]), None)
    if hit:
        hit["taste"] = taste
        hit["taste_at"] = date.today().isoformat()
        save_json(DECISIONS, decisions)
    signals = load_json(SIGNALS, [])
    signals.append({"date": date.today().isoformat(), "source": "shopping-find-feedback",
                    "confidence": 60 if taste == "liked" else 55, "action": taste,
                    "decision_id": row["decision_id"], "label": row["label"],
                    "category": (hit or {}).get("category"),
                    # the attributes are what make a tap a lesson rather than a tally
                    "brand": row.get("brand") or "", "material": row.get("material") or "",
                    "colour_hex": row.get("colour_hex") or "", "colour_family": colour_family(row.get("colour_hex")),
                    "note": f"He tapped {'👍' if taste == 'liked' else '👎'} on a scan find"})
    save_json(SIGNALS, signals)
    row.update({"status": taste, "answered_at": datetime.now(timezone.utc).isoformat()})
    save_json(STORE, store)
    text = ("👍 Noted — more like that." if taste == "liked" else "👎 Noted — I'll steer away from that.") + " 🎩"
    return text, row.get("source_tg_msg_id"), store


def keyboard_after(store: dict) -> dict:
    """The same keyboard with answered finds showing their verdict; the others stay tappable."""
    from send_find import build
    open_rows = []
    for token, rec in store.items():
        if rec.get("status") == "asked":
            short = rec["label"][:18]
            open_rows.append([{"text": f"👍 {short}", "callback_data": f"confirm:find-{token}"},
                              {"text": "👎", "callback_data": f"skip:find-{token}"}])
        elif rec.get("status") in ("liked", "disliked"):
            mark = "👍" if rec["status"] == "liked" else "👎"
            open_rows.append([{"text": f"{mark} {rec['label'][:18]} ✓", "callback_data": f"confirm:find-{token}"}])
    return {"inline_keyboard": open_rows}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--id", required=True)
    ap.add_argument("--answer", choices=["like", "dislike"], required=True)
    a = ap.parse_args()
    text, message_id, store = record(a.id, a.answer)
    if os.environ.get("WARDROBE_NO_SEND") == "1" or not store:
        print(text); return
    same_msg = {t: r for t, r in store.items() if r.get("source_tg_msg_id") == message_id}
    from send_telegram import edit_reply_markup
    if message_id and edit_reply_markup(message_id, keyboard_after(same_msg)):
        print("NO_REPLY"); return
    print(text)


if __name__ == "__main__":
    main()
