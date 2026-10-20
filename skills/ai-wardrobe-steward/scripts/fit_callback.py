#!/usr/bin/env python3
"""👍 / 👌 / 👎 under "arrived. How's the fit?" — edits that message in place.

  python3 fit_callback.py --id <12hex> --answer great|ok|poor

Records the verdict on the purchase (purchase_lifecycle fit_verdict) and appends a
dated evidence line to the brand's sizing entry in body_fit_profile.yaml through the
usual pending → confirm path. A 'poor' answer also drafts a purchase lesson for the
brand so the next recommendation there carries the warning.
"""
from __future__ import annotations

import argparse
import json
import os
import re
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

PROMPTS = data_path("fit_prompts.json")
PENDING_DETAIL = data_path("pending_fit_detail.json")   # one open typed question: "what's off?"
LIFECYCLE = ROOT / "profile" / "purchase_lifecycle.json"
PENDING_FIT = ROOT / "profile" / "pending_body_fit_update.json"
LABEL = {"great": "👍 Great fit", "ok": "👌 OK fit", "poor": "👎 Poor fit"}


def record(pid: str, answer: str, run=subprocess.run):
    prompts = load_json(PROMPTS, {})
    row = prompts.get(pid)
    if not row or row.get("status") not in ("asked", "held"):
        return "No open fit question found. Nothing was changed. 🎩", None
    rows = load_json(LIFECYCLE, [])
    purchase = next((r for r in rows if r["id"] == row["purchase_id"]), None)
    if purchase:
        purchase["fit_verdict"] = {"great": "great", "ok": "acceptable", "poor": "poor"}[answer]
        purchase.setdefault("notes", []).append({"date": date.today().isoformat(), "note": f"fit tap: {answer}"})
        save_json(LIFECYCLE, rows)
    category = guess_category(row["item"])
    size = size_in(row["item"])
    existing = load_json(PENDING_FIT, {}) if PENDING_FIT.exists() else {}
    note = f"{LABEL[answer]} — {row['item']}" + (f", size {size}" if size else "")
    if not existing and category:
        pending = {"review_summary": f"Fit verdict from delivery tap: {row['retailer']} {category} {answer}.",
                   "add": {"brand_sizing": [{"brand": row["retailer"], "category": category, "size": size or "unknown",
                                             "notes": note, "confidence": {"great": 90, "ok": 70, "poor": 85}[answer],
                                             "evidence": [{"date": date.today().isoformat(), "note": note}]}]}}
        save_json(PENDING_FIT, pending)
        run([sys.executable, str(SCRIPTS / "confirm_body_fit_update.py")], capture_output=True, text=True)
    row.update({"status": answer, "answered_at": datetime.now(timezone.utc).isoformat()})
    save_json(PROMPTS, prompts)
    if answer == "great":
        return f"{LABEL[answer]} noted for {row['item']}. 🎩", row.get("source_tg_msg_id")
    # 👌 / 👎: The user chose to DESCRIBE what is off (2026-10-12) rather than tap a reason, so the
    # message becomes a typed question; pending-context.mjs surfaces it and fit_detail.py records it.
    save_json(PENDING_DETAIL, {"id": pid, "purchase_id": row["purchase_id"], "item": row["item"], "retailer": row["retailer"],
                               "verdict": answer, "category": category, "size": size,
                               "asked_at": datetime.now().astimezone().isoformat(timespec="seconds")})
    text = (f"{LABEL[answer]} noted for {row['item']}. Tell me what's off — where it's tight or loose, "
            f"long or short — and I'll record it against {row['retailer']}'s sizing. 🎩")
    return text, row.get("source_tg_msg_id")


def guess_category(item: str) -> str:
    from store_catalog import CATEGORY_WORDS
    order = sorted(CATEGORY_WORDS, key=lambda c: 0 if c in ("t-shirt", "sneakers", "boots", "loafers", "dress_shoes", "athletic_shoes", "belt", "hat", "socks") else 1)
    return next((c for c in order if re.search("|".join(CATEGORY_WORDS[c]), item or "", re.I)), "")


def size_in(item: str) -> str:
    m = re.search(r"\b(XS|S|M|L|XL|XXL|\d{2}(?:x\d{2})?|\d{1,2}(?:\.5)? ?[A-E]?)\b(?!\S)", item or "")
    return m.group(1) if m else ""


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--id", required=True); ap.add_argument("--answer", choices=["great", "ok", "poor"], required=True)
    a = ap.parse_args()
    text, message_id = record(a.id, a.answer)
    if os.environ.get("WARDROBE_NO_SEND") == "1":
        print(text); return
    from pending_proposal import edit_or_fallback
    print(edit_or_fallback(message_id, text))


if __name__ == "__main__":
    main()
