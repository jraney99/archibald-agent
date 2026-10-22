#!/usr/bin/env python3
"""Every script that SENDS a button prompt must leave an "answered" signal.

WHY (2026-10-12): The user's Telegram was cluttered with old unanswered prompts. The
cause was coverage, not policy — 42 scripts send prompts and only 13 registered
with chat_cleanup, so the sweep could not see the rest. Worse, a Telegram bot
cannot delete its own message after 48 hours, so an unseen prompt becomes
permanent: 13 rows in the October archive are already unremovable for that reason.

A sender satisfies this contract one of two ways:
  * it registers the message with chat_cleanup (track / mark_answered / remove_now
    / replace_stream), or
  * every callback it emits reaches a dispatch handler that edits the message in
    place (edit_message_text / edit_reply_markup / tick_in_place / remove_now /
    resolve_group_tap), which is what marks a prompt answered.

Senders that reach Telegram through shared/send_telegram.py's send_chunk() are
covered automatically since 2026-10-12 — it registers anything carrying
reply_markup as kind="answerable". The ones that POST api.telegram.org themselves
are not, and this names them.

BASELINE, not a gate: the 41 direct-POST senders predate the shared sender and are
listed below. The list may only SHRINK. A NEW sender, or one newly given buttons,
has to satisfy the contract.

Read-only: imports nothing from the skills, sends nothing, touches no state.
"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OPENCLAW = HERE.parent
SKILLS = OPENCLAW / "skills"
BASELINE = HERE / "prompt-tracking-baseline.json"

SKIP = ("/tests/", "/test_", ".test.", "run_eval", "__pycache__", "/references/")
SENDS = re.compile(r"sendMessage|sendPhoto|sendDocument|sendMediaGroup|send_chunk|_tg_send")
TRACKS = re.compile(r"chat_cleanup")
SHARED = re.compile(r"from send_telegram import|^import send_telegram", re.M)
# Every mechanism a handler actually uses to edit the prompt in place. The first
# version of this listed only the chat_cleanup/checkin_state names and reported 12
# false positives — the wardrobe, transcript, meals and vacation handlers
# all edit, just through edit_or_fallback / editMessage / edit_board.
EDITS = re.compile(r"edit_message_text|edit_reply_markup|tick_in_place|remove_now"
                   r"|resolve_group_tap|mark_answered|edit_in_place|edit_or_fallback"
                   r"|editMessage|edit_board")


def dispatch_handlers():
    table = json.loads((OPENCLAW / "plugins/callback-dispatch/dispatch-table.json").read_text())
    by_prefix = []
    for token, spec in (table.get("exact") or {}).items():
        by_prefix.append((token, (spec.get("argv") or [None])[0]))
    for spec in (table.get("patterns") or []):
        literal = re.match(r"\^([A-Za-z0-9:_-]+)", spec["re"])
        if literal:
            by_prefix.append((literal.group(1), (spec.get("argv") or [None])[0]))
    return [(p, h) for p, h in by_prefix if p and h]


def edits_in_place(script, cache={}):
    if script not in cache:
        target = SKILLS / script
        cache[script] = bool(EDITS.search(target.read_text())) if target.exists() else False
    return cache[script]


def senders():
    """Scripts that create a button prompt, with how they satisfy the contract."""
    handlers = dispatch_handlers()
    found = {}
    for path in sorted(SKILLS.rglob("*.py")):
        text_path = str(path)
        if any(k in text_path for k in SKIP) or path.name.startswith("test_"):
            continue
        source = path.read_text()
        if "callback_data" not in source and "inline_keyboard" not in source:
            continue
        if not SENDS.search(source):
            continue        # builds buttons for another script to send
        rel = str(path.relative_to(SKILLS))
        tokens = set(re.findall(r"""callback_data["']?\s*[:=]\s*f?["']([^"']+)""", source))
        reached = set()
        for token in tokens:
            literal = re.match(r"([A-Za-z0-9:_-]+)", token)
            if not literal:
                continue
            best = None
            for prefix, handler in handlers:
                if token.startswith(prefix) or prefix.startswith(literal.group(1)):
                    if best is None or len(prefix) > len(best[0]):
                        best = (prefix, handler)
            if best:
                reached.add(best[1])
        found[rel] = {
            "via_shared_sender": bool(SHARED.search(source)),
            "registers": bool(TRACKS.search(source)),
            "handler_edits": any(edits_in_place(h) for h in reached),
        }
    return found


def main():
    found = senders()
    uncovered = sorted(k for k, v in found.items()
                       if not (v["via_shared_sender"] or v["registers"] or v["handler_edits"]))
    baseline = sorted(json.loads(BASELINE.read_text())) if BASELINE.exists() else []
    new = [s for s in uncovered if s not in baseline]
    fixed = [s for s in baseline if s not in uncovered]

    print(f"prompt senders: {len(found)} | satisfy the contract: {len(found) - len(uncovered)} "
          f"| uncovered: {len(uncovered)} (baseline {len(baseline)})")
    if fixed:
        print(f"\n{len(fixed)} sender(s) now covered — take them out of "
              f"{BASELINE.name} so the list keeps shrinking:")
        for s in fixed:
            print(f"   {s}")
    if new:
        print(f"\n{len(new)} sender(s) send a button prompt with NO 'answered' signal:")
        for s in new:
            print(f"   {s}")
        print("\nAn unanswered prompt nothing tracks sits in the chat, and after 48 hours")
        print("Telegram will not let the bot delete it at all. Fix one of three ways:")
        print("  - send through shared/send_telegram.py's send_chunk() (tracked automatically), or")
        print("  - call chat_cleanup.track(...) when sending, or")
        print("  - give its callbacks a dispatch handler that edits the message in place.")
        return 1
    if fixed:
        return 1
    print("every button prompt has an answered signal (or is a listed legacy sender)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
