#!/usr/bin/env python3
"""
Daily FYI digest — drains state["fyi_queue"] (filled by daemon.py's
run_gmail_check for tier=="fyi") into one Telegram message.

Runs from an openclaw command cron at 6pm CT. Sends nothing when the queue is
empty. Direct Telegram Bot API, zero LLM tokens.

Presentation follows the one shared standard —
workspace/Documentation/rules/33-output-mode.md, rendered with
Skills/shared/output_mode.py. The live digest keeps its current wording until
output_mode.LIVE_DELIVERY is on (CLAUDE.md -> Automation rollout gate);
--preview-output-mode prints the new text without sending anything.

Usage: python3 send_fyi_digest.py [--dry-run | --preview-output-mode]
"""
from __future__ import annotations

import os
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent.parent / "shared"))
from output_mode import LIVE_DELIVERY, Report  # noqa: E402
from send_telegram import chunk_text, send_chunk  # noqa: E402

STATE = Path(os.path.expanduser("~/.openclaw/email_monitor_state.json")).expanduser()
CT = ZoneInfo("America/Chicago")


def _load() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def _save(state: dict) -> None:
    STATE.write_text(json.dumps(state, indent=2))


def build_legacy(queue: list) -> str:
    n = len(queue)
    head = f"🎩 {n} FYI email{'s' if n != 1 else ''} today, Master Wayne:"
    lines = [head, ""]
    for e in queue:
        frm = e.get("from", "?")
        subj = (e.get("subject") or "(no subject)").strip()
        summ = (e.get("summary") or "").strip()
        mid = e.get("mid", "")
        lines.append(f"• {frm} — {subj}")
        if summ and summ.lower() not in subj.lower():
            lines.append(f"    {summ}")
        if mid:
            lines.append(f"    https://mail.google.com/mail/u/0/#inbox/{mid}")
        lines.append("")
    return "\n".join(lines).rstrip()


def build_output_mode(queue: list) -> str:
    """Output Mode — workspace/Documentation/rules/33-output-mode.md. FYI is the
    tier that needs no reply, so the digest says so out loud rather than leaving
    The user to work out whether any of it is his to action."""
    n = len(queue)
    r = Report(
        "📬", "FYI email",
        headline=f"{n} today",
        tldr=f"{n} message{'' if n == 1 else 's'} filed for information. None needs a reply.",
        coverage=f"As of {datetime.now(CT).strftime('%Y-%m-%d %-I:%M %p')} CT",
        hat=True,
    )
    section = r.section("📥", "Received", headline=str(n))
    # A digest is a list of things, not a value column — bullets, per the rule.
    for e in queue:
        subj = (e.get("subject") or "(no subject)").strip()
        summ = (e.get("summary") or "").strip()
        section.bullet(f"{e.get('from', '?')} — {subj}")
        if summ and summ.lower() not in subj.lower():
            section.line(f"  {summ}")
        if e.get("mid"):
            section.line(f"  https://mail.google.com/mail/u/0/#inbox/{e['mid']}")
    r.handling(f"Filed {n} FYI email{'' if n == 1 else 's'}; nothing awaiting a reply",
               key="filed fyi email")
    return r.render()


def build(queue: list) -> str:
    return build_output_mode(queue) if LIVE_DELIVERY else build_legacy(queue)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--preview-output-mode", action="store_true",
                    help="Print the Output Mode text without sending, so it can be "
                         "approved before LIVE_DELIVERY is flipped on (CLAUDE.md gate)")
    args = ap.parse_args()
    if args.preview_output_mode:
        args.dry_run = True

    state = _load()
    queue = state.get("fyi_queue", []) or []

    if not queue:
        if args.dry_run:
            print("(fyi_queue empty — nothing to send)")
        return

    text = build_output_mode(queue) if args.preview_output_mode else build(queue)

    if args.dry_run:
        print(text)
        return

    sent_ids = []
    for chunk in chunk_text(text, 4000):
        sent_ids.append((send_chunk(chunk) or {}).get("result", {}).get("message_id"))
    # 2026-09-20: only the latest digest stays in the chat; the previous one is
    # archived to ~/.openclaw/telegram_archive/ and removed. Never raises.
    # This also expires it on its own: The user reads the digest when it arrives and then has
    # to look at it until tomorrow's replaces it — "those delete after 2 hours after being sent
    # to me" (2026-10-08). FYI_DIGEST_TTL_H is his two hours; the frequent sweep enforces it.
    try:
        from chat_cleanup import FYI_DIGEST_TTL_H, replace_stream
        replace_stream("email:fyi-digest", [m for m in sent_ids if m], text, skill="email-monitor",
                       ttl_hours=FYI_DIGEST_TTL_H)
    except Exception:
        pass

    # Drain only what we just sent — anything the daemon queued between _load()
    # and here stays for tomorrow.
    sent_mids = {e.get("mid") for e in queue}
    state = _load()
    state["fyi_queue"] = [e for e in state.get("fyi_queue", [])
                          if e.get("mid") not in sent_mids]
    _save(state)


if __name__ == "__main__":
    main()
