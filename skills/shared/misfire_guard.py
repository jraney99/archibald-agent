#!/usr/bin/env python3
"""
Misfire guard for day/time-pinned cron jobs.

Why this exists
---------------
OpenClaw's cron scheduler runs "missed" jobs on gateway startup (catch-up).
Separately, detect_travel.py shifts a job's --tz when the user travels. Shifting a
weekly job's tz westward (e.g. America/Chicago -> America/Los_Angeles) moves its
most-recent past occurrence ~2h later in absolute time than when the job
actually ran that week, so the already-completed run looks missed -- and the
next gateway restart fires it once, on the wrong day, at a random hour.

The scheduler internals can't be patched (frozen binary), so the fix is the
same one already used by the evening-accountability job's "STEP 0" prompt: the
payload checks the wall clock in the user's *current* timezone and does nothing if
it's plainly outside the slot the job is scheduled for.

Usage
-----
    from misfire_guard import within_slot

    ok, reason = within_slot(weekday=0, hour_lo=8, hour_hi=14)  # Monday 08:00-13:59
    if not ok:
        print(f"misfire-guard: {reason}", file=sys.stderr)
        return

`weekday` is Monday=0 .. Sunday=6 (Python's date.weekday()); pass None to allow
any day. The window is [hour_lo, hour_hi) in 24h local time.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from current_location import get_tz  # noqa: E402

_DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def within_slot(weekday: int | None, hour_lo: int, hour_hi: int,
                now: datetime | None = None) -> tuple[bool, str]:
    """True when `now` (in the user's current tz) falls in the job's scheduled slot."""
    now = now or datetime.now(get_tz())
    if weekday is not None and now.weekday() != weekday:
        want = _DAYS[weekday]
        return False, (f"skipped misfire: {now:%a %H:%M} {now.tzname()} "
                       f"is not {want} (catch-up run after a restart / tz shift)")
    if not (hour_lo <= now.hour < hour_hi):
        return False, (f"skipped misfire: {now:%a %H:%M} {now.tzname()} "
                       f"is outside {hour_lo:02d}:00-{hour_hi:02d}:00 "
                       f"(catch-up run after a restart / tz shift)")
    return True, f"in slot: {now:%a %H:%M} {now.tzname()}"


def near_slot(slots_hhmm, grace_min: int = 90, late_grace_min: int | None = None,
              now: datetime | None = None) -> tuple[bool, str]:
    """True when `now` (The user's current tz) is close enough to one of the job's
    legitimate fire times to be a real run rather than a catch-up misfire.

    `within_slot` needs a hand-picked hour window per job; `near_slot` takes the
    job's actual scheduled time(s) as "HH:MM" strings and checks the wall clock
    directly against them (nearest of yesterday/today/tomorrow, so it's correct
    across midnight). Use this for the day's fixed-time reminder jobs — a
    catch-up run after an overnight sleep + gateway restart fires them hours off
    their slot (2026-09-08: the whole evening's reminders dumped at 2:27pm).

    The tolerance is asymmetric. A fire *before* the slot is always a misfire —
    you cannot catch up to something in the future — so `grace_min` there is
    tight. A fire *after* the slot may be a genuine gateway recovery, so
    `late_grace_min` (default: same as `grace_min`) can be set wider on the
    reminders where a late delivery still beats none (e.g. a daily check-in).

    A single shared script backing several cron jobs (hydration, meals) passes
    every slot it's scheduled for; keep `grace_min` below half the gap between
    adjacent slots so a real run only ever matches its own slot. `late_grace_min`
    is bounded to that same half-gap internally for the multi-slot case so a
    wide late window can't swallow the next slot.
    """
    if isinstance(slots_hhmm, str):
        slots_hhmm = [slots_hhmm]
    if not slots_hhmm:
        return True, "no slots configured — guard inactive"
    if late_grace_min is None:
        late_grace_min = grace_min

    now = now or datetime.now(get_tz())
    best = None  # (abs_off_min, signed_off_min, "HH:MM"); signed >0 == fired late
    for s in slots_hhmm:
        hh, mm = (int(x) for x in s.split(":"))
        base = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        for cand in (base, base - timedelta(days=1), base + timedelta(days=1)):
            signed = (now - cand).total_seconds() / 60
            if best is None or abs(signed) < best[0]:
                best = (abs(signed), signed, s)
    _, signed, slot = best

    # Don't let a wide late window reach past the midpoint to the next slot.
    if len(slots_hhmm) > 1:
        mins = sorted(int(x) * 60 + int(y)
                      for x, y in (t.split(":") for t in slots_hhmm))
        half_gap = min(b - a for a, b in zip(mins, mins[1:])) / 2
        late_grace_min = min(late_grace_min, half_gap)

    if signed < -grace_min:
        return False, (f"skipped misfire: {now:%a %H:%M} {now.tzname()} is "
                       f"{-signed:.0f} min BEFORE scheduled {slot} "
                       f"(early grace {grace_min} min — catch-up after a restart / tz shift)")
    if signed > late_grace_min:
        return False, (f"skipped misfire: {now:%a %H:%M} {now.tzname()} is "
                       f"{signed:.0f} min AFTER scheduled {slot} "
                       f"(late grace {late_grace_min:.0f} min — stale catch-up run)")
    return True, f"in slot: {now:%a %H:%M} {now.tzname()} (~{signed:+.0f} min from {slot})"


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Exit 0 if inside the slot, 1 if a misfire.")
    p.add_argument("--weekday", type=int, default=None, help="Mon=0 .. Sun=6")
    p.add_argument("--hour-lo", type=int, required=True)
    p.add_argument("--hour-hi", type=int, required=True)
    a = p.parse_args()
    ok, reason = within_slot(a.weekday, a.hour_lo, a.hour_hi)
    print(reason)
    sys.exit(0 if ok else 1)
