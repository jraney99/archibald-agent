#!/usr/bin/env python3
"""
Run a script and send its 'message' field if present directly via the
Telegram Bot API — send nothing if absent. Used by command-mode cron jobs.

Sends directly (bypassing the gateway's Telegram channel) rather than
printing for cron's "announce" delivery, because "announce" routes through
a channel that has an intermittent daily reconnect race and can silently
drop the message (see feedback-telegram-reconnect-delivery-race memory).
Cron jobs using this wrapper must have delivery.mode set to "none".

Usage: python3 run_and_extract.py [--guard MON:6-14] <script_path> [args...]

Optional leading --guard <DAY:LO-HI> makes this a no-op (run nothing, send
nothing, exit 0) unless the current wall clock in the user's *current* timezone
falls on weekday DAY (Mon/Tue/.../Sun, or * for any day) within [LO, HI) hours.
Used by day/time-pinned cron jobs to swallow gateway-restart catch-up runs and
tz-shift misfires -- see misfire_guard.py.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from send_telegram import chunk_text, send_chunk  # noqa: E402
from misfire_guard import within_slot  # noqa: E402

_DAY_IDX = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def _parse_guard(spec: str) -> tuple[int | None, int, int]:
    """'MON:6-14' -> (0, 6, 14); '*:5-9' -> (None, 5, 9)."""
    day_part, _, hours_part = spec.partition(":")
    lo, _, hi = hours_part.partition("-")
    day = None if day_part.strip() in ("", "*") else _DAY_IDX[day_part.strip().lower()]
    return day, int(lo), int(hi)


def deliver(text: str) -> None:
    # send_chunk() already retries transient network failures, but a
    # gateway-restart catch-up run landing seconds after a Mac wake can stay
    # offline past that window. A failed *delivery* must not raise here — it
    # would turn a completed job into an uncaught traceback (and, worse,
    # mask whichever real status the wrapped script produced). Log and move
    # on; the wrapped script's own effects already happened.
    for chunk in chunk_text(text, 4000):
        try:
            send_chunk(chunk)
        except Exception as e:  # noqa: BLE001 — deliberately broad
            print(f"run_and_extract: telegram delivery failed: {e}", file=sys.stderr)


def main():
    args = sys.argv[1:]
    if args and args[0] == "--guard":
        if len(args) < 2:
            print("--guard needs a spec like MON:6-14", file=sys.stderr)
            sys.exit(1)
        weekday, hour_lo, hour_hi = _parse_guard(args[1])
        ok, reason = within_slot(weekday, hour_lo, hour_hi)
        if not ok:
            print(f"run_and_extract: {reason}", file=sys.stderr)
            return
        args = args[2:]

    if not args:
        print("Usage: run_and_extract.py [--guard MON:6-14] <script> [args...]", file=sys.stderr)
        sys.exit(1)
    script = args[0]
    extra_args = args[1:]
    script_name = Path(script).name

    r = subprocess.run(
        [sys.executable, script] + extra_args,
        capture_output=True, text=True, timeout=120,
    )

    stdout = r.stdout.strip()

    # Script crashed without JSON output
    if r.returncode != 0 and not stdout:
        stderr_hint = r.stderr.strip().splitlines()[-1] if r.stderr.strip() else "no stderr"
        deliver(f"⚠️ {script_name} crashed (exit {r.returncode}): {stderr_hint}. Check gateway logs. 🎩")
        return

    try:
        d = json.loads(stdout)
        m = d.get("message") or d.get("info")
        if d.get("status") == "error" or r.returncode != 0:
            # Script reported/exited failure — never send this as a plain
            # success message, or the failure silently reads as a normal
            # notification (and the cron run gets logged as ok).
            status = d.get("status", "unknown")
            deliver(f"⚠️ {script_name} error (status={status}): {m or '(no message or info field)'} 🎩")
        elif m:
            deliver(m)
    except (json.JSONDecodeError, AttributeError, ValueError):
        if stdout:
            # Non-JSON output from a script that should produce JSON
            deliver(f"⚠️ {script_name} produced unexpected output. Check gateway logs. 🎩")


if __name__ == "__main__":
    main()
