#!/usr/bin/env python3
"""A mirror photo with no words becomes a one-tap wear log. Deterministic sender; no model text.

The user 2026-09-28. The model's only job is to look at the photo and match what he is
wearing to real closet ids (never invent one). It then runs this, which asks:

  Looks like: {pieces}. Log as worn today?   [✅ Log it] [✏️ Not quite]

Nothing is logged until he taps — matching clothes from a photo is the step most
likely to be wrong. The answer edits this message in place (wear_photo_callback.py).

Usage:
  python3 propose_wear_from_photo.py --items id1,id2,id3
"""
import argparse
import hashlib
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
CLOSET = ROOT / "profile" / "closet.json"
CHAT = "1000000001"
sys.path.insert(0, str(ROOT.parent / "shared"))
sys.path.insert(0, str(SCRIPTS))
from data_paths import path as data_path  # noqa: E402
from proposal_caption import piece_label  # noqa: E402

PENDING = Path(os.environ["WARDROBE_STATE_DIR"]) / "pending_wear_photo.json" if os.environ.get("WARDROBE_STATE_DIR") else data_path("pending_wear_photo.json")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--items", required=True)
    args = ap.parse_args()
    ids = [x.strip() for x in args.items.split(",") if x.strip()]
    closet = {x.get("id"): x for x in json.loads(CLOSET.read_text())}
    unknown = [i for i in ids if i not in closet]
    if not ids or unknown:
        sys.exit("Only real closet ids can be logged" + (f"; unknown: {', '.join(unknown)}" if unknown else "") + ".")
    labels = [piece_label(closet[i]) for i in ids]
    qid = hashlib.sha256(f"{date.today().isoformat()}|{','.join(sorted(ids))}".encode()).hexdigest()[:12]
    text = f"Looks like: {', '.join(labels)}. Log as worn today?"
    markup = {"inline_keyboard": [[{"text": "✅ Log it", "callback_data": f"confirm:wearphoto-{qid}"},
                                   {"text": "✏️ Not quite", "callback_data": f"skip:wearphoto-{qid}"}]]}
    message_id = None
    if os.environ.get("WARDROBE_NO_SEND") != "1":
        from send_outfit_proposal import api
        receipt = api("sendMessage", {"chat_id": CHAT, "text": text, "reply_markup": markup})
        if not receipt.get("ok"):
            sys.exit("Telegram did not confirm delivery; nothing was asked.")
        message_id = receipt.get("result", {}).get("message_id")
    PENDING.parent.mkdir(parents=True, exist_ok=True)
    PENDING.write_text(json.dumps({"id": qid, "items": ids, "labels": labels, "date": date.today().isoformat(), "status": "asked",
                                   "source_tg_msg_id": message_id, "asked_at": datetime.now().astimezone().isoformat(timespec="seconds")}, indent=2, ensure_ascii=False))
    print(json.dumps({"asked": qid, "text": text}, ensure_ascii=False))


if __name__ == "__main__":
    main()
