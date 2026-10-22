#!/usr/bin/env python3
"""Run the guard/button suite unattended. Silent when it passes.

WHY (2026-09-19): the suite that would have caught two days of dead outfit-board
buttons existed only for sessions that remembered to run it. This runs it daily.

It messages the user ONLY when a failure is new or has changed since the last alert —
a suite that stays broken does not nag every morning (Notification digestibility).
A pass clears the remembered failure, so the same breakage recurring later alerts
again. Wording and trigger approved by the user before enabling (Automation rollout gate).

Read-only: test-all.mjs runs every hook with effects disabled. The wardrobe eval is
deliberately NOT run here — it swaps the live profile/ directory while it runs, which
is unsafe next to crons that read it.

Usage:
  python3 nightly_guard_check.py [--dry-run] [--suite-cmd '<json argv>']
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
OPENCLAW = HERE.parent
STATE = OPENCLAW / "state" / "guard_check_state.json"
NODE = "/opt/homebrew/bin/node"
SUITE = [NODE, str(HERE / "test-all.mjs")]
# launchd gives cron commands a bare PATH; the suite shells out to node and python3.
PATH = "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
TIMEOUT_S = 600


# Two different shapes print "FAIL:" into this suite's output and they need
# opposite handling:
#   harness  FAIL: callback-dispatch — loaded content stamp differs; restart the gateway
#   unittest FAIL: test_baseline_only_shrinks (__main__.Layout)
# The harness line carries its whole message, usually naming the fix. The unittest
# line carries only a name, and its message follows some lines below.
HARNESS_FAIL = re.compile(r"^(?:FAIL|ERROR): (?P<name>[^\s(]+) +[\u2014\u2013-] +(?P<message>\S.*)$", re.M)
UNITTEST_FAIL = re.compile(r"^(?:FAIL|ERROR): (?P<name>[\w.]+) +\((?P<where>[^)]+)\)\s*$", re.M)
# Where a unittest failure's own block stops. Mining past this is how one failure's
# name got glued to another failure's text.
# NB: not the "-----" rule. unittest prints that BOTH directly under the FAIL line
# and again above "Ran N tests", so treating it as a terminator empties every block
# and loses the message entirely.
BLOCK_END = re.compile(r"^(?:={10,}|(?:FAIL|ERROR): |Ran \d+ tests?\b|OK$|HARNESS )", re.M)
# A counter is never an explanation.
NOT_A_REASON = re.compile(r"^(?:Ran \d+ tests?\b|OK\b|FAILED\b|Traceback\b)")


def unittest_failure(output):
    """One line naming what failed and why, taken from THAT failure only.

    2026-09-28: `AssertionError[^\n]*` stopped at the newline, so the user was told
    only `{'.../add_note.py': ['journal_notes.json']} != {}` — which reads like a
    script had started typing a data path, when the opposite had happened: one was
    FIXED and the baseline simply needed re-snapshotting. The test said so on the
    next line. Naming the test that failed says it even more plainly.

    2026-10-12: he got `callback: Ran 31 tests in 7.060s`, which is two unrelated
    failures welded together and explains neither. `(\w+)` truncated
    "callback-dispatch" at the hyphen, and the detail was then mined from the first
    `AssertionError:` ANYWHERE later in the output — a different suite's failure —
    landing on a test counter as the reason. The actionable sentence, "restart the
    gateway", was on the matched line all along and was discarded. So: capture the
    whole name, keep the harness line's own message, and never read a unittest
    failure's detail past the end of its own block."""
    harness = HARNESS_FAIL.search(output)
    unit = UNITTEST_FAIL.search(output)
    # Whichever the suite reported first, so the summary describes the first thing
    # that broke rather than whichever shape happens to be matched first here.
    if harness and (not unit or harness.start() < unit.start()):
        return f"{harness.group('name')}: {harness.group('message').strip()}"
    if not unit:
        return None
    block = output[unit.end():]
    stop = BLOCK_END.search(block)
    block = block[:stop.start()] if stop else block
    assertion = re.search(r"^AssertionError:.*$", block, re.M)
    if assertion:
        # Skip the +/- diff unittest prints for containers; the first prose line
        # after it is the message the test author wrote for a human.
        for line in block[assertion.end():].splitlines():
            line = line.strip()
            if line and line[0] not in "+-?{}[]\"'" and not NOT_A_REASON.match(line):
                return f"{unit.group('name')}: {line.rstrip(':')}"
        return f"{unit.group('name')}: {assertion.group(0)}"
    return f"{unit.group('name')} failed"


def summarise(output):
    """One plain line saying what broke, from the suite's own output."""
    lines = [l.strip() for l in output.splitlines() if l.strip()]
    dead = [l for l in lines if l.startswith("DEAD BUTTON")]
    if dead:
        first = re.sub(r"^DEAD BUTTON\s+", "", dead[0])
        script, _, token = first.partition("  ")
        more = f" (+{len(dead) - 1} more)" if len(dead) > 1 else ""
        return f"a button sent by {script.split(':')[0]} never reaches its handler: {token.strip()}{more}"
    named = unittest_failure(output)
    if named:
        return re.sub(r"\s+", " ", named)[:220]
    for pattern in (r"Runtime boundary changed[^\n]*", r"AssertionError[^\n]*", r"\bError: [^\n]*", r"[^\n]*\bfailed\b[^\n]*"):
        hit = re.search(pattern, output)
        if hit:
            return re.sub(r"\s+", " ", hit.group(0))[:220]
    return (lines[-1] if lines else "the suite produced no output")[:220]


def signature(summary):
    return hashlib.sha256(re.sub(r"\d+", "#", summary).encode()).hexdigest()[:16]


def message(summary):
    return (f"⚠️ Daily self-check failed: {summary}\n"
            f"Nothing was changed. Ask a Claude Code session to run the guard suite. 🎩")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="print what would be sent; send nothing, remember nothing")
    ap.add_argument("--suite-cmd", help="JSON argv to run instead of the real suite (tests)")
    ap.add_argument("--state", default=str(STATE))
    args = ap.parse_args()
    state_path = Path(args.state)
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    cmd = json.loads(args.suite_cmd) if args.suite_cmd else SUITE
    try:
        run = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_S, cwd=str(OPENCLAW), env={**os.environ, "PATH": PATH})
        passed, output = run.returncode == 0, run.stdout + run.stderr
    except subprocess.TimeoutExpired:
        passed, output = False, f"Error: the guard suite did not finish within {TIMEOUT_S // 60} minutes"
    now = datetime.now().astimezone().isoformat(timespec="seconds")

    if passed:
        result = {"status": "pass", "alert": None}
        new_state = {"status": "pass", "checked_at": now}
    else:
        summary = summarise(output)
        sig = signature(summary)
        already = state.get("status") == "fail" and state.get("signature") == sig
        result = {"status": "fail", "summary": summary, "alert": None if already else message(summary)}
        new_state = {"status": "fail", "signature": sig, "summary": summary, "checked_at": now,
                     "alerted_at": state.get("alerted_at") if already else now}

    if args.dry_run:
        print(json.dumps({**result, "dry_run": True}, indent=2, ensure_ascii=False))
        return
    if result["alert"] and os.environ.get("GUARD_CHECK_NO_SEND") != "1":      # tests must never reach Telegram
        sys.path.insert(0, str(OPENCLAW / "skills" / "shared"))
        import send_telegram
        send_telegram.send_chunk(result["alert"])
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(new_state, indent=2))
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
