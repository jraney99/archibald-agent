#!/usr/bin/env python3
"""
Gradual temperature descent: warm when the user gets in, cool by the time he is
asleep. Prints one JSON line.

    python3 ramp.py --duration-seconds 2700
    python3 ramp.py --from 92 --to 72 --duration-seconds 1800
    python3 ramp.py --dry-run

The user, 2026-10-06: "I love to get into a warm bed and then have it gradually move
down. 95 is way too hot to sleep at." That is two different temperatures, and the
evidence backs both ends:

  - **Warm on the way in.** The distal-to-proximal skin gradient predicts sleep
    onset better than core temperature, heart rate, melatonin onset or how sleepy
    someone feels (Kräuchi et al., Am J Physiol 2000) — warm skin at the
    extremities is what triggers the vasodilation that starts sleep. A bed that is
    already cold works against falling asleep, which is the one thing a cooling
    pad is usually blamed for.
  - **Cool once he is under.** Cooler over the first half of the night is the
    finding with usable numbers: +14 min deep sleep in men, +9 min REM in women
    (Bioengineering 2024, doi 10.3390/bioengineering11040352).

So the pad is warm at the entry temperature, then walks down to the sleeping
temperature across the first stretch of the night.

Runs in-process for the whole duration, sleeping between steps, so the cron job
calling it needs a --timeout-seconds well above --duration-seconds (night_schedule
sets that automatically). Steps are STEP_SIZE_F apart, which for a 92→72 descent
over 45 min is ten calls — comfortably inside the account's 10/min ceiling.

A failed step is logged and skipped rather than aborting the descent: every step
sets an ABSOLUTE temperature, so the next step that lands puts the ramp back on
track. A failure never jumps the pad to either extreme; the worst case is that it
sits at an intermediate temperature, and the pre-wake phase corrects it later.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(SCRIPTS_DIR.parent.parent / "shared"))

from current_location import is_away_from_home  # noqa: E402
from data_paths import path as data_path  # noqa: E402
from misfire_guard import near_slot  # noqa: E402
import manual_hold  # noqa: E402
from detach import DETACH_FLAG, relaunch_detached  # noqa: E402
from sleepme_client import (  # noqa: E402
    at_least_floor,  # noqa: E402
    SleepMeClient, SleepMeError, clamp_f, load_config, resolve_device,
)
from trial import tonight_base  # noqa: E402

MISFIRE_EARLY_GRACE_MIN = 20
# A descent that starts an hour late still ends up cool for most of the night.
# Later than that and he is already asleep in a warm bed; leave it.
DEFAULT_LATE_GRACE_MIN = 60

# How big a stride to take. 2°F is below what anyone notices as a step but keeps
# the call count low: a 20°F descent is ten calls however long it is spread over.
STEP_SIZE_F = 2
# Spacing between back-to-back calls when the computed interval is tiny.
MIN_STEP_SECONDS = 20


def plan_steps(start_f: int, end_f: int, duration_seconds: int, cfg: dict | None = None) -> list:
    """[(temperature, seconds to wait before setting it)] — the whole descent.

    The first step is applied immediately (0 wait) so the pad leaves the entry
    temperature the moment the ramp fires; the last step lands at `end_f`.
    """
    # Never plan a step below the user's floor: set_temperature would refuse it, and a refused
    # step mid-descent would leave the bed at whatever the last good step was.
    start_f, end_f = at_least_floor(start_f, cfg), at_least_floor(end_f, cfg)
    if start_f == end_f:
        return [(end_f, 0)]
    span = abs(start_f - end_f)
    direction = -1 if end_f < start_f else 1
    count = max(1, round(span / STEP_SIZE_F))
    temps = [start_f + direction * round(span * (i + 1) / count) for i in range(count)]
    temps[-1] = end_f                      # never overshoot through rounding
    interval = max(MIN_STEP_SECONDS, duration_seconds / count) if count else 0
    return [(t, 0 if i == 0 else interval) for i, t in enumerate(temps)]


RAMP_LOG = data_path("ramp_detached.log")


def main() -> None:
    ap = argparse.ArgumentParser(description="Walk the ChiliPad down from warm to sleeping temperature.")
    ap.add_argument("--device", help="Nickname (default: the one in chilipad_config.json).")
    ap.add_argument("--from", dest="start", type=float,
                    help="Starting temperature (default: entry_temp_f from config).")
    ap.add_argument("--to", dest="end", type=float,
                    help="Ending temperature (default: tonight's base, i.e. the trial's rung).")
    ap.add_argument("--duration-seconds", type=int, default=2700,
                    help="How long the descent takes (default 2700 = 45 min).")
    ap.add_argument("--dry-run", action="store_true", help="Print the steps; set nothing.")
    ap.add_argument("--skip-if-away", action="store_true",
                    help="No-op (exit 0) when is_away_from_home() is true.")
    ap.add_argument("--expected-start", metavar="HH:MM",
                    help="This job's own scheduled time. No-op on a gateway catch-up run.")
    ap.add_argument("--late-grace-min", type=int, default=DEFAULT_LATE_GRACE_MIN,
                    help=f"With --expected-start (default {DEFAULT_LATE_GRACE_MIN}).")
    ap.add_argument(DETACH_FLAG, action="store_true",
                    help="Run the descent in a process of its own and return at once. "
                         "The cron job uses this: a 45-minute ramp inside the cron "
                         "worker freezes every other job (2026-10-08, see shared/detach.py).")
    args = ap.parse_args()

    if args.expected_start:
        ok, reason = near_slot(args.expected_start, grace_min=MISFIRE_EARLY_GRACE_MIN,
                               late_grace_min=args.late_grace_min)
        if not ok:
            print(json.dumps({"skipped": reason}))
            return

    if args.skip_if_away and is_away_from_home():
        print(json.dumps({"skipped": "away from home"}))
        return

    if args.detach:
        print(json.dumps(relaunch_detached(RAMP_LOG)))
        return

    try:
        cfg = load_config()
        start = args.start if args.start is not None else cfg.get("entry_temp_f", 92)
        if args.end is not None:
            end, why = args.end, "--to"
        else:
            end, why = tonight_base(cfg)
        steps = plan_steps(int(start), int(end), args.duration_seconds, cfg)

        if args.dry_run:
            print(json.dumps({
                "from": clamp_f(start), "to": clamp_f(end), "to_from": why,
                "duration_minutes": args.duration_seconds // 60,
                "steps": [{"f": t, "after_seconds": round(w)} for t, w in steps],
            }, indent=2))
            return

        device_id, nickname = resolve_device(args.device)
        client = SleepMeClient()
    except SleepMeError as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)

    applied, failures = [], []
    stopped = None
    for temp, wait in steps:
        if wait:
            time.sleep(wait)
        # The user changed the bed himself while this was descending (2026-10-06: "I'm too cold
        # rn" at 23:19, with the ramp four minutes from pulling 76°F down to 74°F). Leave the
        # bed where he put it; this is a normal ending, not a failure.
        live = manual_hold.hold()
        if live:
            stopped = live.get("set")
            break
        try:
            client.set_temperature(device_id, "set_temperature_f", temp)
            applied.append(temp)
        except SleepMeError as e:
            # Skip, don't abort: the next step sets an absolute temperature and
            # puts the descent back on track.
            failures.append({"f": temp, "error": str(e)[:120]})

    out = {
        "device": nickname,
        "from": clamp_f(start),
        "to": clamp_f(end),
        "to_from": why,
        "duration_minutes": args.duration_seconds // 60,
        "applied": applied,
    }
    if failures:
        out["failed_steps"] = failures
    if stopped:
        out["stopped"] = f"Master Wayne set the bed to {stopped} — descent abandoned"
    print(json.dumps(out))
    if failures and not applied:
        sys.exit(1)     # nothing landed at all — let the cron failure alert fire


if __name__ == "__main__":
    main()
