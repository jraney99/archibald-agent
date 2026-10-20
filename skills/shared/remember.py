#!/usr/bin/env python3
"""Save a standing instruction, in the user's own words, where it does not age out.

"Moving forward…", "from now on…", "always / never…", "please note that…",
"for future purchases…" — an instruction meant to hold until he changes it.
2026-08-23: "when the cleaner comes, I don't have to be at home" got
"Noted… I'll keep that in mind" and was written nowhere. life-context.md is not
the place: it is a rolling window of the 30 newest harvested statements, so a
rule ages out of it. This file never drops a line unless he says to.

    remember.py --say "<his exact words>" [--topic calendar|email|finance|health|home|wardrobe|travel|general]
    remember.py --list
    remember.py --forget N          # N from --list; only when he asks

Prints one line: "✓ Saved…" / "✓ Already saved…" / "⚠️ NOT saved — why" (exit 1).
That line is the reply. Verbatim only — never a paraphrase, never an inference.
Body measurements and sizes do not belong here (ai-wardrobe-steward Body & Fit
profile); meals, doses and health facts do not either (dr-physician log_day.py).
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

FILE = Path("~/.openclaw/workspace/memory/domains/standing-instructions.md")
TOPICS = ("calendar", "email", "finance", "health", "home", "wardrobe", "travel", "general")
HEADER = ("# Standing Instructions\n\n"
          "The user's own words, saved by `Skills/shared/remember.py` when he gives an instruction meant to hold\n"
          "until he changes it. Read this before scheduling, planning, buying or recommending in that topic.\n"
          "A newer line overrides an older one on the same subject. Never edit by hand.\n\n")
LINE = re.compile(r"^- (\d{4}-\d{2}-\d{2}) \[([a-z]+)\] (.+)$")


def read_lines() -> list[tuple[str, str, str]]:
    if not FILE.exists():
        return []
    return [m.groups() for m in (LINE.match(ln) for ln in FILE.read_text().splitlines()) if m]


def write_lines(rows: list[tuple[str, str, str]]) -> None:
    FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=FILE.parent, prefix=".standing-", suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        f.write(HEADER + "".join(f"- {d} [{t}] {s}\n" for d, t, s in rows))
    os.replace(tmp, FILE)


def fail(why: str) -> int:
    print(f"⚠️ NOT saved — {why}")
    return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Save, list or remove a standing instruction.")
    ap.add_argument("--say", help="his exact words")
    ap.add_argument("--topic", default="general")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--forget", type=int, metavar="N")
    args = ap.parse_args(argv)
    rows = read_lines()

    if args.list:
        print("\n".join(f"{i}. {d} [{t}] {s}" for i, (d, t, s) in enumerate(rows, 1)) or "No standing instructions saved.")
        return 0

    if args.forget is not None:
        if not 1 <= args.forget <= len(rows):
            print(f"⚠️ Nothing removed — there is no instruction #{args.forget} (run --list).")
            return 1
        gone = rows.pop(args.forget - 1)
        write_lines(rows)
        print(f"✓ Removed standing instruction #{args.forget}: {gone[2]}")
        return 0

    words = " ".join((args.say or "").split())
    if not words:
        return fail("nothing to save; pass his exact words with --say")
    if len(words) > 600:
        return fail("that is over 600 characters; a standing instruction is a sentence or two")
    topic = args.topic.strip().lower()
    if topic not in TOPICS:
        return fail(f"unknown topic {args.topic!r}. Use one of: {', '.join(TOPICS)}")
    if any(s.lower() == words.lower() for _, _, s in rows):
        print(f"✓ Already saved as a standing instruction: {words}")
        return 0

    today = datetime.now(ZoneInfo("America/Chicago")).date().isoformat()
    rows.append((today, topic, words))
    write_lines(rows)
    if (today, topic, words) not in read_lines():  # read it back before claiming anything
        return fail("the file was written but the line did not read back")
    print(f"✓ Saved as standing instruction #{len(rows)} [{topic}]: {words}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
