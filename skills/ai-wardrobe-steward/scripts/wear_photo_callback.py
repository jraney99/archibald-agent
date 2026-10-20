#!/usr/bin/env python3
"""Answer to "Looks like: … Log as worn today?" — log it, or take his correction.

  log       the tap on ✅: log the guessed pieces as worn today
  notquite  the tap on ✏️: keep the question open for his typed correction
  correct   the agent, after his words: --items <the corrected ids> — logs and closes it
"""
import argparse
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
CLOSET = ROOT / "profile" / "closet.json"
sys.path.insert(0, str(ROOT.parent / "shared"))
sys.path.insert(0, str(SCRIPTS))
from data_paths import path as data_path  # noqa: E402
from proposal_caption import piece_label  # noqa: E402

PENDING = Path(os.environ["WARDROBE_STATE_DIR"]) / "pending_wear_photo.json" if os.environ.get("WARDROBE_STATE_DIR") else data_path("pending_wear_photo.json")
NOT_QUITE = "OK — tell me what I got wrong and I'll log it right. 🎩"


def occasion_for(items):
    tiers = yaml.safe_load((ROOT / "vocab.yaml").read_text())["occasion_formality"]
    votes = Counter(t for i in items for t in (i.get("occasion_tags") or []) if t in tiers)
    return votes.most_common(1)[0][0] if votes else "casual"


def log(ids, day):
    closet = {x.get("id"): x for x in json.loads(CLOSET.read_text())}
    unknown = [i for i in ids if i not in closet]
    if unknown:
        return None, f"Not logged — unknown closet id(s): {', '.join(unknown)}."
    items = [closet[i] for i in ids]
    done = subprocess.run([sys.executable, str(SCRIPTS / "outfit_history.py"), "log", "--occasion", occasion_for(items), "--items", ",".join(ids),
                           "--date", day, "--activity", "general", "--notes", "Logged from a photo"], capture_output=True, text=True)
    if done.returncode:
        return None, f"Not logged — {(done.stdout + done.stderr).strip()[:200]}"
    subprocess.run([sys.executable, str(SCRIPTS / "mark_after_wear.py"), "--items", ",".join(ids)], capture_output=True, text=True)
    return [piece_label(i) for i in items], None


def answer(qid, choice, corrected=None):
    pending = json.loads(PENDING.read_text()) if PENDING.exists() else {}
    if pending.get("id") != qid or pending.get("status") not in ("asked", "correcting"):
        return "No open photo question found. Nothing was changed. 🎩", None
    message_id = pending.get("source_tg_msg_id")
    if choice == "notquite":
        pending.update({"status": "correcting", "asked_at": datetime.now().astimezone().isoformat(timespec="seconds")})
        PENDING.write_text(json.dumps(pending, indent=2, ensure_ascii=False))
        return NOT_QUITE, message_id
    ids = corrected if choice == "correct" else pending["items"]
    labels, problem = log(ids, pending.get("date") or datetime.now().date().isoformat())
    if problem:
        return f"⚠️ {problem} 🎩", None
    PENDING.unlink(missing_ok=True)
    return f"✅ Logged as worn today: {', '.join(labels)}. 🎩", message_id


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--id", required=True)
    ap.add_argument("--answer", choices=["log", "notquite", "correct"], required=True)
    ap.add_argument("--items", default="", help="with --answer correct: the corrected closet ids")
    args = ap.parse_args()
    corrected = [x.strip() for x in args.items.split(",") if x.strip()]
    if args.answer == "correct" and not corrected:
        sys.exit("--answer correct needs --items.")
    text, message_id = answer(args.id, args.answer, corrected)
    if os.environ.get("WARDROBE_NO_SEND") == "1":
        print(text); return
    from pending_proposal import edit_or_fallback
    print(edit_or_fallback(message_id, text))


if __name__ == "__main__":
    main()
