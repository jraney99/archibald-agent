#!/usr/bin/env python3
"""
Nightly job: work out tonight's four moments from the lights and the alarm, and
move the four ChiliPad cron jobs onto them. Prints one JSON line.

    python3 night_schedule.py            # retime, report
    python3 night_schedule.py --dry-run  # print the plan, touch no cron

The two anchors are read live, never configured here:
  - **the alarm**, via `shared/sync_bedroom_sunrise.read_plan()` — the one place
    anything learns the user's calendar-aware wake time, the same read the bedroom
    lights use, so the bed follows the alarm however and whenever it was set,
    including a change made by hand in the Hatch app at bedtime that nothing
    announces. The rule the lights have followed since 2026-09-20 ("If I set the
    alarm to 9:30 am, the lights should adjust to that time").
  - **the lights**, via `night_plan.lights_dark_time()` — read out of the Govee
    fade job's own schedule and duration, so govee-lights stays the owner of when
    the room goes dark and the bed just follows it. The bed is WARM at lights-out
    and starts descending then; see night_plan's docstring for why.

`night_plan.build()` does the placement; see its docstring for why the third
lift sits a fixed distance before the real alarm rather than at a fixed +4h from bedtime.

Must run AFTER tomorrow's alarm has been applied to the device, i.e. after
hatch-restore's nightly_wake_and_workout_sync.py at 9:00pm — scheduled 9:20pm,
just behind the 9:15pm bedroom-light sync. If that 9:00pm job ever moves, this
one has to move with it: reading the Hatch too early reads yesterday's wake time
and reports a confident "ok", which is how the lights read stale data for several
days in 2026-08. 9:20pm is also before the 10:15pm bedtime step, so the plan is
always in place before the first thing it schedules.

A DISABLED ALARM does not skip the morning. `read_plan()` still returns the
alarm's time when it is switched off, and switching the pad off at that time
would cut the cooling while the user is still asleep on the morning he meant to sleep
in. So the morning-off moves to `no_alarm_off_time` (10:00am) instead — late
enough not to wake him, early enough that the pump is not running all day.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, time, timedelta
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(SCRIPTS_DIR.parent.parent / "shared"))

from chilipad_jobs import will_not_run  # noqa: E402
from night_plan import PlanError, build, cron_expr, hhmm, lights_dark_time  # noqa: E402
from sleepme_client import SleepMeError, load_config  # noqa: E402
from sync_bedroom_sunrise import HatchPlanError, read_plan  # noqa: E402
from trial import advance, tonight_base  # noqa: E402

CONTROL_SCRIPT = str(SCRIPTS_DIR / "control.py")
RAMP_SCRIPT = str(SCRIPTS_DIR / "ramp.py")
RATE_SCRIPT = str(SCRIPTS_DIR / "rate_prompt.py")
TZ = "America/Chicago"
# Two or three API calls, each of which may wait out a rate-limit window.
JOB_TIMEOUT_SECONDS = 180

# How late a run may still be and still be worth doing. The bedtime step is the
# one worth catching up on — a late start still cools the bed he is lying in.
LATE_GRACE_MIN = {"entry": 90, "descend": 60, "early_off": 180, "off": 180,
                  # a rating that arrives at lunchtime is still a fair question
                  "rate": 240}
# Headroom over the descent's own duration: ramp.py sleeps in-process for the
# whole thing, so its cron job must not be killed underneath it.
RAMP_TIMEOUT_MARGIN_SECONDS = 600


def argv_for(phase: str, hhmm_str: str, descend_minutes: int = 45) -> list[str]:
    """The WHOLE argv for one job. `cron edit --command-argv` replaces the argv
    outright, so every guard has to be re-listed here with the new time — a flag
    left out is a flag silently dropped."""
    if phase == "rate":
        # Its own guards live in should_ask() (enabled, trial active, away, already
        # rated), so it takes neither --skip-if-away nor the misfire flags, which
        # control.py and ramp.py parse and it does not.
        return ["python3", RATE_SCRIPT]
    if phase == "descend":
        # --detach: the ramp runs in a process of its own. A 45-minute ramp inside the
        # cron worker freezes every other job (2026-10-08 — see shared/detach.py), which
        # is how the user's lights stayed on and his bed stayed off on consecutive evenings.
        base = ["python3", RAMP_SCRIPT, "--duration-seconds", str(descend_minutes * 60),
                "--detach"]
    elif phase in ("off", "early_off"):
        base = ["python3", CONTROL_SCRIPT, "--off"]
    elif phase == "entry":
        # The only step that turns the pump on and spends a request on the water:
        # a dry reservoir means the bed does nothing all night.
        base = ["python3", CONTROL_SCRIPT, "--phase", "entry", "--on", "--check-water"]
    else:
        base = ["python3", CONTROL_SCRIPT, "--phase", phase]
    return base + ["--skip-if-away", "--expected-start", hhmm_str,
                   "--late-grace-min", str(LATE_GRACE_MIN[phase])]


def main() -> None:
    ap = argparse.ArgumentParser(description="Point the ChiliPad's jobs at tonight's real schedule.")
    ap.add_argument("--dry-run", action="store_true", help="Print the plan; change nothing.")
    args = ap.parse_args()

    try:
        cfg = load_config()
    except SleepMeError as e:
        print(json.dumps({"status": "error", "error": str(e)}))
        sys.exit(1)

    try:
        hatch = read_plan()
    except HatchPlanError as e:
        print(json.dumps({"status": "error", "error": f"could not read the Hatch alarm: {e}"}))
        sys.exit(1)
    except Exception as e:  # noqa: BLE001 — a Hatch login failure must name itself, not traceback
        print(json.dumps({"status": "error", "error": f"Hatch login failed: {str(e)[:200]}"}))
        sys.exit(1)

    alarm_on = bool(hatch["enabled"])
    base_f, base_why = tonight_base(cfg)
    dark_t, dark_why = lights_dark_time()

    try:
        plan = build(hatch["wake_dt"], base_f, cfg, dark_t=dark_t)
    except PlanError as e:
        print(json.dumps({"status": "error", "error": str(e)}))
        sys.exit(1)

    moments = dict(plan["moments"])

    # A switched-off alarm: keep the evening exactly as it is and push the
    # morning-off to the backstop, so the pad never stops while he is asleep.
    if not alarm_on:
        raw = str(cfg.get("no_alarm_off_time", "10:00"))
        try:
            hh, mm = (int(x) for x in raw.split(":"))
            backstop = datetime.combine(moments["off"].date(), time(hh, mm),
                                        tzinfo=moments["off"].tzinfo)
            if backstop <= moments["entry"]:
                backstop += timedelta(days=1)
            moments["off"] = backstop
        except (ValueError, TypeError):
            pass   # a bad config value leaves the computed time in place

    jobs = cfg.get("jobs") or {}
    report = {
        "base_f": plan["base_f"],
        "base_from": base_why,
        "temps": plan["temps"],
        "descend_minutes": plan["descend_minutes"],
        "lights_dark": hhmm(plan["lights_dark"]),
        "lights_dark_from": dark_why,
        "wake_local": hhmm(plan["wake"]),
        "alarm_on": alarm_on,
        "window_minutes": plan["window_minutes"],
        "at": {name: hhmm(dt) for name, dt in moments.items()},
    }
    if plan["dropped"]:
        report["dropped"] = plan["dropped"]
    if not alarm_on:
        report["note"] = (f"the Hatch alarm is off, so the pad stays on until "
                          f"{hhmm(moments['off'])} rather than cutting out mid-sleep")

    if args.dry_run:
        report["status"] = "dry-run"
        print(json.dumps(report, indent=2))
        return

    for phase, when in moments.items():
        job_id = jobs.get(phase)
        if when is None:
            continue           # phase dropped for tonight; its job is left where it is
        if not job_id:
            # The four bed phases are mandatory — without them the night does not
            # happen. The rating prompt is an extra: a config without jobs.rate must
            # not take the whole schedule down with it (2026-10-07, when adding the
            # prompt made every retime exit 1 on configs that predate it).
            if phase == "rate":
                report.setdefault("skipped", []).append(
                    "rate: no jobs.rate in chilipad_config.json, so the rating prompt was not retimed")
                continue
            report["status"] = "error"
            report["error"] = f"jobs.{phase} is not set in chilipad_config.json — see SKILL.md's Setup"
            print(json.dumps(report))
            sys.exit(1)
        timeout = JOB_TIMEOUT_SECONDS
        # descend used to need duration + margin because the job WAS the ramp. It now
        # detaches and returns in well under a second, so it takes the ordinary timeout —
        # and a hung launch is caught in 3 minutes instead of 51.
        result = subprocess.run(
            ["openclaw", "cron", "edit", job_id, "--cron", cron_expr(when), "--tz", TZ,
             "--command-argv", json.dumps(argv_for(phase, hhmm(when), plan["descend_minutes"])),
             "--timeout-seconds", str(timeout)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            report["status"] = "error"
            report["stage"] = phase
            report["error"] = (result.stderr or result.stdout).strip()[:300]
            print(json.dumps(report))
            sys.exit(1)

    # Count tonight against the trial ladder, now that it is actually scheduled.
    report["trial"] = advance(cfg)

    # Retiming a job that will never run is not success. `cron edit` returns 0 on a
    # disabled job, so the enable state is a separate question (chilipad_jobs.py).
    problems = will_not_run(jobs.values())
    report["status"] = "ok" if not problems else "will_not_run"
    if problems:
        report["problems"] = problems
    print(json.dumps(report))


if __name__ == "__main__":
    main()
