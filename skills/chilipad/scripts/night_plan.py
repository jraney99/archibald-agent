#!/usr/bin/env python3
"""
What the bed should do tonight, worked out from the two things the user already has:
his bedroom lights and his alarm. Nothing here is a number he had to pick.

    lights dark   when the bedroom light finishes its fade — READ FROM THE
                  GOVEE CRON JOB, not configured here, so if he ever moves the
                  fade or changes its length the bed moves with it
    wake          the Hatch's calendar-aware wake time, via
                  shared/sync_bedroom_sunrise.read_plan()

Everything else is placed inside that window:

    entry      lights dark − prewarm (30 min)      entry_temp_f, pump on — WARM
    descend    lights dark                          ramp.py walks entry → base
    early off  early_off_time (04:30)               standby — the bed warms to body heat
    off        wake + 30 min                        standby (backstop; usually a no-op)

The user, 2026-10-06: "I love to get into a warm bed and then have it gradually move
down. 95 is way too hot to sleep at." Both ends of that have support — warm skin
at the extremities is the strongest single predictor of how fast sleep arrives
(Kräuchi et al., Am J Physiol 2000), and cooler over the first half of the night
is the finding with usable numbers (+14 min deep sleep in men, +9 min REM in
women; Bioengineering 2024, doi 10.3390/bioengineering11040352). So the bed is
warm on the way in and cool once he is under, rather than one compromise
temperature that is wrong at both moments.

The third phase has moved twice, both times because of what the user reported rather
than what the papers say. It began as the MIDPOINT of the night, lifting the floor
for the whole second half. After two nights: *"the issue was being too hot while
sleeping last night and then being too cold while waking up"* — a midpoint lift
warms him while he is still asleep and leaves nothing for the part he feels, so it
became a lift 45 minutes before the alarm. Then, the same day: *"I think that if it
shut off at 4:30 am that would be fine. It's mostly the falling asleep and the REM
I am concerned about."* So there is no lift at all now. A pump that stops is not
neutral-warm by accident: with no water circulating below skin temperature the bed
settles at his own body heat under the duvet, which is warmer than any setpoint the
pad would hold. The cooling covers falling asleep and the REM-heavy hours, and then
it gets out of the way.

Only ONE temperature is laddered by the trial: `base_temp_f`, the floor he
actually sleeps at. `entry_temp_f` is how warm the bed is to climb into and is
tuned on its own — he set 95°F on the device himself and called it "way too hot to
sleep at", which is precisely the distinction.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sleepme_client import at_least_floor, clamp_f as clamp  # noqa: E402

# The bedroom light's fade-to-dark job. Read for its start time AND its duration:
# dark = start + duration. Named rather than hardcoded to a time so that
# govee-lights stays the owner of when the room goes dark.
BEDROOM_FADE_JOB = "00000000-0000-0000-0000-000000000000"  # govee-winddown-fade-off-bedroom
FALLBACK_LIGHTS_DARK = time(22, 45)   # 22:00 + 2700s, as of 2026-10-06

# A phase has to last long enough to be worth a pump change and an API call.
MIN_PHASE_MINUTES = 30
# Longer than this and the two anchors disagree rather than describe a night: if
# lights-out and the wake time land on the same clock minute, the day-shift below
# yields a 24-hour "night" and the midpoint phase would fire at lunchtime. Refuse
# it instead of scheduling nonsense.
MAX_WINDOW_HOURS = 16


class PlanError(Exception):
    pass


def _cron_job(job_id: str) -> dict | None:
    probe = subprocess.run(["openclaw", "cron", "list", "--all", "--json"],
                           capture_output=True, text=True)
    if probe.returncode != 0:
        return None
    try:
        jobs = json.loads(probe.stdout).get("jobs") or []
    except (ValueError, AttributeError):
        return None
    return next((j for j in jobs if j.get("id") == job_id), None)


def lights_dark_time() -> tuple[time, str]:
    """(when the bedroom goes dark, how that was decided).

    Falls back to FALLBACK_LIGHTS_DARK and says so in the second value — a
    fallback must be visible in the report, never silent, or a changed light
    schedule would quietly stop reaching the bed.
    """
    job = _cron_job(BEDROOM_FADE_JOB)
    if job is None:
        return FALLBACK_LIGHTS_DARK, "fallback (could not read the bedroom fade job)"
    if not job.get("enabled"):
        return FALLBACK_LIGHTS_DARK, "fallback (the bedroom fade job is disabled)"
    payload = job.get("payload") or {}
    sched = payload.get("schedule") or job.get("schedule") or {}
    argv = payload.get("argv") or []
    try:
        minute, hour = sched["expr"].split()[0], sched["expr"].split()[1]
        start = time(int(hour), int(minute))
    except (KeyError, ValueError, IndexError):
        return FALLBACK_LIGHTS_DARK, "fallback (could not parse the fade schedule)"
    seconds = 0
    if "--duration-seconds" in argv:
        try:
            seconds = int(argv[argv.index("--duration-seconds") + 1])
        except (ValueError, IndexError):
            seconds = 0
    dark = (datetime(2000, 1, 1, start.hour, start.minute) + timedelta(seconds=seconds)).time()
    return dark, f"lights fade {start.strftime('%H:%M')} + {seconds // 60} min"


def phase_temps(cfg: dict, base_f: float) -> dict:
    """{phase: temperature} for the night. `base_f` is the sleeping floor.

    entry is absolute (how warm the bed is to get into); the REM lift is relative
    to the floor, so stepping the trial ladder carries it along.
    """
    return {
        "entry": at_least_floor(cfg.get("entry_temp_f", 92), cfg),
        "base": at_least_floor(base_f, cfg),
    }


def build(wake_dt: datetime, base_f: float, cfg: dict,
          dark_t: time | None = None) -> dict:
    """The night's four moments, as datetimes, plus the temperature for each.

    `wake_dt` is tomorrow morning's wake time; lights-dark is placed on the
    evening before it, whatever the calendar date, so this is correct across
    midnight and for an unusual wake time.
    """
    if dark_t is None:
        dark_t, _ = lights_dark_time()

    dark_dt = datetime.combine(wake_dt.date(), dark_t, tzinfo=wake_dt.tzinfo)
    if dark_dt >= wake_dt:
        dark_dt -= timedelta(days=1)

    window = wake_dt - dark_dt
    if window > timedelta(hours=MAX_WINDOW_HOURS):
        raise PlanError(
            f"lights-out {dark_t.strftime('%H:%M')} and wake "
            f"{wake_dt.strftime('%H:%M')} give a {window.total_seconds() / 3600:.1f}h "
            f"night — the two anchors disagree, so nothing was scheduled")

    temps = phase_temps(cfg, base_f)
    prewarm = int(cfg.get("prewarm_minutes", 30))
    off_delay = int(cfg.get("morning_off_delay_minutes", 30))

    # The descent is bounded by the night: it may never still be running when he
    # has to be cool, nor overrun into the midpoint lift.
    descend_minutes = int(cfg.get("descend_minutes", 45))
    descend_minutes = max(MIN_PHASE_MINUTES, min(descend_minutes,
                                                 int(window.total_seconds() // 60) // 2))

    entry_dt = dark_dt - timedelta(minutes=prewarm)
    descend_dt = dark_dt
    off_dt = wake_dt + timedelta(minutes=off_delay)

    # The rating prompt (live 2026-10-07): wake + its own offset, after the
    # morning-off so it never arrives while the bed is still running. It is a
    # question about the night, so it follows the alarm like everything else
    # rather than sitting on a clock time.
    rate_offset = int(cfg.get("rate_prompt", {}).get("morning_offset_minutes", 60))
    rate_dt = wake_dt + timedelta(minutes=rate_offset)

    # The early off is the one clock time the user picked himself (04:30, 2026-10-07). It is placed
    # on the first occurrence after lights-out, and only if the night is long enough to have
    # cooled him first — on a night that starts at 3am, stopping the pump at 4:30 would end the
    # cooling before it did anything, so it is dropped and the caller reports that.
    dropped = []
    descend_ends = descend_dt + timedelta(minutes=descend_minutes)
    early_hhmm = str(cfg.get("early_off_time") or "04:30")
    early_h, early_m = (int(part) for part in early_hhmm.split(":"))
    early_off_dt = datetime.combine(dark_dt.date(), time(early_h, early_m), tzinfo=dark_dt.tzinfo)
    while early_off_dt <= dark_dt:
        early_off_dt += timedelta(days=1)
    if (early_off_dt < descend_ends + timedelta(minutes=MIN_PHASE_MINUTES)
            or early_off_dt > wake_dt - timedelta(minutes=MIN_PHASE_MINUTES)):
        # Also dropped when it would land in the last half hour before the alarm: the wake + 30
        # backstop covers that, and a pump change he is about to wake through is not worth making.
        early_off_dt = None
        dropped.append("early_off")

    return {
        "lights_dark": dark_dt,
        "wake": wake_dt,
        "window_minutes": int(window.total_seconds() // 60),
        "base_f": round(base_f),
        "descend_minutes": descend_minutes,
        "temps": temps,
        "moments": {"entry": entry_dt, "descend": descend_dt, "early_off": early_off_dt,
                    "off": off_dt, "rate": rate_dt},
        "dropped": dropped,
    }


def hhmm(dt: datetime | None) -> str | None:
    return None if dt is None else f"{dt.hour:02d}:{dt.minute:02d}"


def cron_expr(dt: datetime) -> str:
    return f"{dt.minute} {dt.hour} * * *"
