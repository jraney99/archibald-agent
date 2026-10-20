#!/usr/bin/env python3
"""Send a saved outfit board to Telegram with deterministic feedback buttons."""
import argparse
import json
import os
import urllib.request
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))
from runtime_secrets import require_secret  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PROPOSALS = ROOT / "profile" / "outfit_proposals.json"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from proposal_caption import caption as build_caption, never_label  # noqa: E402
TOKEN = require_secret("TELEGRAM_BOT_TOKEN")
CHAT = "1000000001"


def api(method, payload, photo=None):
    # Tests set WARDROBE_NO_SEND. A script that forgets its own guard must fail here,
    # loudly, not message the user (2026-09-28: the packing test sent him a fake trip).
    if os.environ.get("WARDROBE_NO_SEND") == "1" or os.environ.get("TELEGRAM_SEND_DISABLED") == "1":
        raise RuntimeError(f"Telegram sending is disabled in this environment ({method} blocked)")
    url = f"https://api.telegram.org/bot{TOKEN}/{method}"
    if photo:
        boundary = "----wardrobe-boundary"
        parts = []
        for key, value in payload.items():
            parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n".encode())
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"outfit.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n".encode() + Path(photo).read_bytes() + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        req = urllib.request.Request(url, data=b"".join(parts), headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    else:
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.loads(response.read())


def board_markup(item, menu=None):
    """Buttons under a board.

    2026-10-13 (The user: "I dont really like the way it has looked"): the flat ten-button
    keyboard — two wear answers, three swaps, four corrections and the switch-off — is now
    four, with the detail one tap away. Nothing was removed; `menu` names the open submenu.
    Approved by the user 2026-09-28 in its flat form, reshaped with his OK on 2026-10-13.
    """
    pid = item["id"]
    never = never_label(item.get("meta"))
    if menu == "swap":
        return {"inline_keyboard": [
            [{"text": "Top", "callback_data": f"outfit:swap-{pid}-top"},
             {"text": "Bottoms", "callback_data": f"outfit:swap-{pid}-bottoms"},
             {"text": "Shoes", "callback_data": f"outfit:swap-{pid}-shoes"}],
            [{"text": "◀ Back", "callback_data": f"outfit:menu-{pid}-main"}]]}
    if menu == "wrong":
        rows = [[{"text": "Too formal", "callback_data": f"outfit:formal-{pid}"},
                 {"text": "Too casual", "callback_data": f"outfit:casual-{pid}"}],
                [{"text": "Too warm", "callback_data": f"outfit:warm-{pid}"},
                 {"text": "Too cold", "callback_data": f"outfit:cold-{pid}"}],
                [{"text": "Don't own / unavailable", "callback_data": f"outfit:unavail-{pid}"}]]
        if never:
            rows.append([{"text": never, "callback_data": f"outfit:never-{pid}"}])
        rows.append([{"text": "◀ Back", "callback_data": f"outfit:menu-{pid}-main"}])
        return {"inline_keyboard": rows}
    return {"inline_keyboard": [
        [{"text": "✅ Wore it", "callback_data": f"confirm:outfitworn-{pid}"},
         {"text": "👕 Wore something else", "callback_data": f"skip:outfitworn-{pid}"}],
        [{"text": "🔁 Swap", "callback_data": f"outfit:menu-{pid}-swap"},
         {"text": "✏️ Not right", "callback_data": f"outfit:menu-{pid}-wrong"}]]}


def edit_board(item, keep_buttons=True, new_photo=False, menu=None):
    """Rewrite the board message in place: caption, buttons, and the photo after a swap.
    Returns True when Telegram confirmed the edit. Never raises — callers fall back to a reply."""
    message_id = item.get("source_tg_msg_id")
    if not message_id:
        return False
    markup = board_markup(item, menu) if keep_buttons else {"inline_keyboard": []}
    try:
        if new_photo:
            media = {"type": "photo", "media": "attach://photo", "caption": build_caption(item)[:1000]}
            receipt = api("editMessageMedia", {"chat_id": CHAT, "message_id": str(message_id), "media": json.dumps(media),
                                               "reply_markup": json.dumps(markup)}, item["board_path"])
        else:
            receipt = api("editMessageCaption", {"chat_id": CHAT, "message_id": int(message_id), "caption": build_caption(item)[:1000],
                                                 "reply_markup": markup})
        return bool(receipt.get("ok"))
    except Exception:  # noqa: BLE001 - a failed edit must not lose the tap; the caller replies instead
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", required=True)
    ap.add_argument("--post-event", action="store_true", help="(retired 2026-09-28: the board itself now carries the wear buttons)")
    args = ap.parse_args()
    proposals = json.loads(PROPOSALS.read_text())
    item = next((p for p in proposals if p.get("id") == args.id), None)
    if not item:
        raise SystemExit(f"No outfit proposal with id '{args.id}'.")
    if args.post_event:
        text = f"Did you wear the proposed outfit for {item['event_title']}?"
        markup = {"inline_keyboard": [[
            {"text": "✅ Wore it", "callback_data": f"confirm:outfitworn-{args.id}"},
            {"text": "❌ Different outfit", "callback_data": f"skip:outfitworn-{args.id}"},
        ]]}
        receipt = api("sendMessage", {"chat_id": CHAT, "text": text, "reply_markup": markup})
    else:
        receipt = api("sendPhoto", {"chat_id": CHAT, "caption": build_caption(item)[:1000], "reply_markup": json.dumps(board_markup(item))}, item["board_path"])
    if not receipt.get("ok"):
        raise SystemExit("Telegram did not confirm delivery")
    message_id = receipt.get("result", {}).get("message_id")
    if args.post_event:
        item["status"] = "wear-confirmation-sent"
        item["wear_prompted_at"] = __import__("datetime").datetime.now().astimezone().isoformat()
    else:
        item["source_tg_msg_id"] = message_id      # so every later tap edits this message instead of posting a second one
    PROPOSALS.write_text(json.dumps(proposals, indent=2))
    print(json.dumps({"sent": True, "message_id": message_id}))


if __name__ == "__main__": main()
