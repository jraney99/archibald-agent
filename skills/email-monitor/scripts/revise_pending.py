#!/usr/bin/env python3
"""
Revise a pending email reply — never send it.

Every "Action Needed" card says "or reply with your own text", but nothing
implemented that: the only code that rewrote a pending draft lived in
daemon.handle_telegram_msg, reachable only from telegram_loop(), which main()
never starts (the gateway owns Telegram long-polling). So a typed edit reached
Archibald as ordinary chat and died there — 2026-10-09, the user asked for a line
to be dropped from a pending reply draft and nothing changed.

This is the live path. Archibald calls it when the user types a correction under an
email draft:

    python3 revise_pending.py --id <gmail_msg_id> --text "<the user's replacement body>"
    python3 revise_pending.py --id <gmail_msg_id> --redraft
    ... --dry-run     # print what would be sent, touch nothing

--text replaces the body with his words. --redraft re-runs the drafting model
with the CURRENT prompts, which is what to use when his correction is a rule
("stop opening with filler") rather than replacement prose.

Either way the Gmail signature is re-joined through daemon._with_signature, so
the result carries exactly one sign-off.

A revision invalidates the old approval on purpose: approval.claim() digests the
draft, so the stale ✅ can no longer send the text he rejected. The old button
message is edited in place to say so, and a fresh proposal with a fresh 15-minute
approval is sent. This script NEVER sends email — only send_pending.py does, and
only from a real button tap.

Exit codes: 0 revised · 1 nothing pending for that id · 2 bad usage/failure
"""
import argparse
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _daemon():
    """daemon.py isn't packaged; import it by path the way the tests do.
    Importing it must stay side-effect-free (no network/file I/O at module level)."""
    spec = importlib.util.spec_from_file_location("daemon", HERE / "daemon.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


REPLACED_NOTE = "✏️ Replaced by a newer draft — nothing was sent."


def _sender_name(d, entry: dict) -> str:
    """"Alex - Example Venue", not "alex@venue.example".
    The pending entry only stores the address, so read the From header back; a
    fetch failure is cosmetic, so fall back to the address rather than raise."""
    try:
        full = d._gmail().users().messages().get(
            userId="me", id=entry["gmail_msg_id"], format="metadata",
            metadataHeaders=["From"]).execute()
        hdrs = {h["name"].lower(): h["value"]
                for h in full.get("payload", {}).get("headers", [])}
        return d._display_name(hdrs.get("from", "")) or entry["to_email"]
    except Exception:
        return entry["to_email"]


def _redraft(d, entry: dict) -> str:
    """Re-run the drafting model against the original email with current prompts."""
    svc = d._gmail()
    full = svc.users().messages().get(
        userId="me", id=entry["gmail_msg_id"], format="full").execute()
    hdrs = {h["name"].lower(): h["value"] for h in full.get("payload", {}).get("headers", [])}
    sender = hdrs.get("from", entry.get("to_email", ""))
    subject = hdrs.get("subject", entry.get("subject", ""))
    body = d._get_body(full)
    result = d._classify(sender, subject, body[:2000])
    mtg, prop_t = result.get("meeting_request"), result.get("proposed_time")
    draft = d._write_draft(sender, subject, body[:1000], mtg)
    if not draft:
        raise RuntimeError("the drafting model returned nothing; draft unchanged")
    if mtg in ("open", "specific") and "[AVAILABILITY]" in draft:
        draft = d._resolve_avail(draft, mtg, prop_t)
    return draft


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", required=True, help="Gmail message id from the card's ref: line")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--text", help="The user's replacement body, in his words")
    group.add_argument("--redraft", action="store_true",
                       help="re-run the drafting model with the current prompts")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    d = _daemon()
    state = d._load()
    entry = state.get("gmail_pending", {}).get(args.id)
    if not entry:
        print(f"NO_PENDING: {args.id}")
        return 1

    body = _redraft(d, entry) if args.redraft else args.text
    draft = d._with_signature(body, d._get_signature())
    if draft.strip() == entry["draft"].strip():
        print("UNCHANGED: the revised draft is identical; nothing sent, nothing stored.")
        return 0

    to_email = entry["to_email"]
    name = _sender_name(d, entry)
    text = (f"✏️ <b>Draft revised</b> — reply to {d._h(name)}"
            f"\nRe: {d._h(entry['subject'][:80])}"
            f"\n\n<blockquote>{d._h(draft)}</blockquote>")

    if args.dry_run:
        print("--- would send to Telegram ---")
        print(text)
        print("--- buttons: ✅ Send · ❌ Cancel ---")
        return 0

    revised = dict(entry)
    revised["draft"] = draft
    # issue() digests this payload, so the stale ✅ stops working the moment the
    # new draft is stored. Retire its message first so he never taps a dead one.
    old_btn = entry.get("source_tg_msg_id")
    if old_btn:
        d._tg_edit_message(int(old_btn), REPLACED_NOTE)

    btn_msg_id = d._tg_send_with_buttons(
        text, "email", confirm_label="✅ Send", skip_label="❌ Cancel", proposal=revised)
    if not btn_msg_id:
        print("SEND_FAILED: Telegram did not accept the revised proposal; draft NOT stored.",
              file=sys.stderr)
        return 2
    revised["source_tg_msg_id"] = btn_msg_id

    # Re-read: the daemon may have touched unrelated keys while Telegram answered.
    state = d._load()
    state.setdefault("gmail_pending", {})[args.id] = revised
    tg = state.setdefault("tg_pending", {})
    for k in [k for k, v in tg.items() if v.get("gmail_msg_id") == args.id]:
        del tg[k]
    tg[str(btn_msg_id)] = revised
    d._save(state)

    print(f"REVISED: {args.id} → {to_email} (approval {revised.get('approval_id','?')}, "
          f"button {btn_msg_id}). Nothing sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
