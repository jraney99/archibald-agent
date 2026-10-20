#!/usr/bin/env python3
"""The ✅ Add / ⏭ Skip buttons under the Sunday Pinterest digest; edits that message in place.

  python3 pinterest_digest_callback.py --id <12hex> --answer add|skip

add: the digest's taste lines go into style_profile.yaml as `inspirations` entries
("Pinterest: ...", evidence = the pin ids) through the usual pending → confirm_style_update
path, so the change log records it like any other learning. skip: nothing is written;
the pins just stay marked as digested so they are never re-asked.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(ROOT.parent / "shared"))
sys.path.insert(0, str(SCRIPTS))
from data_paths import path as data_path  # noqa: E402
from _store import load_json, save_json  # noqa: E402

DIGEST_PATH = data_path("pinterest_digest.json")
PENDING_STYLE = ROOT / "profile" / "pending_style_update.json"


def apply_add(row: dict) -> str:
    taste = row.get("summary", {}).get("taste") or []
    if not taste:
        return "Nothing to add: that digest had no taste lines. 🎩"
    existing = load_json(PENDING_STYLE, {}) if PENDING_STYLE.exists() else {}
    if existing:
        return "⚠️ Not added: another style-profile update is waiting for confirmation. Ask Archibald to show it first. 🎩"
    today = date.today().isoformat()
    pending = {"review_summary": f"Pinterest digest {row['board']} ({len(row['pin_ids'])} pins): The user tapped Add.",
               "add": {"inspirations": [{"value": f"Pinterest: {t}", "confidence": 75, "importance": 60,
                                         "evidence": [{"date": today, "note": f"{len(row['pin_ids'])} pins on his {row['board']} board; he confirmed the digest"}]}
                                        for t in taste]}}
    save_json(PENDING_STYLE, pending)
    r = subprocess.run([sys.executable, str(SCRIPTS / "confirm_style_update.py")], capture_output=True, text=True)
    if r.returncode:
        return f"⚠️ Not added — {(r.stdout + r.stderr).strip()[:200]} 🎩"
    return f"✅ Added {len(taste)} line{'s' if len(taste) != 1 else ''} to your style profile. 🎩"


def apply_watch(row: dict, n: int):
    """🔍 Watch on one piece: a watchlist want with the catalog search attached. Returns (text, keyboard)."""
    from pinterest_digest import buttons
    items = row.get("watch") or []
    if n < 0 or n >= len(items):
        return "No such item on that digest. 🎩", None
    w = items[n]
    if w.get("status") == "watching":
        return "Already watching that one. 🎩", None
    notes = f"From Pinterest pin {w['pin_link']}" + (f"; product page {w['source_url']}" if w.get("source_url") else "")
    if w.get("colour"):
        notes += f"; colour {w['colour']}"
    cmd = [sys.executable, str(SCRIPTS / "shopping_watchlist.py"), "add", "--category", w["category"], "--notes", notes, "--source", "user-requested"]
    if w.get("store_name"):
        cmd += ["--brands", w["store_name"]]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        return f"⚠️ Not added — {(r.stdout + r.stderr).strip()[:200]} 🎩", None
    w["status"] = "watching"; w["watch_added_at"] = datetime.now(timezone.utc).isoformat()
    return f"👀 Watching: {w['label']}" + (f" at {w['store_name']}" if w.get("store_name") else "") + ". 🎩", buttons(row["digest_id"], items)


def answer(digest_id: str, choice: str, n: int = -1):
    digests = load_json(DIGEST_PATH, {})
    row = digests.get(digest_id)
    if not row:
        return "No open digest found. Nothing was changed. 🎩", None
    row.setdefault("digest_id", digest_id)
    if choice == "watch":
        text, keyboard = apply_watch(row, n)
        if keyboard:
            save_json(DIGEST_PATH, digests)
            row["_keyboard"] = keyboard
        return text, row.get("source_tg_msg_id")
    if row.get("status") != "asked":
        return "No open digest found. Nothing was changed. 🎩", None
    text = apply_add(row) if choice == "add" else "⏭ Skipped. Those pins stay saved as inspiration. 🎩"
    if not text.startswith("⚠️"):
        row.update({"status": choice, "answered_at": datetime.now(timezone.utc).isoformat()})
        save_json(DIGEST_PATH, digests)
    return text, row.get("source_tg_msg_id")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--id", required=True)
    ap.add_argument("--answer", choices=["add", "skip", "watch"], required=True)
    ap.add_argument("--n", type=int, default=-1, help="which 🔍 Watch button (0-based) for --answer watch")
    a = ap.parse_args()
    text, message_id = answer(a.id, a.answer, a.n)
    if os.environ.get("WARDROBE_NO_SEND") == "1":
        print(text); return
    if a.answer == "watch":
        # only that button changes; the message text and the other buttons stay
        digests = load_json(DIGEST_PATH, {}); row = digests.get(a.id) or {}
        if text.startswith("👀") and row.get("watch"):
            from pinterest_digest import buttons
            from send_telegram import edit_reply_markup
            if edit_reply_markup(message_id, buttons(a.id, row["watch"])):
                print("NO_REPLY"); return
        print(text); return
    from pending_proposal import edit_or_fallback
    print(edit_or_fallback(message_id, text))


if __name__ == "__main__":
    main()
