#!/usr/bin/env python3
"""Send a Telegram message in chunks and return a machine-readable receipt."""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from runtime_secrets import require_secret

TG_TOKEN = require_secret("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID = "1000000001"
CHUNK_SIZE = 4000


def _refuse_if_disabled(what: str) -> None:
    """Test suites set TELEGRAM_SEND_DISABLED=1. A script that reaches here anyway has a
    missing guard; failing loudly is the point (2026-09-28: a wardrobe test sent the user a fake trip)."""
    if os.environ.get("TELEGRAM_SEND_DISABLED") == "1":
        raise RuntimeError(f"Telegram sending is disabled in this environment ({what} blocked)")


def _calling_skill() -> str:
    """Which skill is sending, taken from the running script's path.

    chat_cleanup groups and reports by this, so "unknown" still tracks correctly;
    it just reads less well in the archive."""
    try:
        parts = Path(sys.argv[0]).resolve().parts
        if "Skills" in parts:
            return parts[parts.index("Skills") + 1]
    except Exception:  # noqa: BLE001 - a path we cannot read is not worth a failed send
        pass
    return "unknown"


def _auto_track_prompt(receipt, text, reply_markup) -> None:
    """A message carrying buttons is a PROMPT, so register it with chat_cleanup.

    WHY (2026-10-12): 42 scripts send prompts and only 13 registered, so an
    unanswered prompt was invisible to the sweep. Past 48h Telegram refuses a
    bot's own delete, so those became permanent clutter — 13 messages in the
    October archive are already unremovable for exactly this reason.

    kind="answerable" is the whole reason this is safe to switch on. Rule 1 in
    chat_cleanup never touches an answerable prompt until it is ANSWERED, so
    nothing the user can see changes today; the sweep's `too_old_to_delete` counter
    and the matching archive rows simply start recording which prompts go
    unanswered, which is the evidence the per-kind TTL decisions need.
    Registering these as "notice" would delete unanswered asks after 20h —
    the opposite of the intent, and not approved.

    Never raises: chat hygiene must not break a send. Under a test run
    chat_cleanup's own guard refuses the real state file, and track() swallows
    that, so this no-ops rather than touching live tracking from a test.
    """
    if not reply_markup:
        return
    try:
        message_id = (receipt or {}).get("result", {}).get("message_id")
        if not message_id:
            return
        import chat_cleanup
        chat_cleanup.track(message_id, kind="answerable",
                           skill=_calling_skill(), text=text)
    except Exception:  # noqa: BLE001 - see the docstring: a send must still succeed
        pass


def send_chunk(text: str, reply_markup=None, parse_mode=None,
               disable_preview: bool = False) -> dict:
    # NB: parse_mode is left unannotated on purpose. This module has no
    # `from __future__ import annotations` and the gateway runs Python 3.9, where a
    # `str | None` hint is evaluated at import and raises TypeError — which would
    # break every one of the 60+ scripts that import this module, not just the caller
    # that wanted the new argument. Caught by test_send_telegram_tracking.py.
    """Send one message. parse_mode is opt-in and defaults off.

    2026-10-09: added so a sender can embed a link in its own words
    (<a href="...">The Chicks</a>) instead of stacking a bare URL under every line.
    Both new arguments default to the previous behaviour, so the 60-odd callers that
    pass plain text are unaffected — and plain text stays the right choice for anything
    containing MODEL output, which can carry stray markup that would break parsing
    (see the 2026-09-29 Stoic-reflection heading). Only hand-built templates whose
    dynamic parts are escaped should pass parse_mode.
    """
    _refuse_if_disabled("sendMessage")
    body = {"chat_id": TG_CHAT_ID, "text": text}
    if parse_mode:
        body["parse_mode"] = parse_mode
    if disable_preview:
        # A link-per-line message otherwise renders a large preview card for the
        # first URL, which is exactly the clutter embedding the links removes.
        body["link_preview_options"] = {"is_disabled": True}
    if reply_markup:
        body["reply_markup"] = reply_markup
    payload = json.dumps(body).encode()
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    last_error = None
    for attempt in range(3):
        if attempt:
            time.sleep(8)
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                receipt = json.loads(response.read())
                if receipt.get("ok") is not True:
                    raise RuntimeError("Telegram did not confirm delivery")
                _auto_track_prompt(receipt, text, reply_markup)
                return receipt
        except urllib.error.HTTPError as exc:
            if exc.code < 500:
                raise
            last_error = exc
        except urllib.error.URLError as exc:
            last_error = exc
    raise last_error


CAPTION_LIMIT = 1024


def send_photo(photo_url: str, caption: str, reply_markup=None) -> dict:
    """Send one remote image with a caption and optional buttons.

    WHY (2026-10-16): the shopping scan could only send the user words. He asked to see
    the garment ("it doesnt show me the image of it") — reasonable for clothing, and
    the outfit board had been doing it since September. Telegram fetches `photo` when
    it is a URL, so a catalog image needs no download; the bytes path stays in
    send_outfit_proposal.py, which sends locally rendered boards.

    Captions are capped by Telegram at CAPTION_LIMIT, hard — an over-long caption is
    a 400, not a truncation, so it is trimmed here rather than failing the send.
    Feedback buttons survive on a photo message: find_feedback.py edits it with
    editMessageReplyMarkup, which does not care whether the message is text or media.
    """
    _refuse_if_disabled("sendPhoto")
    caption = caption or ""
    if len(caption) > CAPTION_LIMIT:
        caption = caption[:CAPTION_LIMIT - 1].rstrip() + "\u2026"
    body = {"chat_id": TG_CHAT_ID, "photo": photo_url, "caption": caption}
    if reply_markup:
        body["reply_markup"] = reply_markup
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    last_error = None
    for attempt in range(3):
        if attempt:
            time.sleep(8)
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                receipt = json.loads(response.read())
                if receipt.get("ok") is not True:
                    raise RuntimeError("Telegram did not confirm delivery")
                _auto_track_prompt(receipt, caption, reply_markup)
                return receipt
        except urllib.error.HTTPError as exc:
            if exc.code < 500:
                raise
            last_error = exc
        except urllib.error.URLError as exc:
            last_error = exc
    raise last_error


def edit_message_text(message_id, text: str, drop_buttons: bool = True, keep: bool = False,
                      reply_markup=None) -> bool:
    if not message_id:
        return False
    _refuse_if_disabled("editMessageText")
    body = {"chat_id": TG_CHAT_ID, "message_id": int(message_id), "text": text}
    if reply_markup is not None:
        body["reply_markup"] = reply_markup
    elif drop_buttons:
        body["reply_markup"] = {"inline_keyboard": []}
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{TG_TOKEN}/editMessageText",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            ok = bool(json.loads(response.read()).get("ok"))
    except Exception:
        return False
    if ok and drop_buttons:
        # Buttons gone = this prompt is answered. Archived + removed from the
        # chat at the nightly sweep (chat_cleanup.py); never raises.
        try:
            from chat_cleanup import mark_answered
            # The user's keep rule (2026-09-20): a record of something SENT in his
            # name stays in the chat (still archived). Callers pass keep=True;
            # the wording check is a net under any caller that forgets.
            sent_in_his_name = keep or text.lstrip().startswith("✅ Sent")
            mark_answered(message_id, text, skill="proposal", keep=sent_in_his_name)
        except Exception:
            pass
    return ok


def edit_reply_markup(message_id, reply_markup) -> bool:
    """Replace just the inline keyboard of a sent message (editMessageReplyMarkup).

    2026-10-12: for prompts with several independent buttons (Pinterest digest's
    per-item 🔍 Watch), one tap must change its own button and leave the rest, which
    edit_message_text cannot do without rewriting the whole message.
    """
    if not message_id:
        return False
    _refuse_if_disabled("editMessageReplyMarkup")
    body = {"chat_id": TG_CHAT_ID, "message_id": int(message_id), "reply_markup": reply_markup}
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TG_TOKEN}/editMessageReplyMarkup",
        data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return bool(json.loads(resp.read().decode()).get("ok"))
    except Exception:  # noqa: BLE001 - a failed keyboard edit is reported, never raised
        return False


def chunk_text(text: str, size: int) -> list[str]:
    if len(text) <= size:
        return [text]
    chunks = []
    while len(text) > size:
        cut = text.rfind("\n", 0, size)
        if cut <= 0:
            cut = size
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip("\n")
    if text:
        chunks.append(text)
    return chunks


def main():
    parser = argparse.ArgumentParser(description="Send chunked Telegram message")
    parser.add_argument("--text", help="Message text to send")
    parser.add_argument("--file", help="Path to file containing message text")
    # 2026-09-20 chat cleanup (chat_cleanup.py). Without either flag the message is
    # never auto-removed -- right for anything that needs action or records
    # something done in the user's name (alerts, payment reminders, "Sent" lines).
    parser.add_argument("--notice", metavar="SKILL",
                        help="One-off FYI with nothing to answer: archived + removed after about a day")
    parser.add_argument("--stream", metavar="NAME",
                        help="Recurring report, e.g. finance:weekly-pulse: this send replaces the previous one")
    args = parser.parse_args()

    if args.file:
        text = Path(args.file).read_text()
    elif args.text:
        text = args.text
    elif not sys.stdin.isatty():
        text = sys.stdin.read()
    else:
        print(json.dumps({"error": "no input provided"}))
        raise SystemExit(1)

    text = text.strip()
    if not text:
        print(json.dumps({"sent": False, "reason": "empty message"}))
        return

    receipts = [send_chunk(chunk) for chunk in chunk_text(text, CHUNK_SIZE)]
    message_ids = [r.get("result", {}).get("message_id") for r in receipts]
    if args.notice or args.stream:
        try:
            import chat_cleanup
            ids = [m for m in message_ids if m]
            if args.stream:
                chat_cleanup.replace_stream(args.stream, ids, text, skill=args.stream.split(":")[0])
            else:
                for m in ids:
                    chat_cleanup.track(m, kind="notice", skill=args.notice, text=text)
        except Exception:
            pass  # chat hygiene never blocks a send
    print(json.dumps({"sent": True, "chunks": len(receipts),
                      "total_chars": len(text), "message_ids": message_ids}))


if __name__ == "__main__":
    main()
