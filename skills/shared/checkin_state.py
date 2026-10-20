#!/usr/bin/env python3
"""
Shared helper for a Telegram message with multiple INDEPENDENT button
groups (e.g. send_energy_checkin.py's readiness + caffeine + alcohol on
one daily message) — a tap on one group must only trim that group's
row(s), never touch the others still pending, and never send a second
message while others are still live. Same underlying pattern
confirm_meal.py / log_supplement.py use for their dual meal+supplement
row trim, generalized from 2 groups to N.

2026-09-11 (system audit, cross-skill pass): promoted from
nutrition/scripts/_checkin_state.py, where it started as a nutrition-only
helper for the readiness/caffeine/alcohol check-in. The pattern itself —
one message, several independent tap groups, edit-in-place per group,
collapse to a summary once every group answers — isn't nutrition-specific
and will be wanted again anywhere a single check-in message needs more
than one quick answer. No behavior change for nutrition's own use; the
one-time "(all done for this week)" wording is now a caller-supplied
parameter instead of hardcoded, since a shared helper can't assume any
particular skill's cadence.

State shape:
    {"message_id": <int>, "rows": {"<group>": [[button, ...], ...], ...}}

Usage (from a log_*.py handler):
    from checkin_state import resolve_group_tap
    plain = resolve_group_tap(STATE_PATH, "caffeine", "Logged: stayed under 10am.")
    print(plain)   # may be the literal string "NO_REPLY"
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

try:  # chat cleanup (2026-09-20): answered prompts are archived + removed at the nightly sweep
    sys.path.insert(0, "~/.openclaw/workspace/Skills/shared")
    from chat_cleanup import mark_answered
except Exception:  # never let chat hygiene break a log
    def mark_answered(*a, **k):
        return False

TG_TOKEN = __import__("runpy").run_path("~/.openclaw/workspace/Skills/shared/runtime_secrets.py")["require_secret"]("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID = "1000000001"


def _load(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _edit_reply_markup(message_id: int, rows: list) -> bool:
    payload = {"chat_id": TG_CHAT_ID, "message_id": message_id, "reply_markup": {"inline_keyboard": rows}}
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TG_TOKEN}/editMessageReplyMarkup",
        data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10):
            return True
    except Exception:
        return False


def _edit_message_text(message_id: int, text: str) -> bool:
    payload = {"chat_id": TG_CHAT_ID, "message_id": message_id, "text": text, "reply_markup": {"inline_keyboard": []}}
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TG_TOKEN}/editMessageText",
        data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10):
            return True
    except Exception:
        return False


def edit_in_place(message_id: int, text: str, rows: list) -> bool:
    """Replace the message's text AND keyboard in one call."""
    payload = {"chat_id": TG_CHAT_ID, "message_id": message_id, "text": text,
               "reply_markup": {"inline_keyboard": rows}}
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TG_TOKEN}/editMessageText",
        data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10):
            if not rows:  # no buttons left = fully answered
                mark_answered(message_id, text, skill="check-in")
            return True
    except Exception:
        return False


def progress_text(original: str, title: str | None, done: dict, rows: list) -> str:
    """The one message, as it should read after a tap. While buttons remain:
    the original nudge with a ✅ line per answered group underneath. Once
    every group is answered: just the title and the ✅ lines."""
    lines = [f"✅ {line}" for line in done.values()]
    if rows:
        return original.rstrip() + "\n\n" + "\n".join(lines)
    return "\n".join(([title] if title else []) + lines)


def tick_in_place(record: dict, group: str, plain: str, rows: list) -> bool:
    """For a caller with its own state shape (the meal nudges). The caller has
    already removed the tapped group's buttons and passes what is left as
    `rows`. Returns True when the message was updated in place -- the caller
    then prints NO_REPLY. False (no saved "text", or Telegram refused) means
    fall back to saying it in a message."""
    if not record.get("text") or not record.get("message_id"):
        return False
    done = record.setdefault("done", {})
    done[group] = plain
    return edit_in_place(record["message_id"], progress_text(record["text"], record.get("title"), done, rows), rows)


def resolve_group_tap(state_path: Path, group: str, plain: str, all_done_suffix: str = "(all done)") -> str:
    """Trims `group`'s row(s) from the saved state's keyboard. If other
    groups are still pending, edits just the reply markup and returns
    `plain` as a normal new message. If this was the last pending group,
    collapses the whole message to a summary line and returns "NO_REPLY".
    `all_done_suffix` lets a caller phrase the final collapse for its own
    cadence (e.g. "(all done for today)"); defaults to something generic."""
    state = _load(state_path)
    if not state or group not in state.get("rows", {}):
        return plain  # no known message to edit (stale/expired state) -- still report the outcome

    message_id = state["message_id"]
    state["rows"].pop(group, None)

    # 2026-09-20: one message, ticked off in place. The docstring above always
    # said "never send a second message while others are still live", but
    # each non-final tap returned `plain` as a NEW message and the final tap
    # overwrote the original with only its own line -- 4 taps on the breakfast
    # nudge = 3 receipts + a leftover "✅ Logged <supplement>." A sender opts in
    # by saving the nudge's "text" (and optional "title") in the state.
    if state.get("text"):
        done = state.setdefault("done", {})
        done[group] = plain
        remaining = [row for rows in state["rows"].values() for row in rows]
        if edit_in_place(message_id, progress_text(state["text"], state.get("title"), done, remaining), remaining):
            if state["rows"]:
                state_path.write_text(json.dumps(state))
            else:
                state_path.unlink(missing_ok=True)
            return "NO_REPLY"
        state_path.write_text(json.dumps(state))
        return plain  # the edit failed -- say it in a message rather than lose it

    if state["rows"]:
        remaining = [row for rows in state["rows"].values() for row in rows]
        _edit_reply_markup(message_id, remaining)
        state_path.write_text(json.dumps(state))
        return plain

    if _edit_message_text(message_id, f"✅ {plain} {all_done_suffix}".rstrip()):
        mark_answered(message_id, f"✅ {plain} {all_done_suffix}".rstrip(), skill="check-in")
        state_path.unlink(missing_ok=True)
        return "NO_REPLY"
    return plain
