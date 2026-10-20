#!/usr/bin/env python3
"""
Handles the two buttons on the nightly putting cue:

    confirm:putting  ->  log_putting.py --done
    skip:putting     ->  log_putting.py --skip

Acknowledges the tap by editing the cue in place and clearing its buttons, then
prints NO_REPLY so the callback handler doesn't also send a second message
(The user's no-double-message rule). Falls back to printing the text if the edit
can't be made, so a confirmation is never silently dropped.

Also usable from a normal conversation when he says he's done them without
tapping:

    python3 log_putting.py --done
    python3 log_putting.py --done --putts 30    # partial session, honestly recorded
    python3 log_putting.py --skip
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))
import putting_status as status  # noqa: E402
from data_paths import path as data_path  # noqa: E402
from send_telegram import edit_message_text  # noqa: E402

STATE_PATH = data_path("putting_cue_state.json")


def cue_message_id() -> int | None:
    """The cue this tap belongs to — but only if it is tonight's. A stale id from
    an earlier night would edit the wrong message, and the cue for a night is
    deleted when the next one is sent."""
    try:
        state = json.loads(STATE_PATH.read_text())
    except Exception:
        return None
    if state.get("night_of") != status.night_of().isoformat():
        return None
    return state.get("message_id") or None


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--done", action="store_true")
    group.add_argument("--skip", action="store_true")
    parser.add_argument("--putts", type=int, default=None,
                        help="how many he actually did; defaults to the nightly goal")
    args = parser.parse_args()

    now = datetime.now(status.TZ)
    night = status.night_of(now)
    goal = status.goal_putts()

    if args.done:
        putts = args.putts if args.putts is not None else goal
        status.append_entry(status.DONE, night=night, putts=putts, when=now)
        entries = status.load_entries()
        streak = status.streak_nights(entries, night)
        night_word = "night" if streak == 1 else "nights"
        text = f"⛳ {putts} putts logged. {streak} {night_word} running."
    else:
        status.append_entry(status.SKIPPED, night=night, when=now)
        entries = status.load_entries()
        # What he is giving up, stated once and plainly — no lecture.
        previous = status.streak_nights(entries, night - timedelta(days=1))
        tail = f" Streak was {previous}." if previous else ""
        text = f"⛳ Noted, no putting tonight.{tail}"

    message_id = cue_message_id()
    if message_id and edit_message_text(message_id, text):
        print("NO_REPLY")
    else:
        print(text)


if __name__ == "__main__":
    main()
