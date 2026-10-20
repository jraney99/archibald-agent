#!/usr/bin/env python3
"""After a delivery: one message asking how it fits, with three buttons.

  python3 send_fit_prompt.py --purchase-id <id> [--dry-run]

Called by purchase_lifecycle.py when a purchase reaches `delivered`. Message
(approved wording pending the user 2026-10-12):

    📦 <item> from <retailer> arrived. How's the fit?
    [👍 Great]  [👌 OK]  [👎 Poor]

Each tap edits the message in place (fit_callback.py), records the verdict on the
purchase, and adds a dated evidence line to that brand's sizing in the body/fit
profile, so the next size suggestion for that brand rests on a real outcome.
Gated: FIT_PROMPTS_ENABLED in config/wardrobe_prompts.json must be true, otherwise
this only records the prompt as 'held' and prints what it would have sent.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent / "shared"))
sys.path.insert(0, str(ROOT / "scripts"))
from data_paths import path as data_path  # noqa: E402
from _store import load_json, save_json  # noqa: E402

PROMPTS = data_path("fit_prompts.json")
LIFECYCLE = ROOT / "profile" / "purchase_lifecycle.json"
GATE = ROOT / "config" / "wardrobe_prompts.json"


def away_from_home() -> bool:
    """True while the user is travelling (shared/current_location.py). Prompts that ask him to act
    on something at home or in a store wait until he is back."""
    try:
        from current_location import is_away_from_home
        return bool(is_away_from_home())
    except Exception:  # noqa: BLE001 - never block a prompt on a broken helper
        return False


def enabled() -> bool:
    return bool(load_json(GATE, {}).get("fit_prompts_enabled")) if GATE.exists() else False


def build(row: dict) -> tuple:
    pid = hashlib.sha256(row["id"].encode()).hexdigest()[:12]
    text = f"📦 {row['item']} from {row['retailer']} arrived. How's the fit?"
    kb = {"inline_keyboard": [[{"text": "👍 Great", "callback_data": f"confirm:fit-{pid}-great"},
                               {"text": "👌 OK", "callback_data": f"confirm:fit-{pid}-ok"},
                               {"text": "👎 Poor", "callback_data": f"confirm:fit-{pid}-poor"}]]}
    return pid, text, kb


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--purchase-id", required=True); ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    row = next((r for r in load_json(LIFECYCLE, []) if r["id"] == a.purchase_id), None)
    if not row:
        sys.exit(f"no purchase {a.purchase_id}")
    pid, text, kb = build(row)
    prompts = load_json(PROMPTS, {})
    if pid in prompts and prompts[pid].get("status") in ("asked", "great", "ok", "poor"):
        print("already asked"); return 0
    rec = {"purchase_id": row["id"], "item": row["item"], "retailer": row["retailer"], "text": text,
           "created_at": datetime.now(timezone.utc).isoformat(), "status": "held", "source_tg_msg_id": None}
    if a.dry_run or not enabled() or os.environ.get("WARDROBE_NO_SEND") == "1" or away_from_home():
        prompts[pid] = rec; save_json(PROMPTS, prompts)
        why = "DRY RUN" if a.dry_run else ("HELD (The user is away)" if away_from_home() else "HELD (fit prompts not enabled)")
        print(why + "\n" + text + "\n" + json.dumps(kb, ensure_ascii=False)); return 0
    from send_telegram import send_chunk
    receipt = send_chunk(text, reply_markup=kb)
    rec.update({"status": "asked", "source_tg_msg_id": (receipt or {}).get("result", {}).get("message_id")})
    prompts[pid] = rec; save_json(PROMPTS, prompts)
    print(f"asked {pid}"); return 0


if __name__ == "__main__":
    sys.exit(main())
