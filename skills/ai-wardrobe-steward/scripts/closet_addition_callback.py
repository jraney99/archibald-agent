#!/usr/bin/env python3
"""The three buttons under "Delivered: … Add it to your closet?" — edits that message in place."""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "shared"))
from data_paths import path as data_path  # noqa: E402

STATE = Path(os.environ["WARDROBE_STATE_DIR"]) / "closet_purchase_prompts.json" if os.environ.get("WARDROBE_STATE_DIR") else data_path("closet_purchase_prompts.json")


def run(script, *args):
    return subprocess.run([sys.executable, str(SCRIPTS / script), *args], capture_output=True, text=True)


def answer(prompt_id, choice):
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    row = state.get(prompt_id)
    if not row or row.get("status") != "asked":
        return "No open question found. Nothing was changed. 🎩", None
    label = row.get("label") or "it"
    if choice == "add":
        done = run("confirm_closet_item.py", row["item_id"])
        if done.returncode:
            return f"⚠️ Not added — {(done.stdout + done.stderr).strip()[:200]} 🎩", None
        text = f"✅ Added {label} to your closet." + ("" if row.get("has_photo") else " No photo yet — send one when you can and I'll attach it.") + " 🎩"
    else:
        run("cancel_closet_item.py", row["item_id"])
        if choice == "return":
            if row.get("purchase_id"):
                run("purchase_lifecycle.py", "update", "--id", row["purchase_id"], "--status", "returning")
            text = "↩️ Noted — returning it. 🎩"
        else:
            text = "OK — not clothing, ignored. 🎩"
    row.update({"status": choice, "answered_at": datetime.now().astimezone().isoformat(timespec="seconds")})
    STATE.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    return text, row.get("source_tg_msg_id")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--id", required=True)
    ap.add_argument("--answer", choices=["add", "return", "notclothing"], required=True)
    args = ap.parse_args()
    text, message_id = answer(args.id, args.answer)
    if os.environ.get("WARDROBE_NO_SEND") == "1":
        print(text); return
    from pending_proposal import edit_or_fallback
    print(edit_or_fallback(message_id, text))


if __name__ == "__main__":
    main()
