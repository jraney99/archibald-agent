#!/usr/bin/env python3
"""
Nightly job: move the Nest's two setback jobs onto tonight's real times. Prints
one JSON line.

    python3 night_schedule.py            # retime, report
    python3 night_schedule.py --dry-run  # print the plan, touch no cron

THE SAME TWO ANCHORS THE BED USES, read live, never configured:
  - **the lights** — `chilipad/scripts/night_plan.lights_dark_time()`, which reads
    the Govee bedroom fade job's own schedule and duration. Imported rather than
    copied so there is exactly ONE definition of "when the room goes dark". That
    makes this skill depend on the chilipad skill's folder being present, which is
    the deliberate trade: two definitions that could drift apart would be worse,
    and `shared/sync_bedroom_sunrise.py` already imports across skills the same way.
  - **the alarm** — `shared/sync_bedroom_sunrise.read_plan()`, the one place
    anything learns the user's calendar-aware wake time.

    house down to sleep_temp_f   at lights-dark
    house back to day_temp_f     at wake − morning_lead_minutes

The morning lead is the point of it: a house takes 30-45 minutes to move a few
degrees, so restoring the temperature AT the wake time means he wakes in a cold
house and it warms up while he is in the shower. Leading it means the house is
already right when the alarm goes.

**This is why the bed and the house stop fighting.** They now cool and warm off the
same lights-dark and the same alarm, so the ChiliPad is not pulling heat out of
him while the furnace runs, or vice versa.

Must run AFTER tomorrow's alarm is on the device (hatch-restore's
nightly_wake_and_workout_sync.py, 9:00pm) and before the first thing it schedules.
9:25pm — just behind the chilipad retime at 9:20pm. Reading the Hatch earlier
reads yesterday's wake time and reports a confident "ok", which is how the lights
read stale data for several days in 2026-08.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(SCRIPTS_DIR.parent.parent / "shared"))
sys.path.insert(0, str(SCRIPTS_DIR.parent.parent / "chilipad" / "scripts"))

from night_plan import lights_dark_time  # noqa: E402  (chilipad owns this)
from nest_client import NestError, load_config  # noqa: E402
from sync_bedroom_sunrise import HatchPlanError, read_plan  # noqa: E402

CONTROL_SCRIPT = str(SCRIPTS_DIR / "control.py")
TZ = "America/Chicago"
JOB_TIMEOUT_SECONDS = 180

LATE_GRACE_MIN = {"night": 120, "morning": 60}
# A disabled alarm means a lie-in: restore the house late rather than at a wake
# time he is not keeping, so the heat does not come up while he is still asleep.
NO_ALARM_MORNING = "10:00"


def argv_for(phase: str, hhmm: str) -> list:
    """The WHOLE argv for one job. `cron edit --command-argv` replaces the argv
    outright, so every guard has to be re-listed here with the new time."""
    preset = "sleep" if phase == "night" else "day"
    return ["python3", CONTROL_SCRIPT, "--preset", preset,
            "--skip-if-away", "--expected-start", hhmm,
            "--late-grace-min", str(LATE_GRACE_MIN[phase])]


def hhmm(dt: datetime) -> str:
    return f"{dt.hour:02d}:{dt.minute:02d}"


def cron_expr(dt: datetime) -> str:
    return f"{dt.minute} {dt.hour} * * *"


def main() -> None:
    ap = argparse.ArgumentParser(description="Point the Nest's setback jobs at tonight's real times.")
    ap.add_argument("--dry-run", action="store_true", help="Print the plan; change nothing.")
    args = ap.parse_args()

    try:
        cfg = load_config()
    except NestError as e:
        print(json.dumps({"status": "error", "error": str(e)}))
        sys.exit(1)

    try:
        hatch = read_plan()
    except HatchPlanError as e:
        print(json.dumps({"status": "error", "error": f"could not read the Hatch alarm: {e}"}))
        sys.exit(1)
    except Exception as e:  # noqa: BLE001 — a login failure must name itself, not traceback
        print(json.dumps({"status": "error", "error": f"Hatch login failed: {str(e)[:200]}"}))
        sys.exit(1)

    wake_dt = hatch["wake_dt"]
    alarm_on = bool(hatch["enabled"])
    dark_t, dark_why = lights_dark_time()

    dark_dt = datetime.combine(wake_dt.date(), dark_t, tzinfo=wake_dt.tzinfo)
    if dark_dt >= wake_dt:
        dark_dt -= timedelta(days=1)

    lead = int(cfg.get("morning_lead_minutes", 45))
    morning_dt = wake_dt - timedelta(minutes=lead)
    if not alarm_on:
        hh, mm = (int(x) for x in str(NO_ALARM_MORNING).split(":"))
        morning_dt = datetime.combine(wake_dt.date(), wake_dt.time().replace(hour=hh, minute=mm),
                                      tzinfo=wake_dt.tzinfo)

    # The lead must not reach back past bedtime, or the "morning" job would warm
    # the house up in the middle of the night.
    if morning_dt <= dark_dt:
        morning_dt = dark_dt + timedelta(hours=1)

    moments = {"night": dark_dt, "morning": morning_dt}
    jobs = cfg.get("jobs") or {}
    report = {
        "sleep_f": cfg.get("sleep_temp_f"),
        "day_f": cfg.get("day_temp_f"),
        "lights_dark": hhmm(dark_dt),
        "lights_dark_from": dark_why,
        "wake_local": hhmm(wake_dt),
        "alarm_on": alarm_on,
        "morning_lead_minutes": lead,
        "at": {k: hhmm(v) for k, v in moments.items()},
    }
    if not alarm_on:
        report["note"] = (f"the Hatch alarm is off, so the house comes back at "
                          f"{NO_ALARM_MORNING} rather than at a wake time he is not keeping")

    if args.dry_run:
        report["status"] = "dry-run"
        print(json.dumps(report, indent=2))
        return

    for phase, when in moments.items():
        job_id = jobs.get(phase)
        if not job_id:
            report["status"] = "error"
            report["error"] = f"jobs.{phase} is not set in nest_config.json — see SKILL.md's Setup"
            print(json.dumps(report))
            sys.exit(1)
        result = subprocess.run(
            ["openclaw", "cron", "edit", job_id, "--cron", cron_expr(when), "--tz", TZ,
             "--command-argv", json.dumps(argv_for(phase, hhmm(when))),
             "--timeout-seconds", str(JOB_TIMEOUT_SECONDS)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            report["status"] = "error"
            report["stage"] = phase
            report["error"] = (result.stderr or result.stdout).strip()[:300]
            print(json.dumps(report))
            sys.exit(1)

    # Retiming a job that will never run is not success — `cron edit` returns 0 on a
    # disabled job, which cost the user two mornings of bedroom sunrise in 2026-10.
    sys.path.insert(0, str(SCRIPTS_DIR.parent.parent / "chilipad" / "scripts"))
    from chilipad_jobs import will_not_run  # noqa: E402
    problems = will_not_run(jobs.values())
    report["status"] = "ok" if not problems else "will_not_run"
    if problems:
        report["problems"] = problems
    print(json.dumps(report))


if __name__ == "__main__":
    main()
