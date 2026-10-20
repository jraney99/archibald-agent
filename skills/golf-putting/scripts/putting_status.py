#!/usr/bin/env python3
"""
Shared putting-habit math: what happened on a given night, and how long the
streak is. Imported by send_putting_cue.py and log_putting.py so "did he putt
last night" is computed in exactly one place, and run directly for on-demand
status or the morning briefing line.

A "night" is not a calendar day. The user putts before bed, sometimes after
midnight, so a session logged at 12:30am belongs to the night before —
night_of() applies the config's rollover hour (4am) to get that right. Without
it, a 12:30am session would land on the next day and read as two misses.

Streak rule (The user, 2026-09-27): a night with a 'done' entry counts; a night he
tapped "Not tonight" breaks it; a night he was travelling is stepped over
entirely, neither counting nor breaking, so a trip can't wipe out a long run;
a night with no entry at all is a miss and breaks it.

Usage:
    python3 putting_status.py                  # tonight's status, JSON
    python3 putting_status.py --brief-morning   # one line for the morning briefing
"""
from __future__ import annotations

import argparse
import fcntl
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))
from current_location import get_tz  # noqa: E402
from data_paths import path as data_path  # noqa: E402

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "putting_config.json"
LOG_PATH = data_path("putting_log.json")
LOCK_PATH = LOG_PATH.with_suffix(".json.lock")
TZ = get_tz()

DONE, SKIPPED, AWAY = "done", "skipped", "away"


def load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text())
    except Exception:
        return {}


def goal_putts() -> int:
    return load_config().get("nightly_goal_putts", 50)


def cue_time() -> str:
    return load_config().get("cue_time", "21:45")


def rollover_hour() -> int:
    return load_config().get("night_rollover_hour", 4)


def skip_while_away() -> bool:
    return bool(load_config().get("skip_while_away", True))


def night_of(when: datetime | None = None) -> date:
    """The practice night a moment belongs to. Before the rollover hour, it is
    still the previous night — 12:30am Monday is Sunday night's session."""
    when = when or datetime.now(TZ)
    if when.tzinfo is not None:
        when = when.astimezone(TZ)
    return when.date() - timedelta(days=1) if when.hour < rollover_hour() else when.date()


def load_entries() -> list:
    if not LOG_PATH.exists():
        return []
    try:
        entries = json.loads(LOG_PATH.read_text())
        return entries if isinstance(entries, list) else []
    except Exception:
        return []


def nights(entries: list) -> dict:
    """night (ISO string) -> status. A later entry for the same night wins, so
    tapping Done after an 'away' auto-entry records what actually happened."""
    out = {}
    for e in entries:
        n, status = e.get("night_of"), e.get("status")
        if n and status:
            out[n] = status
    return out


def status_for_night(entries: list, night: date) -> str | None:
    return nights(entries).get(night.isoformat())


def putts_for_night(entries: list, night: date) -> int:
    total = 0
    for e in entries:
        if e.get("night_of") == night.isoformat() and e.get("status") == DONE:
            total = e.get("putts", 0)
    return total


def streak_nights(entries: list, through: date) -> int:
    """Consecutive nights ending at `through` (inclusive) that he putted,
    stepping over travel nights. Bounded by the log's earliest night, so an
    all-travel log can't loop forever."""
    by_night = nights(entries)
    if not by_night:
        return 0
    earliest = min(by_night)
    streak, d = 0, through
    while d.isoformat() >= earliest:
        status = by_night.get(d.isoformat())
        if status == DONE:
            streak += 1
        elif status != AWAY:  # skipped, or nothing logged at all
            break
        d -= timedelta(days=1)
    return streak


def last_done_night(entries: list) -> date | None:
    done = [n for n, s in nights(entries).items() if s == DONE]
    return date.fromisoformat(max(done)) if done else None


def append_entry(status: str, night: date | None = None, putts: int | None = None,
                 when: datetime | None = None) -> dict:
    """Record one night's outcome. Both writers (the cue's travel auto-entry and
    the button handler) come through here so they share one lock — the callback
    plugin and a redundant agent-turn can genuinely process the same tap at the
    same time, and an unlocked read-modify-write of this file doesn't just lose
    an update, it can leave unparseable JSON (proven in the hydration skill).

    Re-recording the same status for the same night is a no-op, so a double
    dispatch of one tap logs once."""
    when = when or datetime.now(TZ)
    night = night or night_of(when)
    entry = {"night_of": night.isoformat(), "status": status, "timestamp": when.isoformat()}
    if putts is not None:
        entry["putts"] = putts

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCK_PATH, "w") as lockfile:
        fcntl.flock(lockfile, fcntl.LOCK_EX)
        try:
            entries = load_entries()
            if status_for_night(entries, night) != status:
                entries.append(entry)
                LOG_PATH.write_text(json.dumps(entries, indent=2))
        finally:
            fcntl.flock(lockfile, fcntl.LOCK_UN)
    return entry


def brief_morning() -> str:
    """One line for the morning briefing, or "" to print nothing at all.

    Silent in two cases: he has never logged a session (the habit hasn't
    started, so the briefing shouldn't grow a section about it), and travel
    nights (nothing to report, and no streak to defend)."""
    entries = load_entries()
    if not entries:
        return ""

    last = night_of() - timedelta(days=1)
    status = status_for_night(entries, last)
    if status == AWAY:
        return ""

    goal = goal_putts()
    if status == DONE:
        streak = streak_nights(entries, last)
        putts = putts_for_night(entries, last) or goal
        night_word = "night" if streak == 1 else "nights"
        return f"⛳ Putting: {putts} putts last night ✅ — {streak} {night_word} running"

    streak_before = streak_nights(entries, last - timedelta(days=1))
    done = last_done_night(entries)
    tail = f", last done {done.strftime('%a %b %-d')}" if done else ""
    if status == SKIPPED:
        return f"⛳ Putting: skipped last night — streak was {streak_before}{tail}"
    return f"⛳ Putting: nothing logged last night — streak was {streak_before}{tail}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--brief-morning", action="store_true")
    args = parser.parse_args()

    if args.brief_morning:
        line = brief_morning()
        if line:
            print(line)
        return

    entries = load_entries()
    tonight = night_of()
    print(json.dumps({
        "night_of": tonight.isoformat(),
        "goal_putts": goal_putts(),
        "status": status_for_night(entries, tonight),
        "putts": putts_for_night(entries, tonight),
        "streak_nights": streak_nights(entries, tonight),
        "last_done_night": (d.isoformat() if (d := last_done_night(entries)) else None),
    }))


if __name__ == "__main__":
    main()
