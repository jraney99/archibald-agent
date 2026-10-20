#!/usr/bin/env python3
"""Answer "will my bedroom light come on tomorrow?" — and change it.

WHY (2026-10-14): The user has asked to "turn off my sunrise" six times in six weeks
and could never tell afterwards what state it was in. Each time the agent
improvised — sometimes `control.py --light bedroom --off` (switches the bulb off
for a second, changes nothing about tomorrow), sometimes reading the cron — and
then reported "Bedroom sunrise — off", which reads as a permanent schedule
change. On 2026-10-14 that cost an hour of back-and-forth, and even then the
honest answer ("tomorrow is already handled, because your alarm is off") was
only found by reading sunrise_ramp.py's source.

The behaviour was never really broken. What was missing is a single place that
READS the real state and says what will actually happen. Two things decide it,
and no message ever combined them:

  1. the cron job            — is it scheduled at all?
  2. the Hatch alarm         — sunrise_ramp.py runs with --follow-hatch, and
                               skips itself entirely when the alarm is off
                               ("Hatch alarm is off — no sunrise")

So "my alarm is off" already means "no sunrise tomorrow", automatically, and
turning the cron off as well is usually not what the user wants: nothing re-enables
it for him (sync_bedroom_sunrise deliberately never re-enables), so it would
stay dark until he noticed and asked.

    python3 sunrise.py status      # what will actually happen tomorrow
    python3 sunrise.py off         # stop a ramp running now; report tomorrow honestly
    python3 sunrise.py off --tomorrow-too   # ALSO disable the job (stays off until `on`)
    python3 sunrise.py on          # schedule it again
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent / "shared"))

JOB_NAME = "govee-bedroom-sunrise"
OPENCLAW = "/opt/homebrew/bin/openclaw"
RAMP = HERE / "sunrise_ramp.py"


def _job() -> dict:
    r = subprocess.run([OPENCLAW, "cron", "list", "--all", "--json"],
                       capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError("could not read the cron list: " + (r.stderr or r.stdout)[:160])
    for j in json.loads(r.stdout).get("jobs", []):
        if j.get("name") == JOB_NAME:
            return j
    raise RuntimeError(f"{JOB_NAME} is not in the cron list")


def _alarm() -> dict:
    """(enabled, wake 'HH:MM' or None). Never raises — an unreadable Hatch is a
    real answer ("can't tell"), not a crash."""
    try:
        from sync_bedroom_sunrise import read_plan
        plan = read_plan()
        return {"readable": True, "enabled": bool(plan["enabled"]),
                "wake": plan["wake_dt"].strftime("%H:%M"),
                "ramp_start": plan["ramp_start_dt"].strftime("%H:%M")}
    except Exception as e:  # noqa: BLE001
        return {"readable": False, "why": str(e)[:160]}


def state() -> dict:
    job = _job()
    alarm = _alarm()
    expr = job.get("schedule", {}).get("expr") or ""
    at = ""
    if expr:
        parts = expr.split()
        if len(parts) >= 2:
            at = f"{int(parts[1]):02d}:{int(parts[0]):02d}"
    scheduled = bool(job.get("enabled"))
    if not scheduled:
        will, because = False, "the sunrise job is switched off"
    elif not alarm["readable"]:
        will, because = None, f"the Hatch could not be read ({alarm['why']})"
    elif not alarm["enabled"]:
        # sunrise_ramp.py --follow-hatch returns {"skipped": "Hatch alarm is off
        # — no sunrise"} before touching the light.
        will, because = False, "your Hatch alarm is off, so the ramp skips itself"
    else:
        will, because = True, f"your alarm is set for {alarm['wake']}"
    return {"will_run_tomorrow": will, "because": because,
            "scheduled_at": at, "job_enabled": scheduled, "alarm": alarm}


def sentence(s: dict) -> str:
    """What the user actually wants to know, in one line he can act on."""
    if s["will_run_tomorrow"] is True:
        return (f"Tomorrow: your bedroom light ramps up at {s['scheduled_at']} — "
                f"{s['because']}.")
    if s["will_run_tomorrow"] is False:
        tail = ("Turn the alarm back on and the sunrise returns by itself."
                if s["job_enabled"] else
                "It stays off until you ask for it back (`sunrise.py on`).")
        return f"Tomorrow: no sunrise — {s['because']}. {tail}"
    return f"Tomorrow: can't say — {s['because']}. Nothing was changed."


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=("status", "off", "on"))
    ap.add_argument("--tomorrow-too", action="store_true",
                    help="with `off`: also disable the job, so it stays off until `on`")
    args = ap.parse_args(argv)

    stopped = None
    if args.action == "off":
        # Stops a ramp that is running RIGHT NOW. Harmless when none is.
        r = subprocess.run([sys.executable, "-W", "ignore", str(RAMP),
                            "--light", "bedroom", "--stop"],
                           capture_output=True, text=True, timeout=60)
        stopped = r.returncode == 0
        if args.tomorrow_too:
            subprocess.run([OPENCLAW, "cron", "disable", _job()["id"]],
                           capture_output=True, text=True, timeout=30)
    elif args.action == "on":
        subprocess.run([OPENCLAW, "cron", "enable", _job()["id"]],
                       capture_output=True, text=True, timeout=30)

    # Always read the world back. The whole point: report what IS, never what was
    # attempted (2026-10-14: "Bedroom sunrise — off" was a claim about an action,
    # and the action had not changed tomorrow at all).
    s = state()
    out = {"action": args.action, "said": sentence(s), **s}
    if stopped is not None:
        out["stopped_a_running_ramp"] = stopped
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
