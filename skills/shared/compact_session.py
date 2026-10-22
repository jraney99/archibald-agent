#!/usr/bin/env python3
"""
Compact a session, treating "there was nothing to compact" as success.

WHY (2026-09-30): compaction moved from nightly to every 6 hours, because the
cost of a Telegram message is the size of the history re-cached with it — a
message right after compaction re-writes ~1,900 tokens, one after 18 hours of
talking re-writes ~38,600. But `openclaw sessions compact` exits 1 when the
session has no new turns since the last run, which on a quiet afternoon is the
normal case, not a fault. Four runs a day therefore meant a "cron job failed"
alert on any quiet stretch — a false alarm that teaches the user to ignore real ones.

This distinguishes the two. A no-op exits 0 and says so; anything else still
fails loudly, so a genuinely broken compaction is never swallowed.

    python3 compact_session.py                       # the live Telegram session
    python3 compact_session.py --key <session key>
    python3 compact_session.py --when-over 64000     # only if the last prompt exceeded 64k tokens

SIZE TRIGGER (2026-10-03, the user's yes): the fixed schedule could not contain an
evening that grew from 32k to 141k tokens of context in two hours — 96 calls, each
re-writing the whole history, $6.39 in a day. OpenClaw's own auto-compaction only
fires near the 150k window. `--when-over N` reads the live session's transcript,
takes the prompt size of its most recent model call (input + cache read + cache
write), and compacts only when that exceeds N. A cron runs it every 15 minutes;
below the line it does nothing and costs nothing. It steps aside while a turn is
being written (OpenClaw's `<id>.jsonl.lock`) and tries again next tick.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

OPENCLAW = "/opt/homebrew/bin/openclaw"
AGENTS_ROOT = Path.home() / ".openclaw" / "agents"
TELEGRAM_SESSION = "agent:main:telegram:direct:1000000001"
# The session had nothing new to summarise. Reported by the CLI as a failure;
# it is the expected outcome of compacting more often than the user talks.
NO_WORK_REASONS = {"already compacted", "nothing to compact", "no prior history"}


def context_tokens(agent: str, key: str):
    """(tokens, state) for a live session: the prompt size of its most recent model
    call, read from the transcript's last assistant usage. state is "ok", "locked"
    (a turn is being written right now), "no_usage" (fresh session, nothing sent
    yet) or "no_session" (the key is not in the index — a typo must fail loudly)."""
    sessions = AGENTS_ROOT / agent / "sessions"
    try:
        entry = json.loads((sessions / "sessions.json").read_text()).get(key) or {}
    except (OSError, ValueError):
        return None, "no_session"
    sid = entry.get("sessionId")
    if not sid or not (sessions / f"{sid}.jsonl").exists():
        return None, "no_session"
    if (sessions / f"{sid}.jsonl.lock").exists():
        return None, "locked"
    last = None
    for line in (sessions / f"{sid}.jsonl").read_text().splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        message = record.get("message") or {}
        if message.get("role") == "assistant" and message.get("usage") and message.get("model") != "delivery-mirror":
            last = message["usage"]
    if not last:
        return None, "no_usage"
    return sum(int(last.get(field) or 0) for field in ("input", "cacheRead", "cacheWrite")), "ok"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--key", default=TELEGRAM_SESSION)
    parser.add_argument("--agent", default="main")
    parser.add_argument("--when-over", type=int, metavar="TOKENS",
                        help="compact only if the session's last prompt exceeded this many tokens")
    args = parser.parse_args()

    tokens = None
    if args.when_over is not None:
        tokens, state = context_tokens(args.agent, args.key)
        if state == "no_session":
            sys.stderr.write(f"no live session for {args.key}\n")
            return 1
        if state != "ok" or tokens <= args.when_over:
            print(json.dumps({"compacted": False, "reason": state if state != "ok" else "under threshold",
                              "context_tokens": tokens, "threshold": args.when_over, "key": args.key}))
            return 0

    run = subprocess.run(
        [OPENCLAW, "sessions", "compact", args.key, "--agent", args.agent, "--json"],
        capture_output=True, text=True, timeout=200,
    )
    raw = (run.stdout or "").strip()
    try:
        result = json.loads(raw)
    except ValueError:
        result = {}

    # `ok` only means the command ran: a session key that does not exist comes back
    # {"ok": true, "compacted": false, "reason": "no sessionId"} with exit code 0. So
    # success is decided by what actually HAPPENED — compacted, or a recognised
    # nothing-to-do. Anything else fails loudly, because a typo'd key that reports
    # success would let compaction silently never run, and the only symptom would be
    # the cost creeping back with nothing to point at.
    reason_raw = result.get("reason")
    reason = str(reason_raw or "").strip().lower()
    if result.get("compacted"):
        print(json.dumps({"compacted": True, "reason": reason_raw or "compacted", "key": args.key,
                          **({"context_tokens": tokens, "threshold": args.when_over} if tokens is not None else {})}))
        return 0
    if reason in NO_WORK_REASONS:
        print(json.dumps({"compacted": False, "reason": reason_raw, "key": args.key}))
        return 0

    sys.stderr.write((raw or run.stderr or f"exit {run.returncode}").rstrip() + "\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
